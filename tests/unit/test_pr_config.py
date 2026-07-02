"""TDD: PR labels and title from config, not hardcoded."""
import pytest
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module


class TestPRConfig:
    """PR metadata comes from PipelineConfig, not hardcoded."""

    def test_default_pr_labels_when_not_set(self):
        """When pr_labels not set, defaults to empty list (no labels)."""
        config = PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(
                id="gen", executor="claude-code",
                prompt="test",
            )],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=["a.c"],
                coverage={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        assert config.pr_labels == []
        assert config.pr_title_template == ""

    def test_pr_labels_from_config(self):
        """pr_labels and pr_title_template are read from config."""
        config = PipelineConfig(
            repo="/tmp",
            pr_labels=["review", "auto"],
            pr_title_template="Review for {module}",
            pipeline=[PipelineStep(
                id="gen", executor="claude-code",
                prompt="test",
            )],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=["a.c"],
                coverage={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        assert config.pr_labels == ["review", "auto"]
        assert config.pr_title_template == "Review for {module}"

    def test_orchestrator_uses_config_pr_labels(self, tmp_path):
        """Orchestrator reads pr_labels and pr_title from config."""
        from cc_pipeline.orchestrator import Orchestrator

        config = PipelineConfig(
            repo=str(tmp_path),
            pr_labels=["code-review", "auto"],
            pr_title_template="Review: {module}",
            pipeline=[PipelineStep(
                id="gen", executor="shell",
                command="echo ok",
                postcondition={"shell": "true"},
            )],
            modules=[Module(
                name="auth", spec_id="S1",
                source_dir="src/", source_files=["a.c"],
                coverage={"line_threshold": 80, "branch_threshold": 70},
            )],
        )

        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))
        # Verify config values are accessible
        assert orch.config.pr_labels == ["code-review", "auto"]
        assert orch.config.pr_title_template == "Review: {module}"

    def test_orchestrator_pr_title_renders_variables(self, tmp_path):
        """PR title template renders {module} variable."""
        import subprocess, os

        # Create a real git repo
        repo = tmp_path / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        (repo / "src").mkdir()
        (repo / "src" / "a.c").write_text("int f() { return 0; }")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=env)

        from unittest.mock import patch, MagicMock
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        config = PipelineConfig(
            repo=str(repo),
            pr_labels=["review"],
            pr_title_template="Code Review: {module} (spec: {spec_id})",
            pipeline=[PipelineStep(
                id="gen", executor="shell",
                command="echo ok",
                postcondition={"shell": "true"},
            )],
            modules=[Module(
                name="auth", spec_id="SPEC-R1",
                source_dir="src/", source_files=["a.c"],
                coverage={"line_threshold": 80, "branch_threshold": 70},
            )],
        )

        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        # Mock PRCreator.create to capture what it receives
        created_prs = []
        def mock_create(branch, title, body, labels):
            created_prs.append({"title": title, "labels": labels})
            return None  # return None to not set pr_url

        with patch("cc_pipeline.pr.PRCreator") as mock_pr_class:
            mock_pr_class.return_value.create = mock_create
            orch.run()

        assert len(created_prs) == 1
        assert created_prs[0]["title"] == "Code Review: auth (spec: SPEC-R1)"
        assert created_prs[0]["labels"] == ["review"]
