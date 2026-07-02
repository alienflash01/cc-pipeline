"""TDD: Resume idempotency — step-level skip + worktree from checkpoint.

Git tags (pipeline/{module}/{step}/{attempt}) serve as event history.
Resume reads tags to determine completed steps and restores worktree
from the latest checkpoint instead of base_branch.
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
    """Resume reads git tags to skip already-completed steps within a module."""

    def test_find_completed_steps_from_tags(self, git_repo):
        """GitCheckpoint can list completed steps for a module from tags."""
        from cc_pipeline.git_checkpoint import GitCheckpoint

        gc = GitCheckpoint(repo_path=str(git_repo))

        # Simulate scaffold and generate passed (create tags)
        gc.checkpoint(step="scaffold", module="auth", attempt=1)
        gc.checkpoint(step="generate", module="auth", attempt=1)

        completed = gc.list_completed_steps(module="auth")
        assert "scaffold" in completed
        assert "generate" in completed

    def test_no_tags_returns_empty(self, git_repo):
        """No git tags → no completed steps."""
        from cc_pipeline.git_checkpoint import GitCheckpoint

        gc = GitCheckpoint(repo_path=str(git_repo))
        completed = gc.list_completed_steps(module="auth")
        assert completed == []

    def test_other_module_tags_ignored(self, git_repo):
        """Tags from other modules don't affect this module's step list."""
        from cc_pipeline.git_checkpoint import GitCheckpoint

        gc = GitCheckpoint(repo_path=str(git_repo))
        gc.checkpoint(step="scaffold", module="payment", attempt=1)
        gc.checkpoint(step="generate", module="payment", attempt=1)

        completed = gc.list_completed_steps(module="auth")
        assert completed == []


class TestWorktreeFromCheckpoint:
    """Worktree restores from latest git checkpoint, not base_branch."""

    def test_create_worktree_from_checkpoint(self, git_repo):
        """When resume=True, worktree is created from latest checkpoint tag."""
        from cc_pipeline.worktree import WorktreeManager
        from cc_pipeline.git_checkpoint import GitCheckpoint

        # Create a checkpoint with actual file changes
        gc = GitCheckpoint(repo_path=str(git_repo))
        (git_repo / "src" / "test.c").write_text("test code")
        subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add test"], cwd=git_repo, capture_output=True, env=GIT_ENV)
        gc.checkpoint(step="scaffold", module="auth", attempt=1)

        # Get checkpoint tag
        tag = gc.find_latest_checkpoint(step="scaffold", module="auth")
        assert tag is not None

        # Create worktree from that tag
        wt_mgr = WorktreeManager(
            repo_path=str(git_repo),
            base_branch="main",
            worktree_root=str(git_repo.parent / "wts"),
            branch_prefix="ut-resume",
        )
        wt_path = wt_mgr.create("auth", from_ref=tag)
        assert Path(wt_path).exists()
        # The file created before checkpoint should exist in worktree
        assert (Path(wt_path) / "src" / "test.c").exists()

    def test_create_worktree_from_base_when_no_checkpoint(self, git_repo):
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
