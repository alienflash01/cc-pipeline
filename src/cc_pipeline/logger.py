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

    def log_prompt(self, step: str, prompt: str) -> None:
        """Log the full CC prompt sent for a step (truncated to 2000 chars).

        Lets failed runs be reproduced/audited — the exact instruction handed
        to Claude Code is preserved in the transcript.
        """
        self.event("cc_prompt", step=step, prompt=prompt[:2000])
