"""TDD: Resume idempotency — step-level skip.

Resume reads state.json (completed_steps) to determine which steps
are already done and skips them within a module.
"""
import pytest
import subprocess, os
from pathlib import Path
from unittest.mock import patch, MagicMock

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.c").write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestStepLevelSkip:
    """Resume reads state.json to skip already-completed steps within a module."""

    def test_completed_steps_from_state_json(self, git_repo):
        """state.json records completed steps via mark_step_completed."""
        from cc_pipeline.state import StateManager

        mgr = StateManager(run_dir=str(git_repo / "runs"))
        mgr.mark_step_completed(module_name="auth", step_id="scaffold")

        completed = mgr.get_completed_steps(module_name="auth")
        assert "scaffold" in completed


class TestWorktreeFromCheckpoint:
    """Worktree restores from base_branch when resuming."""

    def test_create_worktree_from_base(self, git_repo):
        """Without checkpoint, worktree falls back to base_branch."""
        from cc_pipeline.worktree import WorktreeManager

        wt_mgr = WorktreeManager(
            repo_path=str(git_repo),
            base_branch="main",
            worktree_root=str(git_repo.parent / "wts2"),
            branch_prefix="ut-resume",
        )
        wt_path = wt_mgr.create("auth")  # no from_ref
        assert Path(wt_path).exists()
