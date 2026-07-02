"""TDD: CC Executor tests."""
import pytest
import subprocess
from unittest.mock import MagicMock, patch, call


class TestCCExecutor:
    """Test claude-code headless executor."""

    def test_executor_importable(self):
        """CCExecutor class can be imported."""
        from cc_pipeline.executor import CCExecutor
        assert CCExecutor is not None

    def test_executor_constructs_with_defaults(self):
        """CCExecutor constructs with no default model (CC decides)."""
        from cc_pipeline.executor import CCExecutor
        ex = CCExecutor()
        assert ex.model is None  # None = CC uses its own default

    def test_executor_accepts_custom_model(self):
        """CCExecutor accepts a custom model name."""
        from cc_pipeline.executor import CCExecutor
        ex = CCExecutor(model="glm-4.6")
        assert ex.model == "glm-4.6"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_calls_claude_cli(self, mock_run):
        """run() calls `claude -p` with the prompt."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
        ex = CCExecutor()
        result = ex.run(prompt="hello world", cwd="/tmp/test")
        # Verify claude was called
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "claude" in cmd[0] or cmd[0].endswith("claude")

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_passes_prompt_as_arg(self, mock_run):
        """run() passes the prompt as a CLI argument."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        ex = CCExecutor()
        ex.run(prompt="test prompt here", cwd="/tmp")
        cmd = mock_run.call_args[0][0]
        assert "test prompt here" in cmd

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_sets_cwd(self, mock_run):
        """run() sets the working directory."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ex = CCExecutor()
        ex.run(prompt="x", cwd="/custom/path")
        assert mock_run.call_args[1].get("cwd") == "/custom/path"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_returns_result_object(self, mock_run):
        """run() returns a CCResult with stdout, returncode."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="output text", stderr="")
        ex = CCExecutor()
        result = ex.run(prompt="x", cwd="/tmp")
        assert result.returncode == 0
        assert result.stdout == "output text"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_nonzero_exit_returns_error_result(self, mock_run):
        """Non-zero exit code returns a result with returncode != 0."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        ex = CCExecutor()
        result = ex.run(prompt="x", cwd="/tmp")
        assert result.returncode == 1

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_passes_model_flag(self, mock_run):
        """run() passes --model flag when model is set."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ex = CCExecutor(model="glm-4.6")
        ex.run(prompt="x", cwd="/tmp")
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "glm-4.6"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_passes_allowed_tools(self, mock_run):
        """run() passes --allowedTools when specified."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ex = CCExecutor()
        ex.run(prompt="x", cwd="/tmp", allowed_tools=["Read", "Write", "Edit"])
        cmd = mock_run.call_args[0][0]
        assert "--allowedTools" in cmd

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_timeout_propagates(self, mock_run):
        """Timeout is passed to subprocess."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ex = CCExecutor()
        ex.run(prompt="x", cwd="/tmp", timeout=300)
        assert mock_run.call_args[1].get("timeout") == 300

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_uses_custom_claude_path(self, mock_run):
        """run() uses custom claude binary path if set."""
        from cc_pipeline.executor import CCExecutor
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ex = CCExecutor(claude_path="/usr/local/bin/claude")
        ex.run(prompt="x", cwd="/tmp")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/claude"
