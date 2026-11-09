"""
Orchestrates bulk logging and ignore operations for Geocaching.com.
Supports Found, DNF, Note, Needs Maintenance, Needs Archive, and Ignore.
Uses direct tRPC batch communication with safety pacing and live progress reporting.
"""

import time
import re
from datetime import datetime
from typing import List, Callable, Optional, Dict, Any, Set, Tuple
from core.client import GeocachingClient
from core.safety import SafetyManager
from core.config import AnswerTemplate, AiConfig


def extract_answers_from_note(note: str) -> str:
    """
    Extract answers from a cache personal note.
    If the note contains 'Poznámka:' or 'Poznámky:', extracts the text following it.
    Otherwise returns the entire note text.
    """
    if not note:
        return ""
    clean = note.strip()
    m = re.search(r'(?:^|\n)\s*Pozn[aá]mk[ya]?\s*:\s*(.*)$', clean, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return clean


def format_answer_message(
    template_text: str,
    answers: str,
    username: str = "",
    gccode: str = "",
    cache_name: str = ""
) -> str:
    """
    Substitute answers into template placeholder [...] (or {answers}),
    and replace {username}, {gccode}, {cachename}.
    """
    msg = template_text or "[...]"
    clean_ans = answers.strip() if answers else ""
    if "[...]" in msg:
        msg = msg.replace("[...]", clean_ans)
    elif "{answers}" in msg:
        msg = msg.replace("{answers}", clean_ans)
    else:
        msg = f"{msg.strip()}\n\n{clean_ans}" if clean_ans else msg.strip()

    if "{username}" in msg:
        msg = msg.replace("{username}", username)
    if "{gccode}" in msg:
        msg = msg.replace("{gccode}", gccode)
    if "{cachename}" in msg:
        msg = msg.replace("{cachename}", cache_name)
    return msg.strip()


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
        log_text: str = "",
        skip_already_found: bool = True,
        omit_virtual: bool = False,
        omit_earth: bool = False,
        omit_events: bool = False,
        omit_webcam: bool = False,
        omit_traditional: bool = False,
        omit_other: bool = False,
        omit_virtual_and_earth: bool = False,
        checkpoint_manager: Optional[Any] = None,
        resume: bool = False,
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Log multiple caches in bulk.
        :param gc_codes: List of GC codes (e.g. ['GC12345', 'GC67890'])
        :param log_type_key: 'FOUND', 'DNF', 'NOTE', 'NEEDS_OWNER_ATTENTION', 'NEEDS_REVIEWER_ATTENTION'
        :param date_str: Date string 'YYYY-MM-DD'
        :param log_text: Text content of the log
        :param skip_already_found: If True and log_type is 'Found it', checks Groundspeak for duplicates
        :param checkpoint_manager: Optional RunCheckpointManager instance for crash recovery
        :param resume: If True, resumes previous incomplete run by skipping completed codes
        """
        self.safety.reset()
        clean_codes = [c.strip().upper() for c in gc_codes if c.strip()]
        total = len(clean_codes)
        logged_count = 0
        skipped_filter_count = 0
        failed_count = 0
        failed_items: List[Dict[str, str]] = []

        log_type_id = LOG_TYPE_MAP.get(log_type_key, 2)
        display_type = LOG_TYPE_NAMES.get(log_type_key, log_type_key)

        def log(msg: str, level: str = "info"):
            if on_log:
                on_log(msg, level)
            else:
                print(f"[{level.upper()}] {msg}")

        # Checkpoint initialization
        completed_codes: Set[str] = set()
        if checkpoint_manager:
            if resume:
                state = checkpoint_manager.load_active_logging_run()
                if state:
                    completed_codes = set(state.completed_gccodes)
                    log(f"Resuming run from checkpoint: {len(completed_codes)}/{total} already finished.", "info")
            else:
                checkpoint_manager.start_logging_run(log_type_key, date_str, log_text, clean_codes)

        # Format date as ISO string (e.g. "2026-10-06T12:00:00.000Z")
        iso_date = f"{date_str}T12:00:00.000Z"

        log(f"Starting bulk logging: {total} caches as '{display_type}' for date {date_str}...", "info")

        if not self.client.ensure_valid_session():
            log("Session is invalid. Please log in first.", "error")
            return {"total": total, "success": 0, "failed": total, "failed_items": []}

        # Clear cached details to ensure fresh found status from Geocaching.com
        if hasattr(self.client, "clear_cache_details"):
            self.client.clear_cache_details()

        start_time = time.time()

        # Pre-filter already found caches when logging as "Found it"
        already_found_items: List[Tuple[str, Optional[str]]] = []
        to_process_codes: List[str] = []

        if log_type_id == 2 and skip_already_found:
            for clean_code in clean_codes:
                if clean_code in completed_codes:
                    already_found_items.append((clean_code, None))
                    continue
                is_found, found_date = self.client.is_cache_found(clean_code)
                if is_found:
                    already_found_items.append((clean_code, found_date))
                    if checkpoint_manager:
                        date_info = f" on {found_date}" if found_date else ""
                        checkpoint_manager.record_log_item(clean_code, success=True, reason=f"Already found{date_info}")
                        checkpoint_manager.append_logging_log(f"{clean_code} - Already found{date_info}")
                else:
                    to_process_codes.append(clean_code)
        else:
            to_process_codes = list(clean_codes)

        already_found_count = len(already_found_items)

        # Case 1: All caches are already found
        if already_found_count > 0 and len(to_process_codes) == 0:
            log(f"⏭️ All {total} cache(s) already found on {date_str} (skipped duplicates). Nothing to log.", "info")
            if on_progress:
                on_progress(total, total, 100.0, 0.0, "")
            total_elapsed = time.time() - start_time
            if checkpoint_manager:
                checkpoint_manager.finish_logging_run("completed")
            log(f"Logging complete! Logged: 0 | Skipped: {total} (already found) | Failed: 0 in {total_elapsed:.1f}s", "info")
            return {
                "total": total,
                "success": total,
                "logged": 0,
                "skipped": total,
                "failed": 0,
                "failed_items": [],
                "elapsed_seconds": total_elapsed
            }

        # Case 2: Some caches are already found
        if already_found_count > 0:
            codes_preview = ", ".join([c[0] for c in already_found_items[:8]])
            if already_found_count > 8:
                codes_preview += f" ... (+{already_found_count - 8} more)"
            log(f"⏭️ {already_found_count} cache(s) already found (filtered out: {codes_preview}).", "info")

        processed_times: List[float] = []

        for idx_rel, clean_code in enumerate(to_process_codes, 1):
            if self.safety.is_cancelled:
                log(f"Logging cancelled by user at item {idx_rel}/{len(to_process_codes)}.", "warning")
                break

            item_start = time.time()
            idx = already_found_count + idx_rel
            prefix = f"[{idx}/{total}] {clean_code}"

            # Check if already processed in earlier run (for non-duplicate runs)
            if clean_code in completed_codes:
                log(f"{prefix} ⏭️ Already processed in earlier run (checkpoint skip)", "info")
                percent = (idx / total) * 100
                if on_progress:
                    on_progress(idx, total, percent, None, clean_code)
                continue

            # Check cache type filtering
            should_filter = (
                omit_virtual
                or omit_earth
                or omit_events
                or omit_webcam
                or omit_traditional
                or omit_other
                or omit_virtual_and_earth
            )
            if should_filter and self.client.is_cache_filtered(
                clean_code,
                omit_virtual=omit_virtual or omit_virtual_and_earth,
                omit_earth=omit_earth or omit_virtual_and_earth,
                omit_events=omit_events,
                omit_webcam=omit_webcam,
                omit_traditional=omit_traditional,
                omit_other=omit_other
            ):
                skipped_filter_count += 1
                log(f"{prefix} ⏭️ Skipped: Cache type filtered out", "info")
                if checkpoint_manager:
                    checkpoint_manager.record_log_item(clean_code, success=True, reason="Filtered out")
                    checkpoint_manager.append_logging_log(f"{clean_code} - Skipped: Cache type filtered out")
                percent = (idx / total) * 100
                if on_progress:
                    on_progress(idx, total, percent, None, clean_code)
                continue

            success, message = self.client.create_log(clean_code, log_type_id, iso_date, log_text)

            if success:
                logged_count += 1
                log(f"{prefix} ✅ Logged as {display_type}", "success")
                if checkpoint_manager:
                    checkpoint_manager.record_log_item(clean_code, success=True)
                    checkpoint_manager.append_logging_log(f"{clean_code} - Logged as {display_type}")
            else:
                failed_count += 1
                failed_items.append({"gccode": clean_code, "reason": message})
                log(f"{prefix} ❌ Failed: {message}", "error")
                if checkpoint_manager:
                    checkpoint_manager.record_log_item(clean_code, success=False, reason=message)
                    checkpoint_manager.append_logging_log(f"{clean_code} - Failed: {message}")

            elapsed_item = time.time() - item_start
            processed_times.append(elapsed_item)
            avg_time = sum(processed_times[-10:]) / len(processed_times[-10:])
            remaining_items = len(to_process_codes) - idx_rel
            eta = remaining_items * (avg_time + ((self.safety.min_delay + self.safety.max_delay) / 2))

            percent = (idx / total) * 100
            if on_progress:
                on_progress(idx, total, percent, eta, clean_code)

            if idx_rel < len(to_process_codes):
                if not self.safety.wait_before_next(idx, total, log_callback=lambda m: log(m, "info")):
                    log("Logging paused/interrupted.", "warning")
                    break

        total_elapsed = time.time() - start_time
        if checkpoint_manager:
            if self.safety.is_cancelled:
                checkpoint_manager.finish_logging_run("cancelled")
            else:
                checkpoint_manager.finish_logging_run("completed")

        total_skipped = already_found_count + skipped_filter_count
        success_count = logged_count + total_skipped
        is_cancelled = self.safety.is_cancelled
        summary = {
            "total": total,
            "success": success_count,
            "logged": logged_count,
            "skipped": total_skipped,
            "failed": failed_count,
            "failed_items": failed_items,
            "cancelled": is_cancelled,
            "elapsed_seconds": total_elapsed
        }

        if is_cancelled:
            log(f"Logging stopped by user! Logged: {logged_count} | Skipped: {total_skipped} | Failed: {failed_count} in {total_elapsed:.1f}s", "warning")
        else:
            log(f"Logging complete! Logged: {logged_count} | Skipped: {total_skipped} | Failed: {failed_count} in {total_elapsed:.1f}s", "info")
        return summary

    def run_edit_log(
        self,
        gc_codes: List[str],
        log_type_key: Optional[str] = None,
        date_str: Optional[str] = None,
        log_text: Optional[str] = None,
        keep_original_text: bool = False,
        keep_original_type: bool = False,
        keep_original_date: bool = False,
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Edit existing logs in bulk for multiple caches.
        If keep_original_text is True (or log_text starts with '[Ponechat' or '[Keep'), retains each cache's existing text.
        """
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

        log(f"Starting bulk edit for {total} cache log(s)...", "info")

        if not self.client.ensure_valid_session():
            log("Session is invalid. Please log in first.", "error")
            return {"total": total, "success": 0, "failed": total, "failed_items": []}

        start_time = time.time()
        processed_times: List[float] = []

        for idx, gc_code in enumerate(gc_codes, 1):
            if self.safety.is_cancelled:
                log(f"Edit cancelled by user at item {idx}/{total}.", "warning")
                break

            item_start = time.time()
            clean_code = gc_code.strip().upper()
            prefix = f"[{idx}/{total}] {clean_code}"

            user_logs = self.client.get_user_logs_for_cache(clean_code)
            if not user_logs:
                failed_count += 1
                failed_items.append({"gccode": clean_code, "reason": "No existing log found on your account"})
                log(f"{prefix} ⏭️ No existing log found on your account to edit.", "warning")
                percent = (idx / total) * 100
                if on_progress:
                    on_progress(idx, total, percent, None, clean_code)
                continue

            target_log = user_logs[0]
            log_code = target_log["log_code"]

            # Determine text
            is_keep_text = (
                keep_original_text
                or not log_text
                or log_text.startswith("[Ponechat")
                or log_text.startswith("[Keep")
            )
            final_text = target_log["text"] if is_keep_text else log_text

            # Determine type
            if keep_original_type or not log_type_key or log_type_key == "KEEP_ORIGINAL":
                final_type_id = target_log["log_type_id"]
            else:
                final_type_id = LOG_TYPE_MAP.get(log_type_key, target_log["log_type_id"])

            # Determine date
            if keep_original_date or not date_str:
                final_iso = target_log["iso_date"] or f"{datetime.now().strftime('%Y-%m-%d')}T12:00:00.000Z"
            else:
                final_iso = f"{date_str}T12:00:00.000Z"

            success, message = self.client.update_log(log_code, clean_code, final_type_id, final_iso, final_text)
            if success:
                success_count += 1
                log(f"{prefix} ✅ Updated log ({log_code})", "success")
            else:
                failed_count += 1
                failed_items.append({"gccode": clean_code, "reason": message})
                log(f"{prefix} ❌ Edit failed: {message}", "error")

            elapsed_item = time.time() - item_start
            processed_times.append(elapsed_item)
            avg_time = sum(processed_times[-10:]) / len(processed_times[-10:])
            remaining_items = total - idx
            eta = remaining_items * (avg_time + ((self.safety.min_delay + self.safety.max_delay) / 2))

            percent = (idx / total) * 100
            if on_progress:
                on_progress(idx, total, percent, eta, clean_code)

            if idx < total:
                if not self.safety.wait_before_next(idx, total, log_callback=lambda m: log(m, "info")):
                    break

        total_elapsed = time.time() - start_time
        summary = {
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "failed_items": failed_items,
            "cancelled": self.safety.is_cancelled,
            "elapsed_seconds": total_elapsed
        }
        if self.safety.is_cancelled:
            log(f"Bulk edit stopped by user! Success: {success_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "warning")
        else:
            log(f"Bulk edit complete! Success: {success_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "info")
        return summary

    def run_delete_log(
        self,
        gc_codes: List[str],
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Delete existing logs in bulk for multiple caches.
        """
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

        log(f"Starting bulk delete for {total} cache log(s)...", "info")

        if not self.client.ensure_valid_session():
            log("Session is invalid. Please log in first.", "error")
            return {"total": total, "success": 0, "failed": total, "failed_items": []}

        start_time = time.time()
        processed_times: List[float] = []

        for idx, gc_code in enumerate(gc_codes, 1):
            if self.safety.is_cancelled:
                log(f"Delete cancelled by user at item {idx}/{total}.", "warning")
                break

            item_start = time.time()
            clean_code = gc_code.strip().upper()
            prefix = f"[{idx}/{total}] {clean_code}"

            user_logs = self.client.get_user_logs_for_cache(clean_code)
            if not user_logs:
                failed_count += 1
                failed_items.append({"gccode": clean_code, "reason": "No existing log found on your account"})
                log(f"{prefix} ⏭️ No existing log found on your account to delete.", "warning")
                percent = (idx / total) * 100
                if on_progress:
                    on_progress(idx, total, percent, None, clean_code)
                continue

            all_deleted = True
            for entry in user_logs:
                log_code = entry["log_code"]
                success, message = self.client.delete_log(log_code)
                if success:
                    log(f"{prefix} 🗑️ Deleted log ({log_code})", "success")
                else:
                    all_deleted = False
                    failed_items.append({"gccode": clean_code, "reason": message})
                    log(f"{prefix} ❌ Delete failed for {log_code}: {message}", "error")

            if all_deleted:
                success_count += 1
            else:
                failed_count += 1

            elapsed_item = time.time() - item_start
            processed_times.append(elapsed_item)
            avg_time = sum(processed_times[-10:]) / len(processed_times[-10:])
            remaining_items = total - idx
            eta = remaining_items * (avg_time + ((self.safety.min_delay + self.safety.max_delay) / 2))

            percent = (idx / total) * 100
            if on_progress:
                on_progress(idx, total, percent, eta, clean_code)

            if idx < total:
                if not self.safety.wait_before_next(idx, total, log_callback=lambda m: log(m, "info")):
                    break

        total_elapsed = time.time() - start_time
        summary = {
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "failed_items": failed_items,
            "cancelled": self.safety.is_cancelled,
            "elapsed_seconds": total_elapsed
        }
        if self.safety.is_cancelled:
            log(f"Bulk delete stopped by user! Success: {success_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "warning")
        else:
            log(f"Bulk delete complete! Success: {success_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "info")
        return summary

    def run_ignore(
        self,
        gc_codes: List[str],
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        source_list_code: Optional[str] = None,
        remove_from_source_list: bool = False
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
                log(f"{prefix} Already on Ignore list", "info")
                if remove_from_source_list and source_list_code:
                    del_ok, del_msg = self.client.remove_cache_from_list(source_list_code, clean_code)
                    if del_ok:
                        log(f"{prefix} Removed from source list {source_list_code}", "info")
                    else:
                        log(f"{prefix} Could not remove from source list: {del_msg}", "warning")
                percent = (idx / total) * 100
                if on_progress:
                    on_progress(idx, total, percent, None, gc_code)
                continue

            success, message = self.client.ignore_cache(clean_code)

            if success:
                success_count += 1
                ignored_set.add(clean_code)
                log(f"{prefix} {message}", "success")
                if remove_from_source_list and source_list_code:
                    del_ok, del_msg = self.client.remove_cache_from_list(source_list_code, clean_code)
                    if del_ok:
                        log(f"{prefix} Removed from source list {source_list_code}", "info")
                    else:
                        log(f"{prefix} Could not remove from source list: {del_msg}", "warning")
            else:
                failed_count += 1
                failed_items.append({"gccode": gc_code, "reason": message})
                log(f"{prefix} {message}", "warning")

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
            "cancelled": self.safety.is_cancelled,
            "elapsed_seconds": total_elapsed
        }

    def run_unignore(
        self,
        gc_codes: List[str],
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """Remove multiple caches from the Ignore list (un-ignore)."""
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

        log(f"Starting Ignore list removal (un-ignore) for {total} caches...", "info")

        if not self.client.ensure_valid_session():
            log("Session is invalid. Please log in first.", "error")
            return {"total": total, "success": 0, "failed": total, "failed_items": []}

        log("Fetching current Ignore list from your account...", "info")
        ignored_set = self.client.get_ignored_cache_codes()
        log(f"Found {len(ignored_set)} cache(s) currently on your Ignore list.", "info")

        start_time = time.time()
        for idx, gc_code in enumerate(gc_codes, 1):
            if self.safety.is_cancelled:
                log(f"Cancelled by user at item {idx}/{total}.", "warning")
                break

            prefix = f"[{idx}/{total}] {gc_code}"
            clean_code = gc_code.strip().upper()

            # Fast check: if not in ignored_set, it's already un-ignored (0 network calls)
            if clean_code not in ignored_set:
                success_count += 1
                log(f"{prefix} ⏩ Not on Ignore list (already un-ignored)", "info")
                percent = (idx / total) * 100
                if on_progress:
                    on_progress(idx, total, percent, None, gc_code)
                continue

            success, message = self.client.unignore_cache(clean_code)

            if success:
                success_count += 1
                ignored_set.discard(clean_code)
                log(f"{prefix} ✅ {message}", "success")
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
            "cancelled": self.safety.is_cancelled,
            "elapsed_seconds": total_elapsed
        }

    def run_send_concepts(
        self,
        on_progress: Optional[Callable[[int, int, float, Optional[float], str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        checkpoint_manager: Optional[Any] = None,
        resume: bool = False,
        send_answers: bool = False,
        answer_templates: Optional[List[AnswerTemplate]] = None,
        selected_template_name: str = "",
        ai_config: Optional[AiConfig] = None,
        default_answer_source: str = "note",
        on_ask_answer: Optional[Callable[[Dict[str, Any]], Tuple[str, str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Fetch all pending log drafts (concepts) from the user's account and promote them
        to published geocache logs one by one with anti-detection pacing.
        If send_answers is enabled, EarthCache and Virtual Cache concepts pause for user review
        and send answers via Geocaching Message Center.
        """
        self.safety.reset()

        def log(msg: str, level: str = "info"):
            if checkpoint_manager:
                checkpoint_manager.append_log(msg)
            if on_log:
                on_log(msg, level)
            else:
                print(f"[{level.upper()}] {msg}")

        log("Fetching pending concepts from your Geocaching.com account...", "info")
        ok, drafts, err_msg = self.client.get_log_drafts()
        if not ok:
            log(f"Failed to fetch concepts: {err_msg}", "error")
            return {"total": 0, "success": 0, "failed": 0, "failed_items": [], "cancelled": False, "elapsed_seconds": 0.0}

        total = len(drafts)
        if total == 0:
            log("No pending concepts found on your account.", "info")
            return {"total": 0, "success": 0, "failed": 0, "failed_items": [], "cancelled": False, "elapsed_seconds": 0.0}

        log(f"Found {total} concept(s) waiting to be sent. Starting submission...", "info")

        start_time = time.time()
        success_count = 0
        failed_count = 0
        failed_items: List[Dict[str, str]] = []
        processed_times: List[float] = []

        for idx, draft in enumerate(drafts, 1):
            if self.safety.is_cancelled:
                log(f"Submission cancelled by user at item {idx}/{total}.", "warning")
                break

            item_start = time.time()
            ref_code = draft.get("referenceCode", "")
            gc_code = draft.get("geocacheCode", "")
            log_type = draft.get("logType", "Log")
            date_raw = draft.get("loggedDate", "")
            date_display = date_raw[:10] if date_raw else ""
            prefix = f"[{idx}/{total}] {gc_code} ({ref_code})"

            # Check if this cache is an EarthCache or Virtual Cache requiring answers
            is_earth = self.client.is_earth_cache(gc_code)
            is_virtual = self.client.is_virtual_cache(gc_code)

            if send_answers and (is_earth or is_virtual) and on_ask_answer:
                cache_type_name = "EarthCache" if is_earth else "Virtual Cache"
                log(f"{prefix} Found {cache_type_name} requiring answers. Loading details...", "info")
                page_data = self.client.get_cache_page_data(gc_code)

                note_content = page_data.get("personal_note", "") or draft.get("note", "")
                extracted_ans = extract_answers_from_note(note_content)
                owner_guid = page_data.get("owner_guid", "")
                owner_name = page_data.get("owner_name", "")
                listing_text = page_data.get("listing_text", "")
                cache_name = page_data.get("cache_name") or draft.get("geocacheName", gc_code)

                ask_payload = {
                    "draft": draft,
                    "gccode": gc_code,
                    "name": cache_name,
                    "cache_type": cache_type_name,
                    "owner_guid": owner_guid,
                    "owner_name": owner_name,
                    "note": note_content,
                    "extracted_answers": extracted_ans,
                    "listing": listing_text,
                    "default_template_name": selected_template_name,
                    "default_source": default_answer_source
                }

                action, final_answers, chosen_tmpl_name = on_ask_answer(ask_payload)

                if action == "STOP" or self.safety.is_cancelled:
                    log(f"{prefix} Submission cancelled by user during answer review.", "warning")
                    break

                if action == "SEND":
                    # Locate template text
                    tmpl_text = ""
                    if answer_templates:
                        for tmpl in answer_templates:
                            if tmpl.name == chosen_tmpl_name:
                                tmpl_text = tmpl.text
                                break
                        if not tmpl_text and answer_templates:
                            tmpl_text = answer_templates[0].text
                    if not tmpl_text:
                        tmpl_text = "[...]"

                    full_message = format_answer_message(
                        template_text=tmpl_text,
                        answers=final_answers,
                        username=self.client.logged_in_username or "",
                        gccode=gc_code,
                        cache_name=cache_name
                    )

                    if owner_guid:
                        log(f"{prefix} ✉️ Sending answers to owner {owner_name or owner_guid}...", "info")
                        conv_id = self.client.get_or_create_conversation(owner_guid)
                        if conv_id:
                            msg_ok, msg_res = self.client.send_message_center_message(conv_id, full_message)
                            if msg_ok:
                                log(f"{prefix} ✉️ Answers successfully sent to owner {owner_name}!", "success")
                            else:
                                log(f"{prefix} ⚠️ Failed to send message to owner: {msg_res}", "warning")
                        else:
                            log(f"{prefix} ⚠️ Could not establish conversation with owner {owner_name}.", "warning")
                    else:
                        log(f"{prefix} ⚠️ Could not determine owner GUID for sending answers.", "warning")

                elif action == "SKIP":
                    log(f"{prefix} ⏭️ Skipped sending answers to owner as requested.", "info")

            # Promote draft to live log
            succ, msg = self.client.promote_log_draft(ref_code)

            if succ:
                success_count += 1
                date_suffix = f" on {date_display}" if date_display else ""
                log(f"{prefix} ✅ Concept submitted as {log_type}{date_suffix}", "success")
            else:
                failed_count += 1
                failed_items.append({"gccode": gc_code, "reason": msg})
                log(f"{prefix} ❌ Failed to submit concept: {msg}", "error")

            # Progress & ETA calculation
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
                    log("Concepts submission interrupted.", "warning")
                    break

        total_elapsed = time.time() - start_time
        is_cancelled = self.safety.is_cancelled
        summary = {
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "failed_items": failed_items,
            "cancelled": is_cancelled,
            "elapsed_seconds": total_elapsed
        }

        if is_cancelled:
            log(f"Concepts submission stopped by user! Submitted: {success_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "warning")
        else:
            log(f"Concepts submission complete! Submitted: {success_count} | Failed: {failed_count} in {total_elapsed:.1f}s", "info")

        return summary

