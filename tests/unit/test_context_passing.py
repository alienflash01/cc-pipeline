"""TDD: CC context passing — .pipeline/ output files + prompt injection."""
import pytest
import json
import os
import subprocess
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
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestPipelineDirCreation:
    """runner must create .pipeline/ before CC executes."""

    def test_pipeline_dir_created_before_cc(self, git_repo):
        """_execute_step creates .pipeline/ directory in worktree."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="scaffold",
            executor="claude-code",
            rendered_prompt="do scaffold",
            output="scaffold.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        assert (git_repo / ".pipeline").exists()
        assert (git_repo / ".pipeline").is_dir()


class TestOutputInstructionInjection:
    """When step has output, runner appends write instruction to prompt."""

    def test_prompt_contains_output_instruction(self, git_repo):
        """CC prompt includes instruction to write .pipeline/{output}."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="scaffold",
            executor="claude-code",
            rendered_prompt="generate scaffold for auth",
            output="scaffold.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        actual_prompt = ""
        # Find the CC call (not git status) in call_args_list
        for call in mock_run.call_args_list:
            cmd = call[0][0] if call[0] else []
            if isinstance(cmd, list) and "-p" in cmd:
                prompt_idx = cmd.index("-p") + 1
                actual_prompt = cmd[prompt_idx] if prompt_idx < len(cmd) else ""
                break

        assert ".pipeline/scaffold.json" in actual_prompt
        assert "JSON" in actual_prompt or "json" in actual_prompt.lower()

    def test_no_output_no_instruction(self, git_repo):
        """Step without output field doesn't get write instruction."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="analyze",
            executor="claude-code",
            rendered_prompt="just analyze",
            output=None,
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        cmd_list = mock_run.call_args[0][0]
        prompt_idx = cmd_list.index("-p") + 1 if "-p" in cmd_list else -1
        actual_prompt = cmd_list[prompt_idx] if prompt_idx > 0 else ""

        assert ".pipeline/" not in actual_prompt


class TestContextInjectionFromPriorSteps:
    """Step N's prompt includes info about prior step outputs."""

    def test_prior_outputs_not_injected_by_default(self, git_repo):
        """If .pipeline/scaffold.json exists, next step prompt mentions it."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        # Simulate prior step wrote a file
        pipeline_dir = git_repo / ".pipeline"
        pipeline_dir.mkdir(exist_ok=True)
        (pipeline_dir / "scaffold.json").write_text(
            json.dumps({"files_created": ["test_auth.c"]})
        )

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="generate tests for auth",
            output="generate.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        actual_prompt = ""
        for call in mock_run.call_args_list:
            cmd = call[0][0] if call[0] else []
            if isinstance(cmd, list) and "-p" in cmd:
                prompt_idx = cmd.index("-p") + 1
                actual_prompt = cmd[prompt_idx] if prompt_idx < len(cmd) else ""
                break

        # Prompt should mention the prior output file
        assert "scaffold.json" in actual_prompt
        # prior outputs only injected on rerun, not by default


class TestShellExecutorPipelineDir:
    """Shell executor also gets .pipeline/ dir."""

    def test_shell_step_creates_pipeline_dir(self, git_repo):
        """Shell executor also creates .pipeline/ before running."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="verify",
            executor="shell",
            rendered_prompt="echo verified",
            output="generate.verified.json",
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        assert (git_repo / ".pipeline").exists()
