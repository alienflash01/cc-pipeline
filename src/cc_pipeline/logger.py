"""Logger — JSONL transcript for pipeline execution."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class Logger:
    """Writes structured JSONL events for a single module's pipeline run."""

    def __init__(self, run_dir: str, module_name: str):
        self.module_name = module_name
        self.log_dir = Path(run_dir) / module_name
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "transcript.jsonl"
        self._lock = threading.Lock()
        # Create empty file so it exists immediately
        self.log_file.touch(exist_ok=True)

    def event(self, event: str, **kwargs) -> None:
        """Write a generic event to the JSONL transcript."""
        entry = {
            "ts": datetime.now().isoformat(),
            "module": self.module_name,
            "event": event,
            **kwargs,
        }
        with self._lock:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_pass(self, step: str, attempt: int, info: dict | None = None) -> None:
        """Log a step pass event."""
        self.event("pass", step=step, attempt=attempt, info=info or {})

    def log_fail(self, step: str, attempt: int, reason: str) -> None:
        """Log a step fail event."""
        self.event("fail", step=step, attempt=attempt, reason=reason)

    def log_retry(self, step: str, attempt: int, reason: str) -> None:
        """Log a retry event."""
        self.event("retry", step=step, attempt=attempt, reason=reason)

    def log_prompt(self, step: str, prompt: str):
        """Log the full CC prompt sent for a step. No truncation."""
        self.event("cc_prompt", step=step, prompt=prompt)

    def log_cc_result(self, step: str, cc_result) -> None:
        """Log CC execution result (returncode/stdout/stderr) to transcript.

        stdout capped at 20000 chars, stderr at 10000 chars.
        """
        self.event(
            "cc_result",
            step=step,
            returncode=cc_result.returncode,
            stdout=(cc_result.stdout or "")[:20000],
            stderr=(cc_result.stderr or "")[:10000],
        )

    def log_command_audit(self, step: str, command: str, cwd: str, executor: str,
                          returncode: int = 0, **extra) -> None:
        """Log a command execution for audit trail.

        Records what command was run, where, by which executor, and the result.
        """
        self.event(
            "command_audit",
            step=step,
            executor=executor,
            command=command[:500],
            cwd=cwd,
            returncode=returncode,
            **extra,
        )

    def log_file_changes(self, step: str, changes: list[str]) -> None:
        """Log git status changes after CC execution for audit trail."""
        if changes:
            self.event("file_changes", step=step, changes=changes)
