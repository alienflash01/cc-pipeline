"""TDD: Rollback to last successful checkpoint, not hardcoded attempt=1."""
import pytest
import subprocess
import os
from pathlib import Path
from unittest.mock import patch, MagicMock


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestRollbackToLastSuccess:
    """Rollback must target the last successful checkpoint, not attempt=1."""

    def test_rollback_targets_last_successful_attempt(self, git_repo):
        """If scaffold passed on attempt 3, rollback should go to scaffold/3."""
        from cc_pipeline.git_checkpoint import GitCheckpoint

        gc = GitCheckpoint(str(git_repo))

        # Simulate: scaffold attempt 1, 2, 3 — only 3 creates a real checkpoint
        for attempt in [1, 2, 3]:
            (git_repo / f"scaffold_{attempt}.txt").write_text(f"attempt {attempt}")
            gc.checkpoint("scaffold", "auth", attempt)

        # Verify all 3 tags exist
        result = subprocess.run(
            ["git", "tag", "-l", "pipeline/auth/scaffold/*"],
            cwd=git_repo, capture_output=True, text=True,
        )
        tags = result.stdout.strip().split("\n")
        assert len(tags) == 3

        # Find the latest checkpoint tag
        latest = gc.find_latest_checkpoint("scaffold", "auth")
        assert latest == "pipeline/auth/scaffold/3"

    def test_rollback_to_latest_checkpoint_preserves_correct_files(self, git_repo):
        """Rollback to latest checkpoint keeps the right state."""
        from cc_pipeline.git_checkpoint import GitCheckpoint

        gc = GitCheckpoint(str(git_repo))

        # scaffold/1
        (git_repo / "v1.txt").write_text("v1")
        gc.checkpoint("scaffold", "auth", 1)

        # scaffold/2
        (git_repo / "v2.txt").write_text("v2")
        gc.checkpoint("scaffold", "auth", 2)

        # generate changes (to be rolled back)
        (git_repo / "gen.txt").write_text("gen")
        gc.checkpoint("generate", "auth", 1)

        # Rollback generate → should go to generate/1 (latest generate checkpoint)
        gc.rollback("generate", "auth", 1)
        assert (git_repo / "v2.txt").exists()
        assert (git_repo / "gen.txt").exists()  # generate/1 includes gen.txt

        # But if we rollback to latest scaffold checkpoint (not generate):
        gc.rollback_to_latest("scaffold", "auth")
        assert (git_repo / "v2.txt").exists()  # scaffold/2
        assert not (git_repo / "gen.txt").exists()  # generate stuff gone

    def test_find_latest_returns_none_if_no_checkpoint(self, git_repo):
        """find_latest_checkpoint returns None if no tags exist."""
        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(git_repo))
        assert gc.find_latest_checkpoint("nonexistent", "auth") is None
