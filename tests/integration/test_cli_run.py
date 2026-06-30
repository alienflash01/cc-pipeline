"""TDD: CLI run command integration tests."""
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
def cli_repo(tmp_path):
    """Real git repo for CLI integration."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True)
    src = repo / "src"
    src.mkdir()
    (src / "a.c").write_text("int f() { return 0; }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


CLI_YAML = """
repo: PLACEHOLDER
base_branch: main
concurrency: 1
max_retries: 1

pipeline:
  - id: step1
    executor: shell
    prompt: "echo hello"
    postcondition:
      shell: "echo ok"

modules:
  - name: mod_a
    spec_id: S1
    source_dir: src/
    source_files: [a.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
"""


class TestCLIRunCommand:
    """Test `cc-pipeline run` end-to-end."""

    def test_run_command_executes_pipeline(self, cli_repo, tmp_path):
        """`cc-pipeline run config.yaml` executes and returns 0 on success."""
        from cc_pipeline.cli import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CLI_YAML.replace("PLACEHOLDER", str(cli_repo)))

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 0

    def test_run_command_prints_summary(self, cli_repo, tmp_path, capsys):
        """Run command prints a summary with pass/fail counts."""
        from cc_pipeline.cli import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CLI_YAML.replace("PLACEHOLDER", str(cli_repo)))

        main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        captured = capsys.readouterr()
        assert "passed" in captured.out

    def test_run_command_single_module_filter(self, cli_repo, tmp_path):
        """--module flag filters to one module."""
        from cc_pipeline.cli import main

        yaml = CLI_YAML.replace("PLACEHOLDER", str(cli_repo))
        yaml += """
  - name: mod_b
    spec_id: S2
    source_dir: src/
    source_files: [b.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml)

        ret = main(["run", str(config_path), "--module", "mod_a", "--run-dir", str(tmp_path / "runs")])
        assert ret == 0

    def test_run_command_unknown_module_returns_error(self, cli_repo, tmp_path):
        """--module with unknown name returns 1."""
        from cc_pipeline.cli import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CLI_YAML.replace("PLACEHOLDER", str(cli_repo)))

        ret = main(["run", str(config_path), "--module", "nonexistent"])
        assert ret == 1

    def test_run_command_concurrency_override(self, cli_repo, tmp_path):
        """--concurrency overrides config."""
        from cc_pipeline.cli import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CLI_YAML.replace("PLACEHOLDER", str(cli_repo)))

        ret = main(["run", str(config_path), "--concurrency", "3", "--run-dir", str(tmp_path / "runs")])
        assert ret == 0

    def test_run_command_generates_run_id(self, cli_repo, tmp_path, capsys):
        """Run output includes a timestamp-based run_id."""
        from cc_pipeline.cli import main

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CLI_YAML.replace("PLACEHOLDER", str(cli_repo)))

        main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        captured = capsys.readouterr()
        assert "run_id" in captured.out


class TestCLIStatusCommand:
    """Test `cc-pipeline status`."""

    def test_status_no_runs(self, tmp_path, monkeypatch, capsys):
        """Status with no runs prints message."""
        from cc_pipeline.cli import main

        # Point to empty dir
        monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
        main(["status"])
        captured = capsys.readouterr()
        assert "No runs" in captured.out or "runs" in captured.out.lower()
