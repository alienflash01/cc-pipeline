"""TDD: Shell executor must not get context injection or output instruction."""
import pytest
from unittest.mock import patch, MagicMock


class TestShellNoInjection:
    """Shell executor uses raw prompt — no context, no output instruction."""

    def test_shell_command_not_modified(self, tmp_path):
        """Shell executor gets the original prompt, not injected version."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="verify",
            executor="shell",
            rendered_prompt="gcov src/*.c --json",
            output="generate.verified.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(tmp_path),
            run_dir=str(tmp_path / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='{"line":85}', stderr="")
            runner._execute_step(step)

        # Shell command should be exactly the original prompt
        cmd = mock_run.call_args[0][0]
        # Find the command (first positional arg for shell=True)
        actual_cmd = mock_run.call_args[0][0] if isinstance(mock_run.call_args[0][0], str) else ""
        # It should NOT contain Chinese context text
        assert "前序步骤" not in actual_cmd
        assert "请将本次执行" not in actual_cmd
        # It SHOULD be close to the original
        assert "gcov" in actual_cmd
