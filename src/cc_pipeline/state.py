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

    def _atomic_write(self, data: dict) -> None:
        """Write state JSON atomically (temp file + rename)."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(self.run_dir), suffix=".tmp",
            prefix=".state-", delete=False
        ) as tmp:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, str(self.state_file))

    def save(self, run_id: str, modules: dict) -> None:
        """Save full orchestrator state.

        Args:
            run_id: Unique run identifier.
            modules: Dict of module_name → module state.
        """
        with self._lock:
            state = {
                "run_id": run_id,
                "saved_at": datetime.now().isoformat(),
                "modules": modules,
            }
            self._atomic_write(state)

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
                try:
                    with open(self.state_file) as f:
                        state = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    state = None
            if state is None:
                state = {"run_id": "unknown", "saved_at": "", "modules": {}}
            if module_name not in state["modules"]:
                state["modules"][module_name] = {}
            state["modules"][module_name].update(kwargs)
            state["saved_at"] = datetime.now().isoformat()
            self._atomic_write(state)

    def mark_step_completed(self, module_name: str, step_id: str, loop_file: str = "") -> None:
        """Mark a step as completed for resume support.

        Args:
            module_name: Module name.
            step_id: Step identifier.
            loop_file: Loop file name if per_file step (empty otherwise).
        """
        key = f"{step_id}/{loop_file}" if loop_file else step_id
        with self._lock:
            state = None
            if self.state_file.exists():
                try:
                    with open(self.state_file) as f:
                        state = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    state = None
            if state is None:
                state = {"run_id": "unknown", "saved_at": "", "modules": {}}
            if module_name not in state["modules"]:
                state["modules"][module_name] = {}
            completed = state["modules"][module_name].setdefault("completed_steps", [])
            if key not in completed:
                completed.append(key)
            state["saved_at"] = datetime.now().isoformat()
            self._atomic_write(state)

    def get_completed_steps(self, module_name: str) -> set[str]:
        """Get completed steps for a module (for resume)."""
        with self._lock:
            if not self.state_file.exists():
                return set()
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                return set()
            mods = state.get("modules", {})
            if module_name not in mods:
                return set()
            return set(mods[module_name].get("completed_steps", []))

    def clear_step_completed(self, module_name: str, step_id: str, loop_file: str = "") -> None:
        """Remove a step from completed_steps (when on_failure jump invalidates it)."""
        key = f"{step_id}/{loop_file}" if loop_file else step_id
        with self._lock:
            if not self.state_file.exists():
                return
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                return
            mods = state.get("modules", {})
            if module_name not in mods:
                return
            completed = mods[module_name].get("completed_steps", [])
            changed = False
            if key in completed:
                completed.remove(key)
                mods[module_name]["completed_steps"] = completed
                changed = True
            # Always clear cc_session (even for failed steps — on_failure jump)
            cc = mods[module_name].get("cc_sessions", {})
            step_cc = cc.get(step_id, {}) if isinstance(cc, dict) else {}
            file_key = loop_file or ""
            if file_key in step_cc:
                del step_cc[file_key]
                if step_cc:
                    cc[step_id] = step_cc
                else:
                    cc.pop(step_id, None)
                mods[module_name]["cc_sessions"] = cc
                changed = True
            if changed:
                self._atomic_write(state)

    def set_cc_session(self, module_name: str, step_id: str, loop_file: str, session_uuid: str) -> None:
        """Store a CC session UUID for a specific step+file."""
        import uuid as _uuid
        _uuid.UUID(session_uuid)  # validate format
        with self._lock:
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                state = {}
            mods = state.setdefault("modules", {})
            mod = mods.setdefault(module_name, {})
            cc = mod.setdefault("cc_sessions", {})
            step_cc = cc.setdefault(step_id, {})
            step_cc[loop_file or ""] = session_uuid
            state["saved_at"] = datetime.now().isoformat()
            self._atomic_write(state)

    def get_cc_session(self, module_name: str, step_id: str, loop_file: str) -> str | None:
        """Get CC session UUID for a step+file, or None."""
        with self._lock:
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                state = {}
            if not state:
                return None
            mods = state.get("modules", {})
            mod = mods.get(module_name, {})
            cc = mod.get("cc_sessions", {})
            step_cc = cc.get(step_id, {}) if isinstance(cc, dict) else {}
            return step_cc.get(loop_file or "")

    def clear_cc_session(self, module_name: str, step_id: str, loop_file: str) -> None:
        """Remove CC session UUID for a step+file."""
        with self._lock:
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                state = {}
            if not state:
                return
            mods = state.get("modules", {})
            mod = mods.get(module_name, {})
            cc = mod.get("cc_sessions", {})
            step_cc = cc.get(step_id, {}) if isinstance(cc, dict) else {}
            file_key = loop_file or ""
            if file_key in step_cc:
                del step_cc[file_key]
                if step_cc:
                    cc[step_id] = step_cc
                else:
                    cc.pop(step_id, None)
                mod["cc_sessions"] = cc
                self._atomic_write(state)

    def clear_completed_for_file(self, module_name: str, loop_file: str) -> None:
        """Remove ALL completed steps for a given loop_file.

        Used when continue_on_error marks a file as failed —
        downstream steps that depend on its output should be invalidated.
        """
        with self._lock:
            if not self.state_file.exists():
                return
            try:
                with open(self.state_file) as f:
                    state = json.load(f)
            except (json.JSONDecodeError, ValueError):
                return
            mods = state.get("modules", {})
            if module_name not in mods:
                return
            completed = mods[module_name].get("completed_steps", [])
            # Remove any key ending with /loop_file
            suffix = f"/{loop_file}"
            new_completed = [k for k in completed if not k.endswith(suffix)]
            completed_changed = len(new_completed) != len(completed)
            if completed_changed:
                mods[module_name]["completed_steps"] = new_completed

            # Also clear cc_sessions for all steps of this file
            cc = mods[module_name].get("cc_sessions", {})
            cc_changed = False
            if isinstance(cc, dict):
                for step_id in list(cc.keys()):
                    step_cc = cc.get(step_id, {})
                    if isinstance(step_cc, dict) and loop_file in step_cc:
                        del step_cc[loop_file]
                        cc_changed = True
                    if isinstance(step_cc, dict) and not step_cc:
                        cc.pop(step_id, None)
                if cc_changed:
                    mods[module_name]["cc_sessions"] = cc

            if completed_changed or cc_changed:
                self._atomic_write(state)

    def set_run_id(self, run_id: str) -> None:
        """Set run_id in state file (idempotent, thread-safe)."""
        with self._lock:
            state = None
            if self.state_file.exists():
                try:
                    with open(self.state_file) as f:
                        state = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    state = None
            if state is None:
                state = {"run_id": run_id, "saved_at": "", "modules": {}}
            state["run_id"] = run_id
            state["saved_at"] = datetime.now().isoformat()
            self._atomic_write(state)

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
