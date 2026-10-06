"""
Safety and anti-detection engine for Geocaching.com API communication.
Implements randomized jitter, periodic batch pauses (breathers),
rate-limit exponential backoff, and thread-safe pause/cancel controls.
"""

import time
import random
import threading
from typing import Callable, Optional


class SafetyManager:
    """Manages request pacing and user interruptions to mimic human behavior."""

    def __init__(
        self,
        min_delay: float = 0.7,
        max_delay: float = 1.2,
        breather_interval: int = 50,
        breather_duration: float = 4.0
    ):
        self.min_delay = max(0.1, min_delay)
        self.max_delay = max(self.min_delay, max_delay)
        self.breather_interval = max(1, breather_interval)
        self.breather_duration = max(0.0, breather_duration)

        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Unpaused by default

    def reset(self) -> None:
        """Reset cancellation and pause states before starting a new batch."""
        self._cancel_event.clear()
        self._pause_event.set()

    def cancel(self) -> None:
        """Signal the running operation to terminate immediately."""
        self._cancel_event.set()
        self._pause_event.set()  # Unblock if currently paused

    def pause(self) -> None:
        """Pause execution without terminating."""
        self._pause_event.clear()

    def resume(self) -> None:
        """Resume paused execution."""
        self._pause_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def wait_interruptible(self, seconds: float) -> bool:
        """
        Sleep for a given duration while listening for cancellation and pause signals.
        Returns True if completed successfully, False if cancelled.
        """
        end_time = time.time() + seconds
        while time.time() < end_time:
            if self._cancel_event.is_set():
                return False

            # Wait if paused
            while not self._pause_event.is_set():
                if self._cancel_event.is_set():
                    return False
                time.sleep(0.2)

            time.sleep(0.05)

        return not self._cancel_event.is_set()

    def wait_before_next(
        self,
        item_index: int,
        total_items: int,
        log_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """
        Apply randomized jitter and periodic batch breathers between requests.
        Returns True to continue, False if operation was cancelled.
        """
        if self._cancel_event.is_set():
            return False

        # Periodic breather pause (e.g. every 50 items)
        if item_index > 0 and item_index % self.breather_interval == 0:
            breather = self.breather_duration * random.uniform(0.85, 1.25)
            if log_callback:
                log_callback(f"☕ Safety pause: batch checkpoint {item_index}/{total_items} (resting {breather:.1f}s)...")
            if not self.wait_interruptible(breather):
                return False

        # Randomized per-item jitter
        jitter = random.uniform(self.min_delay, self.max_delay)
        return self.wait_interruptible(jitter)

    def handle_rate_limit(
        self,
        backoff_seconds: float = 30.0,
        log_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """
        Handle HTTP 429 Too Many Requests with exponential or fixed backoff.
        """
        if log_callback:
            log_callback(f"⚠️ Rate limit warning from server. Backing off for {backoff_seconds:.0f}s...")
        return self.wait_interruptible(backoff_seconds)
