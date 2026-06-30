"""TDD: Shell Executor + Postcondition Evaluator tests."""
import pytest
import json
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── Shell Executor ──

class TestShellExecutor:
    """Test deterministic shell command execution."""

    def test_importable(self):
        from cc_pipeline.executor import ShellExecutor
        assert ShellExecutor is not None

    @patch("cc_pipeline.executor.subprocess.run")
    def test_runs_command_in_cwd(self, mock_run):
        """Command runs in the specified cwd."""
        from cc_pipeline.executor import ShellExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout='{"line": 85}', stderr="")
        ex = ShellExecutor()
        result = ex.run(command="echo test", cwd="/custom")
        assert mock_run.call_args[1].get("cwd") == "/custom"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_returns_shell_result(self, mock_run):
        """Returns ShellResult with stdout as JSON."""
        from cc_pipeline.executor import ShellExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout='{"line": 85}', stderr="")
        ex = ShellExecutor()
        result = ex.run(command="gcov x", cwd="/tmp")
        assert result.returncode == 0
        assert result.stdout == '{"line": 85}'

    @patch("cc_pipeline.executor.subprocess.run")
    def test_nonzero_exit_captured(self, mock_run):
        """Non-zero exit is captured, not raised."""
        from cc_pipeline.executor import ShellExecutor
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        ex = ShellExecutor()
        result = ex.run(command="false", cwd="/tmp")
        assert result.returncode == 1

    @patch("cc_pipeline.executor.subprocess.run")
    def test_timeout_propagated(self, mock_run):
        """Timeout is passed through."""
        from cc_pipeline.executor import ShellExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ex = ShellExecutor()
        ex.run(command="x", cwd="/tmp", timeout=60)
        assert mock_run.call_args[1].get("timeout") == 60


# ── Postcondition Evaluator ──

class TestPostconditionEvaluator:
    """Test postcondition shell + expect expression evaluation."""

    def test_importable(self):
        from cc_pipeline.postcondition import evaluate
        assert evaluate is not None

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_simple_ge_passes(self, mock_run):
        """$.line >= 80 passes when line=85."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout='{"line": 85}', stderr="")
        result = evaluate(
            shell="gcov --json",
            expect="$.line >= 80",
            cwd="/tmp",
        )
        assert result.passed is True

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_simple_ge_fails(self, mock_run):
        """$.line >= 80 fails when line=70."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout='{"line": 70}', stderr="")
        result = evaluate(
            shell="gcov --json",
            expect="$.line >= 80",
            cwd="/tmp",
        )
        assert result.passed is False

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_and_expression_passes(self, mock_run):
        """$.line >= 80 AND $.branch >= 70 both pass."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout='{"line": 85, "branch": 75}', stderr="")
        result = evaluate(
            shell="gcov",
            expect="$.line >= 80 && $.branch >= 70",
            cwd="/tmp",
        )
        assert result.passed is True

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_and_expression_fails_one(self, mock_run):
        """AND fails when only one condition is met."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout='{"line": 85, "branch": 60}', stderr="")
        result = evaluate(
            shell="gcov",
            expect="$.line >= 80 && $.branch >= 70",
            cwd="/tmp",
        )
        assert result.passed is False

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_equality_check(self, mock_run):
        """$.errors == 0 passes."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout='{"errors": 0}', stderr="")
        result = evaluate(
            shell="lint",
            expect="$.errors == 0",
            cwd="/tmp",
        )
        assert result.passed is True

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_shell_nonzero_exit_fails(self, mock_run):
        """Shell command failure → postcondition fails."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        result = evaluate(
            shell="false",
            expect="$.line >= 80",
            cwd="/tmp",
        )
        assert result.passed is False

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_invalid_json_fails(self, mock_run):
        """Non-JSON stdout → postcondition fails."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout="not json at all", stderr="")
        result = evaluate(
            shell="bad",
            expect="$.line >= 80",
            cwd="/tmp",
        )
        assert result.passed is False

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_contains_check_passes(self, mock_run):
        """contains('passed') matches stdout."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout="3 tests passed", stderr="")
        result = evaluate(
            shell="pytest",
            expect="contains('passed')",
            cwd="/tmp",
        )
        assert result.passed is True

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_contains_check_fails(self, mock_run):
        """contains('passed') fails when text not present."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout="3 tests failed", stderr="")
        result = evaluate(
            shell="pytest",
            expect="contains('passed')",
            cwd="/tmp",
        )
        assert result.passed is False

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_no_expect_always_passes(self, mock_run):
        """If no expect specified, passes if shell exits 0."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = evaluate(
            shell="echo ok",
            expect=None,
            cwd="/tmp",
        )
        assert result.passed is True

    @patch("cc_pipeline.postcondition.subprocess.run")
    def test_result_includes_stdout(self, mock_run):
        """Result includes raw stdout for debugging."""
        from cc_pipeline.postcondition import evaluate
        mock_run.return_value = MagicMock(returncode=0, stdout='{"line": 90}', stderr="")
        result = evaluate(
            shell="gcov",
            expect="$.line >= 80",
            cwd="/tmp",
        )
        assert result.stdout == '{"line": 90}'
