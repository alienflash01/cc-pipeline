"""TDD: Progress file injection — Anthropic harness best practice.

After each step, runner appends a progress entry to .pipeline/progress.md.
Next CC sees this file in its prompt via existing context injection.
"""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def git_repo(tmp_path):
    import subprocess, os
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=env)
    return repo


class TestProgressFileCreation:
    """Runner creates .pipeline/progress.md after each successful step."""

    def test_progress_file_created_after_first_step(self, git_repo):
        """After scaffold step passes, .pipeline/progress.md exists."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        class FakeCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                return CCResult(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="scaffold", executor="claude-code",
            rendered_prompt="scaffold for auth", postcondition=None,
            retry=1, output="scaffold.json",
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=FakeCC(),
        )
        result = runner.run()
        assert result["status"] == "passed"

        progress_file = git_repo / ".pipeline" / "progress.md"
        assert progress_file.exists()
        content = progress_file.read_text()
        assert "scaffold" in content
        assert "passed" in content or "PASS" in content


class TestProgressFileContent:
    """Progress file has structured content for next CC to read."""

    def test_progress_contains_step_id_and_status(self, git_repo):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        class FakeCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                return CCResult(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="generate", executor="claude-code",
            rendered_prompt="generate tests", postcondition=None,
            retry=1, output="generate.json",
        )

        runner = ModuleRunner(
            steps=[step], module_name="payment",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=FakeCC(),
        )
        runner.run()

        content = (git_repo / ".pipeline" / "progress.md").read_text()
        assert "generate" in content
        assert "payment" in content  # module name


class TestProgressFileAccumulates:
    """Multiple steps append to the same progress file."""

    def test_two_steps_both_in_progress(self, git_repo):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        class FakeCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
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
                retry=1, output="generate.json",
            ),
        ]

        runner = ModuleRunner(
            steps=steps, module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=FakeCC(),
        )
        runner.run()

        content = (git_repo / ".pipeline" / "progress.md").read_text()
        assert "scaffold" in content
        assert "generate" in content


class TestProgressVisibleToNextCC:
    """Progress.md content is injected into next CC's prompt."""

    def test_progress_not_injected_by_default(self, git_repo):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        # Pre-create a progress file
        pd = git_repo / ".pipeline"
        pd.mkdir(exist_ok=True)
        (pd / "progress.md").write_text(
            "## Progress\n- [PASS] scaffold: created test files\n"
        )

        received_prompts = []

        class CaptureCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                received_prompts.append(prompt)
                return CCResult(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="generate", executor="claude-code",
            rendered_prompt="generate tests", postcondition=None,
            retry=1, output="generate.json",
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=CaptureCC(),
        )
        runner._execute_step(step)

        assert len(received_prompts) == 1
        prompt = received_prompts[0]
        assert "progress.md" not in prompt  # not injected by default
        assert "scaffold" not in prompt  # prior progress not injected by default visible
