"""
Orchestrates bulk coordinate upload for Geocaching.com.
Supports two modes:
1. OVERWRITE_ALL: Uploads coordinates for every cache in the list.
2. UNMODIFIED_ONLY: Skips caches that already have user-modified coordinates on the website.
Provides progress reporting, live ETA calculation, and safety pacing.
"""

import time
from typing import List, Callable, Optional, Dict, Any
from core.gpx_parser import WaypointItem, is_virtual_or_earth, is_cache_filtered
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
        upload_coords: bool = True,
        upload_notes: bool = True,
        on_progress: Optional[Callable[[int, int, float, Optional[float], WaypointItem], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        source_file_path: str = "",
        resume: bool = False,
        omit_virtual: bool = False,
        omit_earth: bool = False,
        omit_events: bool = False,
        omit_webcam: bool = False,
        omit_virtual_and_earth: bool = False
    ) -> Dict[str, Any]:
        """
        Execute the upload process.
        :param items: List of WaypointItem to process.
        :param mode: OVERWRITE_ALL or UNMODIFIED_ONLY.
        :param upload_coords: Whether to upload geographic coordinates.
        :param upload_notes: Whether to upload personal cache notes.
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

        action_parts = []
        if upload_coords:
            action_parts.append(f"coordinates ({mode})")
        if upload_notes:
            action_parts.append("personal notes")
        action_desc = " & ".join(action_parts) or "nothing selected"

        if resume and already_completed_set:
            log(f"Resuming upload: {len(already_completed_set)}/{total} caches already completed in prior run ({action_desc}).", "info")
        else:
            log(f"Starting upload for {total} caches ({action_desc})...", "info")

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

            # Granular cache type filter
            should_filter = (omit_virtual or omit_earth or omit_events or omit_webcam or omit_virtual_and_earth)
            if should_filter:
                ov = omit_virtual or omit_virtual_and_earth
                oe = omit_earth or omit_virtual_and_earth
                oev = omit_events
                ow = omit_webcam or omit_virtual_and_earth
                if item.cache_type:
                    is_filtered = is_cache_filtered(item.cache_type, omit_virtual=ov, omit_earth=oe, omit_events=oev, omit_webcam=ow)
                else:
                    is_filtered = self.client.is_cache_filtered(item.gccode, omit_virtual=ov, omit_earth=oe, omit_events=oev, omit_webcam=ow)

                if is_filtered:
                    log(f"{prefix} ⏭️ Skipped: Cache type filtered out.", "info")
                    skipped_count += 1
                    if self.checkpoint_manager:
                        self.checkpoint_manager.record_coord_item(item.gccode, success=True)
                    if on_progress:
                        percent = (idx / total) * 100
                        on_progress(idx, total, percent, None, item)
                    continue

            # Query web status if UNMODIFIED_ONLY mode is active
            user_status = None
            if mode == self.MODE_UNMODIFIED_ONLY and (upload_coords or (upload_notes and getattr(item, "note", ""))):
                user_status = self.client.get_cache_user_status(item.gccode)

            # 1. Coordinate upload handling (if enabled)
            coords_status = None  # None = disabled, "SKIPPED", "SUCCESS", "FAILED"
            coords_msg = ""
            if upload_coords:
                if mode == self.MODE_UNMODIFIED_ONLY and user_status and user_status.get("is_coords_modified") is True:
                    coords_status = "SKIPPED"
                    coords_msg = "Coordinates already modified on web"

                if coords_status is None:
                    c_succ, c_msg = self.client.upload_coordinates(item.gccode, item.lat, item.lon)
                    if c_succ:
                        coords_status = "SUCCESS"
                        coords_msg = c_msg
                    else:
                        coords_status = "FAILED"
                        coords_msg = c_msg

            # 2. Personal note upload handling (if enabled)
            note_status = None  # None = disabled, "SKIPPED_EMPTY", "SKIPPED_EXISTS", "SUCCESS", "FAILED"
            note_msg = ""
            if upload_notes:
                if not getattr(item, "note", ""):
                    note_status = "SKIPPED_EMPTY"
                    note_msg = "No note in GPX"
                elif mode == self.MODE_UNMODIFIED_ONLY and user_status and user_status.get("has_personal_note") is True:
                    note_status = "SKIPPED_EXISTS"
                    note_msg = "Personal note already exists on web"
                else:
                    n_succ, n_msg = self.client.upload_personal_note(item.gccode, item.note)
                    if n_succ:
                        note_status = "SUCCESS"
                        note_msg = n_msg
                    else:
                        note_status = "FAILED"
                        note_msg = n_msg

            # 3. Overall item evaluation
            has_failed = (coords_status == "FAILED" or note_status == "FAILED")
            all_skipped = (
                (coords_status in (None, "SKIPPED"))
                and (note_status in (None, "SKIPPED_EMPTY", "SKIPPED_EXISTS"))
                and coords_status != "SUCCESS"
                and note_status != "SUCCESS"
            )

            # Build readable log message
            log_parts = []
            if upload_coords:
                if coords_status == "SUCCESS":
                    log_parts.append(f"✅ Coords updated ({item.lat:.5f}, {item.lon:.5f})")
                elif coords_status == "SKIPPED":
                    log_parts.append("⏭️ Coords already modified")
                elif coords_status == "FAILED":
                    log_parts.append(f"❌ Coords failed: {coords_msg}")

            if upload_notes:
                if note_status == "SUCCESS":
                    log_parts.append("📝 Note uploaded")
                elif note_status == "SKIPPED_EXISTS":
                    log_parts.append("⏭️ Note already exists on web")
                elif note_status == "SKIPPED_EMPTY":
                    log_parts.append("(no note in GPX)")
                elif note_status == "FAILED":
                    log_parts.append(f"❌ Note failed: {note_msg}")

            combined_msg = f"{prefix} " + " | ".join(log_parts)

            if has_failed:
                failed_count += 1
                fail_reasons = []
                if coords_status == "FAILED":
                    fail_reasons.append(f"Coords: {coords_msg}")
                if note_status == "FAILED":
                    fail_reasons.append(f"Note: {note_msg}")
                reason_str = "; ".join(fail_reasons)
                failed_items.append({"gccode": item.gccode, "reason": reason_str})
                if self.checkpoint_manager:
                    self.checkpoint_manager.record_coord_item(item.gccode, success=False, reason=reason_str)
                log(combined_msg, "error")
            elif all_skipped:
                skipped_count += 1
                if self.checkpoint_manager:
                    self.checkpoint_manager.record_coord_item(item.gccode, success=True)
                log(combined_msg, "warning" if (coords_status == "SKIPPED" or note_status == "SKIPPED_EXISTS") else "info")
            else:
                success_count += 1
                if self.checkpoint_manager:
                    self.checkpoint_manager.record_coord_item(item.gccode, success=True)
                log(combined_msg, "success")

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
