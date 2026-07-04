"""TDD: run-before preflight check + verbose runner events + failure output.

Covers three UX improvements:
  Task 1: _preflight_check() warns on missing Claude Code CLI / repo / branch,
          always returns True (never blocks the run).
  Task 2: failed-module summary line shows the error reason + a transcript
          command hint pointing at the run directory.
  Task 3: verbose mode prints RATE LIMIT / RETRY / JUMP / FAIL events live,
          each with an [HH:MM:SS] timestamp.
"""
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
}


def _head_branch(repo) -> str:
    """Return the current branch name of a repo (master/main agnostic)."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return r.stdout.strip() or "master"


@pytest.fixture
def git_repo(tmp_path):
    """Minimal git repo with an initial commit, for runner/preflight checks."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


# --- Task 1: preflight check -------------------------------------------------

class TestPreflightCheck:
    """Run-before environment checks: warn-only, always returns True."""

    def test_function_exists(self):
        from cc_pipeline import cli
        assert hasattr(cli, "_preflight_check")
        assert callable(cli._preflight_check)

    def test_returns_true_on_healthy_env(self, git_repo, capsys):
        """Healthy repo + present CC CLI → no warnings, returns True."""
        from cc_pipeline.cli import _preflight_check
        from cc_pipeline.config import PipelineConfig

        config = PipelineConfig(repo=str(git_repo), base_branch=_head_branch(git_repo))
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            result = _preflight_check(config, MagicMock())
        assert result is True
        assert "Preflight warning" not in capsys.readouterr().err

    def test_warns_on_missing_cc_cli(self, git_repo, capsys):
        """Missing Claude Code CLI → WARNING to stderr, still returns True."""
        from cc_pipeline.cli import _preflight_check
        from cc_pipeline.config import PipelineConfig

        config = PipelineConfig(repo=str(git_repo), base_branch=_head_branch(git_repo))
        with patch("cc_pipeline.cli.shutil.which", return_value=None):
            result = _preflight_check(config, MagicMock())
        captured = capsys.readouterr()
        assert result is True
        assert "Preflight warning" in captured.err
        assert "Claude Code CLI not found" in captured.err
        assert "npm i -g @anthropic-ai/claude-code" in captured.err

    def test_warns_on_missing_branch(self, git_repo, capsys):
        """base_branch not in repo → warning, still returns True."""
        from cc_pipeline.cli import _preflight_check
        from cc_pipeline.config import PipelineConfig

        config = PipelineConfig(repo=str(git_repo), base_branch="nonexistent-branch")
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            result = _preflight_check(config, MagicMock())
        captured = capsys.readouterr()
        assert result is True
        assert "nonexistent-branch" in captured.err

    def test_warns_on_missing_repo(self, tmp_path, capsys):
        """repo dir does not exist → warning, still returns True."""
        from cc_pipeline.cli import _preflight_check
        from cc_pipeline.config import PipelineConfig

        config = PipelineConfig(repo=str(tmp_path / "no-such-repo"), base_branch="main")
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            result = _preflight_check(config, MagicMock())
        captured = capsys.readouterr()
        assert result is True
        assert "not found" in captured.err.lower()

    def test_never_blocks_run(self, git_repo, capsys):
        """Every check failing still returns True (run must continue)."""
        from cc_pipeline.cli import _preflight_check
        from cc_pipeline.config import PipelineConfig

        config = PipelineConfig(repo=str(git_repo), base_branch="nope")
        with patch("cc_pipeline.cli.shutil.which", return_value=None):
            result = _preflight_check(config, MagicMock())
        assert result is True

    def test_preflight_invoked_during_run(self, git_repo, capsys):
        """`cc-pipeline run` triggers preflight (CC CLI warning reaches stderr)."""
        from cc_pipeline.cli import main

        cfg = git_repo.parent / "config.yaml"
        cfg.write_text(f"""
repo: {git_repo}
base_branch: {_head_branch(git_repo)}
pipeline:
  - id: gen
    executor: shell
    command: "echo ok"
modules:
  - name: auth
    source_dir: src/
    source_files: [a.c]
""")
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value=None), \
             patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = []
            MockOrch.return_value.run_id = "test-run"
            main(["run", str(cfg), "--run-dir", str(runs)])
        assert "Preflight warning" in capsys.readouterr().err


# --- Task 2: failure output --------------------------------------------------

class TestFailureOutput:
    """Failed-module summary shows error reason + transcript command hint."""

    def _write_config(self, git_repo) -> Path:
        cfg = git_repo.parent / "config.yaml"
        cfg.write_text(f"""
repo: {git_repo}
base_branch: {_head_branch(git_repo)}
pipeline:
  - id: gen
    executor: shell
    command: "echo ok"
modules:
  - name: auth
    source_dir: src/
    source_files: [a.c]
""")
        return cfg

    def test_failed_module_shows_reason_and_hint(self, git_repo, capsys):
        """Failed line includes the error text and a transcript command."""
        from cc_pipeline.cli import main

        cfg = self._write_config(git_repo)
        runs = git_repo.parent / "runs"
        reason = "evaluate: score=45 < 60 (3 retries)"
        with patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = [
                {"status": "failed", "module": "auth", "error": reason}
            ]
            MockOrch.return_value.run_id = "test-run"
            ret = main(["run", str(cfg), "--run-dir", str(runs)])

        out = capsys.readouterr().out
        assert ret == 1
        assert "✗" in out
        assert "auth" in out
        assert reason in out
        # transcript troubleshooting hint pointing at the run dir + module
        assert "transcript" in out
        assert "--run-dir" in out
        assert "--module auth" in out
        assert "💡" in out

    def test_passed_module_has_no_hint(self, git_repo, capsys):
        """Passed modules do not print a transcript hint."""
        from cc_pipeline.cli import main

        cfg = self._write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = [
                {"status": "passed", "module": "auth"}
            ]
            MockOrch.return_value.run_id = "test-run"
            main(["run", str(cfg), "--run-dir", str(runs)])

        out = capsys.readouterr().out
        assert "✓" in out
        assert "💡" not in out


# --- Task 3: verbose runner events -------------------------------------------

def _step(**kw):
    from cc_pipeline.compiler import CompiledStep
    defaults = dict(
        step_id="gen", executor="claude-code",
        rendered_prompt="x", postcondition=None, retry=0,
    )
    defaults.update(kw)
    return CompiledStep(**defaults)


class TestVerboseRunnerEvents:
    """verbose mode prints RATE LIMIT / RETRY / JUMP / FAIL events live."""

    def test_verbose_rate_limit(self, git_repo, capsys):
        from cc_pipeline.runner import ModuleRunner

        with patch("cc_pipeline.executor.subprocess.run") as mock_run, \
             patch("cc_pipeline.runner._time_mod.sleep"):
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="429 rate_limit_error"
            )
            runner = ModuleRunner(
                steps=[_step(retry=0)], module_name="auth",
                worktree_path=str(git_repo),
                run_dir=str(git_repo / "runs"),
                verbose=True,
            )
            runner.run()
        out = capsys.readouterr().out
        assert "RATE LIMIT" in out
        assert re.search(r"\[\d{2}:\d{2}:\d{2}\]", out)

    def test_verbose_retry(self, git_repo, capsys):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.executor import CCResult

        class FlakyCC:
            def __init__(self):
                self.n = 0

            def run(self, prompt, cwd, allowed_tools=None, **kw):
                self.n += 1
                if self.n == 1:
                    return CCResult(returncode=1, stdout="", stderr="boom")
                return CCResult(returncode=0, stdout="done", stderr="")

        runner = ModuleRunner(
            steps=[_step(retry=1)], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=FlakyCC(),
            verbose=True,
        )
        runner.run()
        out = capsys.readouterr().out
        assert "RETRY" in out
        assert "attempt" in out

    def test_verbose_fail(self, git_repo, capsys):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.executor import CCResult

        class FailCC:
            def run(self, prompt, cwd, allowed_tools=None, **kw):
                return CCResult(returncode=1, stdout="", stderr="boom")

        runner = ModuleRunner(
            steps=[_step(retry=0)], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=FailCC(),
            verbose=True,
        )
        runner.run()
        out = capsys.readouterr().out
        assert "FAIL" in out

    def test_verbose_jump(self, git_repo, capsys):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        class FailCC:
            def run(self, prompt, cwd, allowed_tools=None, **kw):
                return CCResult(returncode=1, stdout="", stderr="boom")

        steps = [
            CompiledStep(step_id="gen", executor="claude-code",
                         rendered_prompt="x", postcondition=None, retry=0,
                         on_failure="gen"),
        ]
        runner = ModuleRunner(
            steps=steps, module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=FailCC(),
            verbose=True,
        )
        runner.run()
        out = capsys.readouterr().out
        assert "JUMP" in out
        assert "gen" in out
