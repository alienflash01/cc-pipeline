"""TDD: Git Checkpoint + Retry/Rollback tests."""
import pytest
import subprocess
import os
from pathlib import Path


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo for checkpoint tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in [
        ["git", "init"],
        ["git", "config", "user.name", "test"],
        ["git", "config", "user.email", "test@test.com"],
    ]:
        subprocess.run(cmd, cwd=repo, capture_output=True, env=GIT_ENV)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestGitCheckpoint:
    """Test git commit + tag checkpoint mechanism."""

    def test_importable(self):
        from cc_pipeline.git_checkpoint import GitCheckpoint
        assert GitCheckpoint is not None

    def test_creates_commit_with_message(self, git_repo):
        """checkpoint() creates a git commit."""
        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(git_repo))
        (git_repo / "new_file.txt").write_text("content")
        gc.checkpoint("scaffold", module="auth", attempt=1)
        # Verify commit was made
        result = subprocess.run(
            ["git", "log", "--oneline"], cwd=git_repo,
            capture_output=True, text=True,
        )
        assert "scaffold" in result.stdout

    def test_creates_tag(self, git_repo):
        """checkpoint() creates a git tag."""
        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(git_repo))
        (git_repo / "f.txt").write_text("x")
        gc.checkpoint("generate", module="auth", attempt=1)
        # Tag format: pipeline/{module}/{step}/{attempt}
        result = subprocess.run(
            ["git", "tag", "-l", "pipeline/auth/generate/*"],
            cwd=git_repo, capture_output=True, text=True,
        )
        assert "pipeline/auth/generate/1" in result.stdout

    def test_tag_format_correct(self, git_repo):
        """Tag format is pipeline/{module}/{step}/{attempt}."""
        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(git_repo))
        (git_repo / "f.txt").write_text("x")
        gc.checkpoint("scaffold", module="payment", attempt=2)
        result = subprocess.run(
            ["git", "tag", "-l"], cwd=git_repo,
            capture_output=True, text=True,
        )
        assert "pipeline/payment/scaffold/2" in result.stdout

    def test_rollback_restores_state(self, git_repo):
        """rollback() restores files to checkpoint state."""
        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(git_repo))
        
        # Checkpoint 1: scaffold
        (git_repo / "scaffold.txt").write_text("scaffold output")
        gc.checkpoint("scaffold", module="auth", attempt=1)
        
        # Make more changes
        (git_repo / "generate.txt").write_text("generate output")
        
        # Rollback to scaffold
        gc.rollback("scaffold", module="auth", attempt=1)
        
        # scaffold.txt should still exist, generate.txt should be gone
        assert (git_repo / "scaffold.txt").exists()
        assert not (git_repo / "generate.txt").exists()

    def test_rollback_preserves_previous_checkpoint(self, git_repo):
        """Rolling back to scaffold discards generate outputs."""
        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(git_repo))
        
        (git_repo / "s.txt").write_text("scaffold")
        gc.checkpoint("scaffold", module="auth", attempt=1)
        
        (git_repo / "g1.txt").write_text("gen attempt 1")
        gc.checkpoint("generate", module="auth", attempt=1)
        
        (git_repo / "g2.txt").write_text("gen attempt 2")
        
        # Roll back to generate checkpoint → g2.txt gone, g1.txt preserved
        gc.rollback("generate", module="auth", attempt=1)
        assert (git_repo / "s.txt").exists()
        assert (git_repo / "g1.txt").exists()
        assert not (git_repo / "g2.txt").exists()

    def test_multiple_checkpoints_independent(self, git_repo):
        """Multiple checkpoints are independent."""
        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(git_repo))
        
        (git_repo / "a.txt").write_text("a")
        gc.checkpoint("scaffold", module="auth", attempt=1)
        
        (git_repo / "b.txt").write_text("b")
        gc.checkpoint("generate", module="auth", attempt=1)
        
        (git_repo / "c.txt").write_text("c")
        gc.checkpoint("generate", module="auth", attempt=2)
        
        # Roll to scaffold: only a.txt
        gc.rollback("scaffold", module="auth", attempt=1)
        assert (git_repo / "a.txt").exists()
        assert not (git_repo / "b.txt").exists()
        assert not (git_repo / "c.txt").exists()
