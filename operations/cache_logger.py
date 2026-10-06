"""
Orchestrates bulk logging and ignore operations for Geocaching.com.
Supports Found, DNF, Note, Needs Maintenance, Needs Archive, and Ignore.
Uses direct tRPC batch communication with safety pacing and live progress reporting.
"""

import time
from typing import List, Callable, Optional, Dict, Any
from core.client import GeocachingClient
from core.safety import SafetyManager


LOG_TYPE_MAP = {
    "FOUND": 2,
    "DNF": 3,
    "NOTE": 4,
    "NEEDS_OWNER_ATTENTION": 45,
    "NEEDS_REVIEWER_ATTENTION": 7
}

LOG_TYPE_NAMES = {
    "FOUND": "Found it",
    "DNF": "Didn't find it",
    "NOTE": "Write note",
    "NEEDS_OWNER_ATTENTION": "Needs maintenance",
    "NEEDS_REVIEWER_ATTENTION": "Needs archive"
}


class CacheLogger:
    """Manages bulk logging and ignore operations."""

    def __init__(self, client: GeocachingClient, safety: SafetyManager):
        self.client = client
        self.safety = safety

    def run_log(
        self,
        gc_codes: List[str],
        log_type_key: str,
        date_str: str,
        log_text: str,
        skip_already_found: bool = True,
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Log multiple caches in bulk.
        :param gc_codes: List of GC codes (e.g. ['GC12345', 'GC67890'])
        :param log_type_key: 'FOUND', 'DNF', 'NOTE', 'NEEDS_OWNER_ATTENTION', 'NEEDS_REVIEWER_ATTENTION'
        :param date_str: Date string 'YYYY-MM-DD'
        :param log_text: Text content of the log
        :param skip_already_found: If True and log_type is 'Found it', skips caches already found
        """
        self.safety.reset()
        total = len(gc_codes)
        success_count = 0
        failed_count = 0
        failed_items: List[Dict[str, str]] = []

        log_type_id = LOG_TYPE_MAP.get(log_type_key, 2)
        display_type = LOG_TYPE_NAMES.get(log_type_key, log_type_key)

        def log(msg: str, level: str = "info"):
            if on_log:
                on_log(msg, level)
            else:
                print(f"[{level.upper()}] {msg}")

        # Format date as ISO string (e.g. "2026-10-06T12:00:00.000Z")
        iso_date = f"{date_str}T12:00:00.000Z"

        log(f"Starting bulk logging: {total} caches as '{display_type}' for date {date_str}...", "info")

        if not self.client.ensure_valid_session():
            log("Session is invalid. Please log in first.", "error")
            return {"total": total, "success": 0, "failed": total, "failed_items": []}

        start_time = time.time()
        processed_times: List[float] = []

        for idx, gc_code in enumerate(gc_codes, 1):
            if self.safety.is_cancelled:
                log(f"Logging cancelled by user at item {idx}/{total}.", "warning")
                break

            item_start = time.time()
            prefix = f"[{idx}/{total}] {gc_code}"
            clean_code = gc_code.strip().upper()

            # Check if cache is already found when logging as "Found it"
            if log_type_id == 2 and skip_already_found:
                is_found, found_date = self.client.is_cache_found(clean_code)
                if is_found:
                    success_count += 1
                    date_info = f" on {found_date}" if found_date else ""
                    log(f"{prefix} ⏭️ Already found{date_info} (skipped duplicate)", "info")
                    percent = (idx / total) * 100
                    if on_progress:
                        on_progress(idx, total, percent, None, clean_code)
                    continue

            success, message = self.client.create_log(clean_code, log_type_id, iso_date, log_text)

            if success:
                success_count += 1
                log(f"{prefix} ✅ Logged as {display_type}", "success")
            else:
                failed_count += 1
                failed_items.append({"gccode": gc_code, "reason": message})
                log(f"{prefix} ❌ Failed: {message}", "error")

            elapsed_item = time.time() - item_start
            processed_times.append(elapsed_item)
            avg_time = sum(processed_times[-10:]) / len(processed_times[-10:])
            remaining_items = total - idx
            eta = remaining_items * (avg_time + ((self.safety.min_delay + self.safety.max_delay) / 2))

            percent = (idx / total) * 100
            if on_progress:
                on_progress(idx, total, percent, eta, gc_code)

            if idx < total:
                if not self.safety.wait_before_next(idx, total, log_callback=lambda m: log(m, "info")):
                    log("Logging paused/interrupted.", "warning")
                    break

        total_elapsed = time.time() - start_time
        summary = {
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "failed_items": failed_items,
            "elapsed_seconds": total_elapsed
        }

        log(f"Logging complete! Success: {success_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "info")
        return summary

    def run_ignore(
        self,
        gc_codes: List[str],
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """Add multiple caches to the Ignore list."""
        self.safety.reset()
        total = len(gc_codes)
        success_count = 0
        failed_count = 0
        failed_items: List[Dict[str, str]] = []

        def log(msg: str, level: str = "info"):
            if on_log:
                on_log(msg, level)
            else:
                print(f"[{level.upper()}] {msg}")

        log(f"Starting Ignore list addition for {total} caches...", "info")

        if not self.client.ensure_valid_session():
            log("Session is invalid. Please log in first.", "error")
            return {"total": total, "success": 0, "failed": total, "failed_items": []}

        log("Fetching current Ignore list from your account...", "info")
        ignored_set = self.client.get_ignored_cache_codes()
        if ignored_set:
            log(f"Found {len(ignored_set)} cache(s) already on your Ignore list.", "info")

        start_time = time.time()
        for idx, gc_code in enumerate(gc_codes, 1):
            if self.safety.is_cancelled:
                log(f"Cancelled by user at item {idx}/{total}.", "warning")
                break

            prefix = f"[{idx}/{total}] {gc_code}"
            clean_code = gc_code.strip().upper()

            # Fast check: already on ignore list (0 network calls)
            if clean_code in ignored_set:
                success_count += 1
                log(f"{prefix} 🚫 Already on Ignore list", "info")
                percent = (idx / total) * 100
                if on_progress:
                    on_progress(idx, total, percent, None, gc_code)
                continue

            success, message = self.client.ignore_cache(clean_code)

            if success:
                success_count += 1
                ignored_set.add(clean_code)
                log(f"{prefix} 🚫 {message}", "success")
            else:
                failed_count += 1
                failed_items.append({"gccode": gc_code, "reason": message})
                log(f"{prefix} ⚠️ {message}", "warning")

            percent = (idx / total) * 100
            if on_progress:
                on_progress(idx, total, percent, None, gc_code)

            if idx < total:
                if not self.safety.wait_before_next(idx, total):
                    break

        total_elapsed = time.time() - start_time
        return {
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "failed_items": failed_items,
            "elapsed_seconds": total_elapsed
        }
