"""TDD: CC execution error handling — CO-style layered strategy.

Tests for _execute_step error classification and handling:
1. CC returncode != 0 → immediate step failure (skip postcondition)
2. CC zero-work detection (< 1s, no output) → immediate step failure
3. Rate limit (429) → don't consume retry budget
4. Timeout → consume retry budget
5. Normal CC success → proceed to postcondition
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestCCExecutionFailure:
    """Test that CC execution failures are detected and handled."""

    @patch("cc_pipeline.executor.subprocess.run")
    def test_cc_nonzero_returncode_skips_postcondition(self, mock_run):
        """CC returns non-zero → step fails immediately, postcondition not run."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.postcondition import PostconditionResult

        # CC returns error
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="API error")

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="do something",
            postcondition={"shell": "echo ok"},
            retry=1,
        )

        runner = ModuleRunner(
            steps=[step], module_name="test",
            worktree_path="/tmp", run_dir="/tmp/test-runs",
        )

        # Track if postcondition was called
        pc_called = []
        def track_pc(step):
            pc_called.append(True)
            return PostconditionResult(passed=True)
        runner._check_postcondition = track_pc

        result = runner.run()
        assert result["status"] == "failed"
        assert len(pc_called) == 0  # postcondition should NOT run

    @patch("cc_pipeline.executor.subprocess.run")
    def test_cc_success_proceeds_to_postcondition(self, mock_run):
        """CC returns 0 → postcondition runs normally."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.postcondition import PostconditionResult

        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="do something",
            postcondition={"shell": "echo ok"},
            retry=1,
        )

        runner = ModuleRunner(
            steps=[step], module_name="test",
            worktree_path="/tmp", run_dir="/tmp/test-runs2",
        )

        pc_called = []
        def track_pc(step):
            pc_called.append(True)
            return PostconditionResult(passed=True)
        runner._check_postcondition = track_pc

        result = runner.run()
        assert result["status"] == "passed"
        assert len(pc_called) == 1  # postcondition DID run


class TestCCZeroWorkDetection:
    """Detect CC that exits without doing meaningful work."""

    @patch("cc_pipeline.executor.subprocess.run")
    def test_cc_empty_output_flagged_as_zero_work(self, mock_run):
        """CC returns 0 but with empty stdout and very fast → zero work."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.postcondition import PostconditionResult

        # CC returns 0 but no meaningful output
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="do something",
            postcondition={"shell": "echo ok"},
            retry=2,
        )

        runner = ModuleRunner(
            steps=[step], module_name="test",
            worktree_path="/tmp", run_dir="/tmp/test-runs3",
        )
        runner._check_postcondition = lambda s: PostconditionResult(passed=True)

        result = runner.run()
        # Zero-work should still fail (CC didn't actually do anything)
        # Even though postcondition would pass, we detect CC produced nothing
        assert result["status"] == "failed"


class TestRateLimitHandling:
    """Rate limit errors should not consume retry budget."""

    @patch("cc_pipeline.runner._time_mod.sleep")
    @patch("cc_pipeline.executor.subprocess.run")
    def test_rate_limit_does_not_consume_retry(self, mock_run, mock_sleep):
        """429 error → retry without consuming budget."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.postcondition import PostconditionResult

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                # Simulate 429 rate limit in stderr
                return MagicMock(returncode=1, stdout="", stderr="429 rate_limit_error")
            return MagicMock(returncode=0, stdout="done", stderr="")

        mock_run.side_effect = side_effect

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="do something",
            postcondition=None,  # no postcondition, rely on CC returncode
            retry=2,
        )

        runner = ModuleRunner(
            steps=[step], module_name="test",
            worktree_path="/tmp", run_dir="/tmp/test-runs4",
        )

        result = runner.run()
        # Rate limited twice, then succeeded — should pass
        # retry budget was 2, rate-limit retries don't count
        assert result["status"] == "passed"


class TestTimeoutHandling:
    """Timeout errors should consume retry budget."""

    @patch("cc_pipeline.executor.subprocess.run")
    def test_timeout_consumes_retry_budget(self, mock_run):
        """Timeout → retry, consumes budget."""
        import subprocess as sp
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        # Always timeout
        mock_run.side_effect = sp.TimeoutExpired(cmd="claude", timeout=10)

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="do something",
            postcondition=None,
            retry=2,
        )

        runner = ModuleRunner(
            steps=[step], module_name="test",
            worktree_path="/tmp", run_dir="/tmp/test-runs5",
        )

        result = runner.run()
        assert result["status"] == "failed"
        # Should have tried exactly retry=2 times
        assert mock_run.call_count == 2
