"""TDD: State persistence + crash recovery tests."""
import pytest
import json
from pathlib import Path


class TestStatePersistence:
    """Test orchestrator state save/load for crash recovery."""

    def test_importable(self):
        from cc_pipeline.state import StateManager
        assert StateManager is not None

    def test_save_state_creates_file(self, tmp_path):
        """save() creates orchestrator-state.json."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path))
        sm.save(
            run_id="test-run",
            modules={"auth": {"status": "running", "current_step": "generate"}},
        )
        assert (tmp_path / "orchestrator-state.json").exists()

    def test_load_state_returns_saved_data(self, tmp_path):
        """load() returns previously saved state."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="test-run", modules={"auth": {"status": "done"}})
        state = sm.load()
        assert state["run_id"] == "test-run"
        assert state["modules"]["auth"]["status"] == "done"

    def test_load_missing_state_returns_none(self, tmp_path):
        """load() returns None if no state file exists."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path))
        assert sm.load() is None

    def test_save_includes_timestamp(self, tmp_path):
        """Saved state includes a timestamp."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="r1", modules={})
        state = sm.load()
        assert "saved_at" in state

    def test_update_module_status(self, tmp_path):
        """update_module() updates a single module's status."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="r1", modules={"auth": {"status": "running"}})
        sm.update_module("auth", status="passed", steps_completed=3)
        state = sm.load()
        assert state["modules"]["auth"]["status"] == "passed"
        assert state["modules"]["auth"]["steps_completed"] == 3

    def test_get_failed_modules(self, tmp_path):
        """get_failed_modules() returns list of failed module names."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="r1", modules={
            "auth": {"status": "passed"},
            "payment": {"status": "failed"},
            "user": {"status": "failed"},
        })
        failed = sm.get_failed_modules()
        assert "payment" in failed
        assert "user" in failed
        assert "auth" not in failed

    def test_get_resume_point(self, tmp_path):
        """get_resume_point() returns the step to resume from."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="r1", modules={
            "auth": {"status": "failed", "current_step": "generate", "last_passed_step": "scaffold"},
        })
        resume = sm.get_resume_point("auth")
        assert resume is not None
        assert resume["last_passed_step"] == "scaffold"
