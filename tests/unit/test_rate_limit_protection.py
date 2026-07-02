"""TDD: Rate limit retry budget protection.

CO strategy:
1. Rate-limited retries have a max free-retry limit
2. Rate-limited retries include a sleep/backoff
3. After global stall threshold, rate-limit retries consume budget
"""
import pytest
import time
from unittest.mock import patch, MagicMock
import subprocess


class TestRateLimitBudgetProtection:
    """Rate limit retries must have limits to prevent infinite loops."""

    @patch("cc_pipeline.runner._time_mod.sleep")
    @patch("cc_pipeline.executor.subprocess.run")
    def test_max_free_rate_limit_retries(self, mock_run, mock_sleep):
        """After MAX_RATE_LIMIT_RETRIES free retries, start consuming budget."""
        from cc_pipeline.runner import ModuleRunner, MAX_FREE_RATE_LIMIT_RETRIES
        from cc_pipeline.compiler import CompiledStep

        # Always return rate limited
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="429 rate_limit_error code:1302"
        )

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="do something",
            postcondition=None,
            retry=3,
        )

        runner = ModuleRunner(
            steps=[step], module_name="test",
            worktree_path="/tmp", run_dir="/tmp/rl-test1",
        )

        result = runner.run()
        assert result["status"] == "failed"
        # Total calls = MAX_FREE_RATE_LIMIT_RETRIES (free) + step.retry (budget)
        expected_max = MAX_FREE_RATE_LIMIT_RETRIES + step.retry + 1
        assert mock_run.call_count <= expected_max

    @patch("cc_pipeline.executor.subprocess.run")
    @patch("cc_pipeline.runner._time_mod.sleep")
    def test_rate_limit_includes_backoff(self, mock_sleep, mock_run):
        """Rate-limited retry should include a sleep/backoff."""
        from cc_pipeline.runner import ModuleRunner

        call_seq = []

        def side_effect(*args, **kwargs):
            call_seq.append(1)
            if len(call_seq) <= 2:
                return MagicMock(returncode=1, stdout="", stderr="429 rate_limit")
            return MagicMock(returncode=0, stdout="done", stderr="")

        mock_run.side_effect = side_effect
        mock_sleep.return_value = None  # don't actually sleep

        from cc_pipeline.compiler import CompiledStep
        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="x", postcondition=None, retry=3,
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path="/tmp", run_dir="/tmp/rl-test2",
        )

        runner.run()
        # sleep should have been called for rate-limit backoff
        assert mock_sleep.called

    @patch("cc_pipeline.executor.subprocess.run")
    @patch("cc_pipeline.runner._time_mod.sleep")
    def test_rate_limit_backoff_uses_configured_wait(self, mock_sleep, mock_run):
        """Backoff wait time is configurable and reasonable."""
        from cc_pipeline.runner import ModuleRunner

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="429 rate_limit"
        )

        from cc_pipeline.compiler import CompiledStep
        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="x", postcondition=None, retry=1,
        )
        runner = ModuleRunner(
            steps=[step], module_name="t",
            worktree_path="/tmp", run_dir="/tmp/rl-test3",
        )

        runner.run()
        # Each sleep call should be a positive number (seconds)
        for call_args in mock_sleep.call_args_list:
            wait_seconds = call_args[0][0]
            assert wait_seconds > 0
            assert wait_seconds <= 300  # max 5 minutes per backoff
