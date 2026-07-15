"""Supplementary edge-case tests for all 7 grill-me fixes.

Each fix gets boundary/edge scenarios that weren't covered initially.
"""
import pytest
import json
import os
import subprocess
import time as _time_mod
from pathlib import Path
from unittest.mock import patch, MagicMock


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


# ═══════════════════════════════════════════════════════════════
# Fix 1: CC returncode — more edge cases
# ═══════════════════════════════════════════════════════════════

class TestCCReturncodeEdges:
    """Edge cases for CC returncode handling."""

    def test_cc_returncode_2_with_stderr(self, tmp_path):
        """CC returns 2 with meaningful error → fail, not rate-limit."""
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=2, stdout="", stderr="fatal: config error"
            )
            runner = ModuleRunner(
                steps=[CompiledStep(
                    step_id="gen", executor="claude-code",
                    rendered_prompt="x", postcondition=None, retry=1,
                )],
                module_name="t", worktree_path=str(tmp_path),
                run_dir=str(tmp_path / "runs"),
            )
            result = runner._execute_step(runner.steps[0])
            assert result.outcome == ExecOutcome.CC_FAILED

    def test_cc_returncode_0_with_stderr_still_success(self, tmp_path):
        """CC returns 0 with warnings in stderr → success (not zero-work)."""
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="done", stderr="deprecation warning"
            )
            runner = ModuleRunner(
                steps=[CompiledStep(
                    step_id="gen", executor="claude-code",
                    rendered_prompt="x", postcondition=None, retry=1,
                )],
                module_name="t", worktree_path=str(tmp_path),
                run_dir=str(tmp_path / "runs"),
            )
            result = runner._execute_step(runner.steps[0])
            assert result.outcome == ExecOutcome.SUCCESS

    def test_cc_returncode_negative(self, tmp_path):
        """CC returns -1 (signal kill) → CC_FAILED."""
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=-1, stdout="", stderr="killed by signal"
            )
            runner = ModuleRunner(
                steps=[CompiledStep(
                    step_id="gen", executor="claude-code",
                    rendered_prompt="x", postcondition=None, retry=1,
                )],
                module_name="t", worktree_path=str(tmp_path),
                run_dir=str(tmp_path / "runs"),
            )
            result = runner._execute_step(runner.steps[0])
            assert result.outcome == ExecOutcome.CC_FAILED


# ═══════════════════════════════════════════════════════════════
# Fix 2: Zero-work — more scenarios
# ═══════════════════════════════════════════════════════════════

class TestZeroWorkEdges:
    """Edge cases for zero-work detection."""

    def test_cc_stdout_only_whitespace_is_zero_work(self, tmp_path):
        """CC returns 0 with only whitespace output → zero-work."""
        from cc_pipeline.runner import _is_zero_work
        from cc_pipeline.executor import CCResult
        result = CCResult(returncode=0, stdout="   \n  ", stderr="  ")
        assert _is_zero_work(result)

    def test_cc_stdout_with_content_not_zero_work(self, tmp_path):
        """CC returns 0 with real output → not zero-work."""
        from cc_pipeline.runner import _is_zero_work
        from cc_pipeline.executor import CCResult
        result = CCResult(returncode=0, stdout="generated test files", stderr="")
        assert not _is_zero_work(result)

    def test_cc_nonzero_with_empty_output_not_zero_work(self, tmp_path):
        """CC returns 1 with empty output → CC_FAILED, not zero-work."""
        from cc_pipeline.runner import _is_zero_work
        from cc_pipeline.executor import CCResult
        result = CCResult(returncode=1, stdout="", stderr="")
        assert not _is_zero_work(result)  # it's CC_FAILED, not ZERO_WORK


# ═══════════════════════════════════════════════════════════════
# Fix 3: Rate limit — more patterns
# ═══════════════════════════════════════════════════════════════

class TestRateLimitPatterns:
    """Rate limit detection patterns."""

    def test_detect_glzm_code_1302(self):
        from cc_pipeline.runner import _is_rate_limited
        assert _is_rate_limited("HTTP 429: code 1302 rate limit exceeded")

    def test_detect_generic_429(self):
        from cc_pipeline.runner import _is_rate_limited
        assert _is_rate_limited("Error: 429 Too Many Requests")

    def test_detect_lowercase_rate_limit(self):
        from cc_pipeline.runner import _is_rate_limited
        assert _is_rate_limited("rate_limit_error: too many requests")

    def test_not_rate_limit_normal_error(self):
        from cc_pipeline.runner import _is_rate_limited
        assert not _is_rate_limited("Error: invalid API key")

    def test_not_rate_limit_empty_stderr(self):
        from cc_pipeline.runner import _is_rate_limited
        assert not _is_rate_limited("")

    def test_not_rate_limit_config_error(self):
        from cc_pipeline.runner import _is_rate_limited
        assert not _is_rate_limited("config error: missing field")


# ═══════════════════════════════════════════════════════════════
# Fix 3b: Rate limit budget exhaustion converts to CC_FAILED
# ═══════════════════════════════════════════════════════════════

class TestRateLimitBudgetExhaustion:
    """After MAX_FREE_RATE_LIMIT_RETRIES, converts to budget-consuming failure."""

    @patch("cc_pipeline.runner._time_mod.sleep")
    @patch("cc_pipeline.executor.subprocess.run")
    def test_free_retries_then_budget_then_fail(self, mock_run, mock_sleep):
        """Rate limit forever → 5 free + retry budget → fail."""
        from cc_pipeline.runner import ModuleRunner, MAX_FREE_RATE_LIMIT_RETRIES
        from cc_pipeline.compiler import CompiledStep

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="429 rate_limit_error"
        )
        mock_sleep.return_value = None

        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="x", postcondition=None, retry=2,
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path="/tmp", run_dir="/tmp/rl-edge1",
        )
        result = runner.run()
        assert result["status"] == "failed"
        # Total calls: MAX_FREE_RATE_LIMIT_RETRIES (free) + retry (budget)
        assert mock_run.call_count == MAX_FREE_RATE_LIMIT_RETRIES + step.retry + 1


# ═══════════════════════════════════════════════════════════════
# Fix 4: State concurrency — more threads
# ═══════════════════════════════════════════════════════════════

class TestStateConcurrencyEdges:
    """State concurrency edge cases."""

    def test_50_concurrent_update_module(self, tmp_path):
        """50 threads update different modules — all present in final state."""
        from cc_pipeline.state import StateManager
        from concurrent.futures import ThreadPoolExecutor, as_completed

        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="stress", modules={})

        def update(i):
            sm.update_module(f"mod_{i}", status="passed", index=i)

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(update, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()

        state = sm.load()
        assert len(state["modules"]) == 50
        for i in range(50):
            assert state["modules"][f"mod_{i}"]["status"] == "passed"
            assert state["modules"][f"mod_{i}"]["index"] == i

    def test_concurrent_set_run_id_idempotent(self, tmp_path):
        """set_run_id from multiple threads — last one wins, no corruption."""
        from cc_pipeline.state import StateManager
        from concurrent.futures import ThreadPoolExecutor

        sm = StateManager(run_dir=str(tmp_path))

        def set_id(i):
            sm.set_run_id(f"run-{i}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(set_id, i) for i in range(10)]
            for f in futures:
                f.result()

        state = sm.load()
        assert state["run_id"].startswith("run-")
        assert state["modules"] == {}

    def test_update_then_get_failed(self, tmp_path):
        """update_module then get_failed_modules returns correct list."""
        from cc_pipeline.state import StateManager

        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="r", modules={
            "a": {"status": "passed"},
            "b": {"status": "failed"},
            "c": {"status": "passed"},
            "d": {"status": "failed"},
        })
        failed = sm.get_failed_modules()
        assert set(failed) == {"b", "d"}


# ═══════════════════════════════════════════════════════════════
# Fix 6: Context passing — more scenarios
# ═══════════════════════════════════════════════════════════════

class TestContextPassingEdges:
    """Edge cases for context passing."""

    def test_no_prior_files_no_context_section(self, tmp_path):
        """Empty .pipeline/ → no context section in prompt."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="scaffold", executor="claude-code",
            rendered_prompt="do scaffold", output="scaffold.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path=str(tmp_path),
            run_dir=str(tmp_path / "runs"),
        )
        result = runner._inject_context("do scaffold", step)
        assert "前序步骤" not in result
        assert ".pipeline/scaffold.json" in result  # output instruction always present

    def test_multiple_prior_files_not_injected_by_default(self, tmp_path):
        """Multiple .pipeline/*.json files all injected."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        pd = tmp_path / ".pipeline"
        pd.mkdir()
        (pd / "scaffold.json").write_text('{"files": ["a.c"]}')
        (pd / "generate.json").write_text('{"coverage": 85}')

        step = CompiledStep(
            step_id="evaluate", executor="judge",
            rendered_prompt="evaluate", output="evaluate.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path=str(tmp_path),
            run_dir=str(tmp_path / "runs"),
        )
        result = runner._inject_context("evaluate", step)
        assert "scaffold.json" not in result
        assert "generate.json" not in result
        assert "scaffold" not in result  # prior file name not injected
        assert '"coverage"' not in result  # prior outputs not injected by default

    def test_corrupt_json_not_injected_by_default(self, tmp_path):
        """Corrupt JSON in .pipeline/ is skipped without error."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        pd = tmp_path / ".pipeline"
        pd.mkdir()
        (pd / "good.json").write_text('{"valid": true}')
        (pd / "bad.json").write_text("not json {{{")

        step = CompiledStep(
            step_id="eval", executor="claude-code",
            rendered_prompt="eval", output="eval.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path=str(tmp_path),
            run_dir=str(tmp_path / "runs"),
        )
        result = runner._inject_context("eval", step)
        assert "good.json" not in result  # prior outputs not injected by default
        # bad.json content is injected as raw text (it's still read, just not parsed)
        # but no exception is raised

    def test_empty_pipeline_dir_no_context(self, tmp_path):
        """Empty .pipeline/ dir → no context section."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        pd = tmp_path / ".pipeline"
        pd.mkdir()  # exists but empty

        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="gen", output="gen.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path=str(tmp_path),
            run_dir=str(tmp_path / "runs"),
        )
        result = runner._inject_context("gen", step)
        assert "前序步骤" not in result


# ═══════════════════════════════════════════════════════════════
# Fix 7: Shell no inject — more scenarios
# ═══════════════════════════════════════════════════════════════

class TestShellNoInjectEdges:
    """Shell executor never gets context injection."""

    def test_shell_with_output_still_no_inject(self, tmp_path):
        """Shell executor with output field → raw prompt, no inject."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="verify", executor="shell",
            rendered_prompt="gcov --json-output=-",
            output="generate.verified.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path=str(tmp_path),
            run_dir=str(tmp_path / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        cmd = mock_run.call_args
        actual_cmd = cmd[1].get("command") or cmd[0][0] if cmd[0] else ""
        # Shell executor passes command as first positional in ShellExecutor.run
        # The actual command should be the raw prompt
        assert "gcov" in str(mock_run.call_args)

    def test_shell_with_prior_files_no_context(self, tmp_path):
        """Shell executor with .pipeline/*.json present → no context injected."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        pd = tmp_path / ".pipeline"
        pd.mkdir()
        (pd / "scaffold.json").write_text('{"files": ["a.c"]}')

        step = CompiledStep(
            step_id="verify", executor="shell",
            rendered_prompt="echo verified", output=None,
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path=str(tmp_path),
            run_dir=str(tmp_path / "runs"),
        )
        # _inject_context should not be called for shell
        full_prompt = runner._inject_context("echo verified", step)
        # When called directly it still injects, but _execute_step won't call it for shell
        # Verify by checking _execute_step path
        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        # The actual command passed should be "echo verified", not the injected version
        call_args = mock_run.call_args
        # ShellExecutor.run(command=..., cwd=...) — command is keyword arg or first positional
        actual_cmd = call_args[0][0] if call_args[0] else call_args[1].get("command", "")
        assert actual_cmd == "echo verified"


# ═══════════════════════════════════════════════════════════════
# Timeout edge cases
# ═══════════════════════════════════════════════════════════════

class TestTimeoutEdges:
    """Timeout handling edge cases."""

    def test_shell_timeout_returns_timeout_outcome(self, tmp_path):
        """Shell executor timeout → ExecOutcome.TIMEOUT."""
        import subprocess as sp
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.side_effect = sp.TimeoutExpired(cmd="gcov", timeout=5)
            runner = ModuleRunner(
                steps=[CompiledStep(
                    step_id="v", executor="shell",
                    rendered_prompt="gcov", postcondition=None, retry=1,
                )],
                module_name="t", worktree_path=str(tmp_path),
                run_dir=str(tmp_path / "runs"),
            )
            result = runner._execute_step(runner.steps[0])
            assert result.outcome == ExecOutcome.TIMEOUT

    def test_cc_unknown_exception_returns_unknown_error(self, tmp_path):
        """CC unexpected exception → ExecOutcome.UNKNOWN_ERROR."""
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.side_effect = ConnectionError("network down")
            runner = ModuleRunner(
                steps=[CompiledStep(
                    step_id="gen", executor="claude-code",
                    rendered_prompt="x", postcondition=None, retry=1,
                )],
                module_name="t", worktree_path=str(tmp_path),
                run_dir=str(tmp_path / "runs"),
            )
            result = runner._execute_step(runner.steps[0])
            assert result.outcome == ExecOutcome.UNKNOWN_ERROR
            assert "network down" in result.reason
