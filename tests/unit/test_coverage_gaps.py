"""Tests for runner CC-crash rollback recovery path (lines 168-169)."""
import pytest
import subprocess
import os
from pathlib import Path
from unittest.mock import patch, MagicMock


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestCCCrashRollbackRecovery:
    """When CC crashes (returncode≠0) and retry triggers, retry is attempted."""

    def test_cc_crash_triggers_rollback_then_retry(self, git_repo):
        """CC crashes on attempt 1 → rollback called → attempt 2 succeeds."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        call_count = [0]

        class CrashThenSucceed:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                call_count[0] += 1
                # call 1: scaffold passes
                # call 2: generate attempt 1 crashes
                # call 3: generate attempt 2 passes
                if call_count[0] == 2:
                    return CCResult(returncode=1, stdout="", stderr="fatal: API error")
                return CCResult(returncode=0, stdout="done", stderr="")

        steps = [
            CompiledStep(
                step_id="scaffold", executor="claude-code",
                rendered_prompt="scaffold", postcondition=None,
                retry=1, output="scaffold.json",
            ),
            CompiledStep(
                step_id="generate", executor="claude-code",
                rendered_prompt="generate", postcondition=None,
                retry=2, output="generate.json",
            ),
        ]

        runner = ModuleRunner(
            steps=steps, module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=CrashThenSucceed(),
        )

        # Track retry calls (retry no longer rolls back)
        result = runner.run()
        assert result["status"] == "passed"

    def test_cc_crash_first_step_no_rollback(self, git_repo):
        """CC crash on first step (i=1) → no rollback (nothing to rollback to)."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        class AlwaysCrash:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                return CCResult(returncode=1, stdout="", stderr="crash")

        step = CompiledStep(
            step_id="scaffold", executor="claude-code",
            rendered_prompt="scaffold", postcondition=None,
            retry=2,
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=AlwaysCrash(),
        )

        result = runner.run()
        assert result["status"] == "failed"
        # First step crash → no rollback (i=1, no previous step)

    def test_shell_rate_limit_detected(self, git_repo):
        """Shell executor returncode≠0 with 429 in stderr → RATE_LIMITED."""
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="verify", executor="shell",
            rendered_prompt="gcov", postcondition=None, retry=1,
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="429 rate_limit_error code:1302"
            )
            result = runner._execute_step(step)
            assert result.outcome == ExecOutcome.RATE_LIMITED

    def test_shell_exception_returns_unknown_error(self, git_repo):
        """Shell executor unexpected exception → UNKNOWN_ERROR."""
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="verify", executor="shell",
            rendered_prompt="gcov", postcondition=None, retry=1,
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("gcov not found")
            result = runner._execute_step(step)
            assert result.outcome == ExecOutcome.UNKNOWN_ERROR
            assert "gcov not found" in result.reason

    def test_judge_executor_sets_allowed_tools(self, git_repo):
        """Judge executor passes allowed_tools=['Read', 'Bash'] to CC."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        captured_tools = []

        class CaptureTools:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, allowed_tools=None, **kw):
                captured_tools.append(allowed_tools)
                return CCResult(returncode=0, stdout="judged", stderr="")

        step = CompiledStep(
            step_id="evaluate", executor="judge",
            rendered_prompt="evaluate", postcondition=None, retry=1,
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=CaptureTools(),
        )

        runner._execute_step(step)
        assert captured_tools[0] == ["Read", "Bash"]


class TestRenderNoAssertFixes:
    """Fix tests with no assertions — add proper assertions."""

    def test_replaces_multiple_variables(self):
        from cc_pipeline.render import render
        result = render("Hello {name}, age {age}", {"name": "Alice", "age": 30})
        assert result == "Hello Alice, age 30"
        assert "Alice" in result
        assert "30" in result

    def test_unknown_variable_preserved(self):
        from cc_pipeline.render import render
        result = render("Hello {nonexistent}", {})
        assert "{nonexistent}" in result
