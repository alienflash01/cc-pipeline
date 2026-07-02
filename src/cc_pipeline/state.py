"""State Manager — orchestrator state persistence for crash recovery."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class StateManager:
    """Manages orchestrator state JSON for crash recovery."""

    def __init__(self, run_dir: str):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.run_dir / "orchestrator-state.json"
        self._lock = threading.Lock()

    def save(self, run_id: str, modules: dict) -> None:
        """Save full orchestrator state.

        Args:
            run_id: Unique run identifier.
            modules: Dict of module_name → module state.
        """
        with self._lock:
            state = {
                "run_id": run_id,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "modules": modules,
            }
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

    def load(self) -> dict | None:
        """Load previously saved state.

        Returns:
            State dict, or None if no state file exists.
        """
        with self._lock:
            if not self.state_file.exists():
                return None
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError):
                return None

    def update_module(self, module_name: str, **kwargs) -> None:
        """Update a single module's state fields.

        Args:
            module_name: Module to update.
            **kwargs: Fields to update (status, current_step, etc.).
        """
        with self._lock:
            state = None
            if self.state_file.exists():
                with open(self.state_file) as f:
                    state = json.load(f)
            if state is None:
                state = {"run_id": "unknown", "saved_at": "", "modules": {}}
            if module_name not in state["modules"]:
                state["modules"][module_name] = {}
            state["modules"][module_name].update(kwargs)
            state["saved_at"] = datetime.now(timezone.utc).isoformat()
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

    def set_run_id(self, run_id: str) -> None:
        """Set run_id in state file (idempotent, thread-safe)."""
        with self._lock:
            state = None
            if self.state_file.exists():
                with open(self.state_file) as f:
                    state = json.load(f)
            if state is None:
                state = {"run_id": run_id, "saved_at": "", "modules": {}}
            state["run_id"] = run_id
            state["saved_at"] = datetime.now(timezone.utc).isoformat()
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

    def get_failed_modules(self) -> list[str]:
        """Return list of module names that failed.

        Returns:
            List of module names with status == "failed".
        """
        state = self.load()
        if state is None:
            return []
        return [
            name for name, mod_state in state.get("modules", {}).items()
            if mod_state.get("status") == "failed"
        ]

    def get_resume_point(self, module_name: str) -> dict | None:
        """Get the resume point for a specific module.

        Args:
            module_name: Module to check.

        Returns:
            Dict with last_passed_step and current_step, or None.
        """
        state = self.load()
        if state is None:
            return None
        mod_state = state.get("modules", {}).get(module_name)
        if mod_state is None:
            return None
        return {
            "last_passed_step": mod_state.get("last_passed_step"),
            "current_step": mod_state.get("current_step"),
            "status": mod_state.get("status"),
        }
