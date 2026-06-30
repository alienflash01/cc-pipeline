"""TDD: PR creation tests."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestPRCreator:
    """Test GitHub PR creation."""

    def test_importable(self):
        from cc_pipeline.pr import PRCreator
        assert PRCreator is not None

    @patch("cc_pipeline.pr.subprocess.run")
    def test_create_pr_calls_gh(self, mock_run):
        """create() calls gh pr create."""
        from cc_pipeline.pr import PRCreator
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/owner/repo/pull/1", stderr="")
        creator = PRCreator(repo_path="/tmp/repo")
        url = creator.create(
            branch="ut-auto/auth",
            title="UT for auth",
            body="Auto-generated tests",
            labels=["auto-generated", "ut"],
        )
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "gh" in cmd[0]

    @patch("cc_pipeline.pr.subprocess.run")
    def test_create_pr_returns_url(self, mock_run):
        """create() returns the PR URL from gh output."""
        from cc_pipeline.pr import PRCreator
        mock_run.return_value = MagicMock(returncode=0, stdout="https://github.com/owner/repo/pull/42", stderr="")
        creator = PRCreator(repo_path="/tmp/repo")
        url = creator.create(branch="ut-auto/auth", title="UT for auth", body="tests")
        assert "pull/42" in url

    @patch("cc_pipeline.pr.subprocess.run")
    def test_create_pr_includes_labels(self, mock_run):
        """create() passes labels to gh."""
        from cc_pipeline.pr import PRCreator
        mock_run.return_value = MagicMock(returncode=0, stdout="url", stderr="")
        creator = PRCreator(repo_path="/tmp/repo")
        creator.create(branch="b", title="t", body="b", labels=["ut", "auto"])
        cmd = mock_run.call_args[0][0]
        assert "--label" in cmd

    @patch("cc_pipeline.pr.subprocess.run")
    def test_create_pr_handles_gh_failure(self, mock_run):
        """create() returns None when gh fails."""
        from cc_pipeline.pr import PRCreator
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        creator = PRCreator(repo_path="/tmp/repo")
        url = creator.create(branch="b", title="t", body="b")
        assert url is None

    @patch("cc_pipeline.pr.subprocess.run")
    def test_merge_branch_called_before_pr(self, mock_run):
        """merge_to_base() merges the worktree branch to base before PR."""
        from cc_pipeline.pr import PRCreator
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        creator = PRCreator(repo_path="/tmp/repo")
        creator.merge_to_base(branch="ut-auto/auth", base="personal/dev")
        # Should call git merge
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("merge" in " ".join(c) for c in calls)
