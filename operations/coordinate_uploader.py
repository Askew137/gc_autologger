"""
Orchestrates bulk coordinate upload for Geocaching.com.
Supports two modes:
1. OVERWRITE_ALL: Uploads coordinates for every cache in the list.
2. UNMODIFIED_ONLY: Skips caches that already have user-modified coordinates on the website.
Provides progress reporting, live ETA calculation, and safety pacing.
"""

import time
from typing import List, Callable, Optional, Dict, Any
from core.gpx_parser import WaypointItem, is_virtual_or_earth
from core.client import GeocachingClient
from core.safety import SafetyManager
from core.checkpoint import RunCheckpointManager


class CoordinateUploader:
    """Manages the execution of coordinate upload batches with persistent checkpoint recovery."""

    MODE_OVERWRITE_ALL = "OVERWRITE_ALL"
    MODE_UNMODIFIED_ONLY = "UNMODIFIED_ONLY"

    def __init__(
        self,
        client: GeocachingClient,
        safety: SafetyManager,
        checkpoint_manager: Optional[RunCheckpointManager] = None
    ):
        self.client = client
        self.safety = safety
        self.checkpoint_manager = checkpoint_manager

    def run(
        self,
        items: List[WaypointItem],
        mode: str = MODE_OVERWRITE_ALL,
        on_progress: Optional[Callable[[int, int, float, Optional[float], WaypointItem], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        source_file_path: str = "",
        resume: bool = False,
        omit_virtual_and_earth: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the upload process.
        :param items: List of WaypointItem to process.
        :param mode: OVERWRITE_ALL or UNMODIFIED_ONLY.
        :param on_progress: callback(current, total, percent, eta_seconds, item)
        :param on_log: callback(message, level='info'|'success'|'warning'|'error')
        :return: Summary dictionary with results.
        """
        self.safety.reset()
        total = len(items)
        success_count = 0
        skipped_count = 0
        failed_count = 0
        failed_items: List[Dict[str, str]] = []

        start_time = time.time()
        processed_times: List[float] = []

        # Checkpoint integration
        already_completed_set = set()
        if self.checkpoint_manager:
            if resume:
                active_state = self.checkpoint_manager.load_active_coords_run()
                if active_state:
                    already_completed_set = set(active_state.completed_gccodes)
            else:
                file_p = source_file_path or (items[0].source_file if items else "")
                self.checkpoint_manager.start_coords_run(
                    file_path=file_p,
                    mode=mode,
                    total=total
                )

        def log(msg: str, level: str = "info"):
            if self.checkpoint_manager:
                self.checkpoint_manager.append_log(msg)
            if on_log:
                on_log(msg, level)
            else:
                print(f"[{level.upper()}] {msg}")

        if resume and already_completed_set:
            log(f"Resuming coordinate upload: {len(already_completed_set)}/{total} caches already completed in prior run.", "info")
        else:
            log(f"Starting coordinate upload for {total} caches (Mode: {mode})...", "info")

        # Verify authentication
        if not self.client.ensure_valid_session():
            log("Session is not valid. Please log in before starting.", "error")
            return {
                "total": total,
                "success": 0,
                "skipped": 0,
                "failed": total,
                "failed_items": [{"gccode": it.gccode, "reason": "Not authenticated"} for it in items]
            }

        # Warm up token
        sample_code = items[0].gccode if items else None
        token = self.client.get_verification_token(sample_gccode=sample_code)
        if not token:
            log("Failed to acquire __RequestVerificationToken from server.", "error")
            return {
                "total": total,
                "success": 0,
                "skipped": 0,
                "failed": total,
                "failed_items": [{"gccode": it.gccode, "reason": "No verification token"} for it in items]
            }

        for idx, item in enumerate(items, 1):
            if self.safety.is_cancelled:
                log(f"Upload cancelled by user at item {idx}/{total}.", "warning")
                break

            item_start = time.time()
            prefix = f"[{idx}/{total}] {item.gccode}"

            # If already done in prior session (during resume)
            if item.gccode in already_completed_set:
                success_count += 1
                log(f"{prefix} ⏩ Already completed in prior run, skipping.", "info")
                if on_progress:
                    percent = (idx / total) * 100
                    on_progress(idx, total, percent, None, item)
                continue

            # Omit virtual and earth caches filter
            if omit_virtual_and_earth:
                is_voe = False
                if is_virtual_or_earth(item.cache_type):
                    is_voe = True
                elif not item.cache_type:
                    is_voe = self.client.is_virtual_or_earth_cache(item.gccode)

                if is_voe:
                    log(f"{prefix} ⏭️ Skipped: Virtual / EarthCache filtered out.", "info")
                    skipped_count += 1
                    if self.checkpoint_manager:
                        self.checkpoint_manager.record_coord_item(item.gccode, success=True)
                    if on_progress:
                        percent = (idx / total) * 100
                        on_progress(idx, total, percent, None, item)
                    continue

            # Mode check: Unmodified only
            if mode == self.MODE_UNMODIFIED_ONLY:
                is_modified = self.client.has_modified_coordinates(item.gccode)
                if is_modified is True:
                    log(f"{prefix} ⏭️ Skipped: Coordinates are already modified on web.", "warning")
                    skipped_count += 1
                    if self.checkpoint_manager:
                        self.checkpoint_manager.record_coord_item(item.gccode, success=True)
                    # Progress notification
                    if on_progress:
                        percent = (idx / total) * 100
                        on_progress(idx, total, percent, None, item)
                    continue

            # Upload coordinates
            success, message = self.client.upload_coordinates(item.gccode, item.lat, item.lon)

            if success:
                success_count += 1
                if self.checkpoint_manager:
                    self.checkpoint_manager.record_coord_item(item.gccode, success=True)
                log(f"{prefix} ✅ Success: {item.lat:.5f}, {item.lon:.5f} ({message})", "success")
            else:
                failed_count += 1
                failed_items.append({"gccode": item.gccode, "reason": message})
                if self.checkpoint_manager:
                    self.checkpoint_manager.record_coord_item(item.gccode, success=False, reason=message)
                log(f"{prefix} ❌ Failed: {message}", "error")

            # Progress & ETA calculation
            elapsed_item = time.time() - item_start
            processed_times.append(elapsed_item)
            avg_time = sum(processed_times[-10:]) / len(processed_times[-10:])
            remaining_items = total - idx
            eta = remaining_items * (avg_time + ((self.safety.min_delay + self.safety.max_delay) / 2))

            percent = (idx / total) * 100
            if on_progress:
                on_progress(idx, total, percent, eta, item)

            # Safety wait (jitter + breathers)
            if idx < total:
                if not self.safety.wait_before_next(idx, total, log_callback=lambda m: log(m, "info")):
                    log("Upload interrupted.", "warning")
                    break

        # Checkpoint cleanup / archive
        if self.checkpoint_manager:
            if self.safety.is_cancelled:
                self.checkpoint_manager.finish_coords_run(status="cancelled")
            else:
                self.checkpoint_manager.finish_coords_run(status="completed")

        total_elapsed = time.time() - start_time
        summary = {
            "total": total,
            "success": success_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "failed_items": failed_items,
            "elapsed_seconds": total_elapsed
        }

        log("=" * 50, "info")
        log(f"Upload Complete! Success: {success_count} | Skipped: {skipped_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "info")
        if failed_items:
            log(f"Failed codes: {', '.join(f['gccode'] for f in failed_items[:10])}", "warning")
        log("=" * 50, "info")

        return summary
