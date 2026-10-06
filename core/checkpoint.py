"""
Checkpoint and crash recovery manager for GC Autologger.
Tracks active batch operations in real-time, persists progress to disk,
and enables seamless resumption after unexpected crashes or app restarts.
"""

import os
import json
import time
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any


@dataclass
class CoordsRunState:
    run_id: str
    file_path: str
    mode: str
    total: int
    completed_gccodes: List[str] = field(default_factory=list)
    failed_items: List[Dict[str, str]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "in_progress"  # in_progress, completed, cancelled


class RunCheckpointManager:
    """Manages active run state files and crash recovery logs."""

    def __init__(self, runs_dir: Optional[str] = None):
        if not runs_dir:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            runs_dir = os.path.join(base_dir, "runs")
        self.runs_dir = runs_dir
        os.makedirs(self.runs_dir, exist_ok=True)

        self.coords_state_file = os.path.join(self.runs_dir, "active_coords_run.json")
        self.coords_log_file = os.path.join(self.runs_dir, "active_coords_run.log")

    def has_active_coords_run(self) -> bool:
        """Check if an unfinished coordinate upload run exists."""
        state = self.load_active_coords_run()
        return state is not None and state.status == "in_progress" and len(state.completed_gccodes) < state.total

    def load_active_coords_run(self) -> Optional[CoordsRunState]:
        """Load the active coordinate upload run state from disk."""
        if not os.path.exists(self.coords_state_file):
            return None
        try:
            with open(self.coords_state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CoordsRunState(
                run_id=data.get("run_id", ""),
                file_path=data.get("file_path", ""),
                mode=data.get("mode", "OVERWRITE_ALL"),
                total=data.get("total", 0),
                completed_gccodes=data.get("completed_gccodes", []),
                failed_items=data.get("failed_items", []),
                started_at=data.get("started_at", 0.0),
                updated_at=data.get("updated_at", 0.0),
                status=data.get("status", "in_progress")
            )
        except Exception:
            return None

    def start_coords_run(
        self,
        file_path: str,
        mode: str,
        total: int,
        existing_completed: Optional[List[str]] = None
    ) -> CoordsRunState:
        """Initialize a new coordinate upload run or resume an existing one."""
        run_id = f"coords_{int(time.time())}"
        completed = list(existing_completed) if existing_completed else []
        state = CoordsRunState(
            run_id=run_id,
            file_path=file_path,
            mode=mode,
            total=total,
            completed_gccodes=completed,
            started_at=time.time(),
            updated_at=time.time(),
            status="in_progress"
        )
        self._save_state(state)
        # If this is a brand new run, clear previous log file
        if not existing_completed:
            try:
                with open(self.coords_log_file, "w", encoding="utf-8") as f:
                    f.write(f"--- Run started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            except Exception:
                pass
        return state

    def record_coord_item(self, gccode: str, success: bool, reason: str = "") -> None:
        """Record an item as completed or failed and flush state to disk."""
        state = self.load_active_coords_run()
        if not state:
            return

        if success:
            if gccode not in state.completed_gccodes:
                state.completed_gccodes.append(gccode)
        else:
            state.failed_items.append({"gccode": gccode, "reason": reason})

        state.updated_at = time.time()
        self._save_state(state)

    def append_log(self, message: str) -> None:
        """Append a message line to the active run log file."""
        try:
            timestamp = time.strftime("[%H:%M:%S]")
            with open(self.coords_log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} {message}\n")
        except Exception:
            pass

    def read_saved_logs(self, max_lines: int = 150) -> List[str]:
        """Read recent lines from the active run log file."""
        if not os.path.exists(self.coords_log_file):
            return []
        try:
            with open(self.coords_log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = [line.rstrip() for line in f.readlines()]
                return lines[-max_lines:]
        except Exception:
            return []

    def finish_coords_run(self, status: str = "completed") -> None:
        """Mark the active coordinate run as completed or cancelled and remove active checkpoint."""
        if os.path.exists(self.coords_state_file):
            try:
                state = self.load_active_coords_run()
                if state:
                    state.status = status
                    state.updated_at = time.time()
                    archive_path = os.path.join(self.runs_dir, f"{state.run_id}_{status}.json")
                    with open(archive_path, "w", encoding="utf-8") as f:
                        json.dump(asdict(state), f, indent=2)
                os.remove(self.coords_state_file)
            except Exception:
                pass

    def discard_coords_run(self) -> None:
        """Discard any active coordinate run checkpoint and its log."""
        if os.path.exists(self.coords_state_file):
            try:
                os.remove(self.coords_state_file)
            except Exception:
                pass
        if os.path.exists(self.coords_log_file):
            try:
                os.remove(self.coords_log_file)
            except Exception:
                pass

    def _save_state(self, state: CoordsRunState) -> None:
        """Atomically save state to disk."""
        tmp_file = f"{self.coords_state_file}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(asdict(state), f, indent=2)
            os.replace(tmp_file, self.coords_state_file)
        except Exception:
            pass
