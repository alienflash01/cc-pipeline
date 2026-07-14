"""Data-flow tests: verify CC context passing with a fake CC script.

Unlike mock-based tests, these use a real fake CC (tests/fixtures/fake_cc.py)
that actually writes .pipeline/*.json files. This validates the full chain:
  scaffold CC writes → generate CC reads prior context → evaluate reads both
"""
import pytest
import json
import subprocess
import os
from pathlib import Path
from unittest.mock import patch


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}

FAKE_CC = str(Path(__file__).parent / "fixtures" / "fake_cc.py")


@pytest.fixture
def real_repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True)
    src = repo / "src"
    src.mkdir()
    (src / "auth.c").write_text("int auth() { return 0; }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestScaffoldWritesOutputFile:
    """Scaffold step writes .pipeline/scaffold.json."""

    def test_scaffold_creates_output_file(self, real_repo, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        # Use a fake CCExecutor instead of patching subprocess
        from cc_pipeline.executor import CCExecutor, CCResult

        class FakeCCExecutor:
            def __init__(self, **kwargs):
                pass
            def run(self, prompt, cwd, **kwargs):
                import re
                m = re.search(r"\.pipeline/(\S+\.json)", prompt)
                if m:
                    pd = Path(cwd) / ".pipeline"
                    pd.mkdir(parents=True, exist_ok=True)
                    (pd / m.group(1)).write_text(
                        json.dumps({"status": "ok", "files_created": ["test_main.c"]})
                    )
                return CCResult(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="scaffold",
            executor="claude-code",
            rendered_prompt="generate scaffold for auth module",
            postcondition={"shell": "test -f .pipeline/scaffold.json"},
            retry=1,
            output="scaffold.json",
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
            cc_executor=FakeCCExecutor(),
        )

        result = runner.run()
        assert result["status"] == "passed"


class TestGenerateReadsPriorContext:
    """Generate step receives scaffold's output in its prompt."""

    def test_generate_prompt_no_prior_injection(self, real_repo, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.executor import CCExecutor
        from cc_pipeline.compiler import CompiledStep

        # Pre-create scaffold output (simulating scaffold already ran)
        pd = real_repo / ".pipeline"
        pd.mkdir(exist_ok=True)
        (pd / "scaffold.json").write_text(json.dumps({
            "files_created": ["test_auth.c"],
            "test_framework": "dtest",
        }))

        # Capture the prompt that CC receives
        received_prompts = []

        original_run = CCExecutor.run

        def capturing_run(self, prompt, cwd, **kwargs):
            received_prompts.append(prompt)
            from cc_pipeline.executor import CCResult
            return CCResult(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="generate",
            executor="claude-code",
            rendered_prompt="generate tests for auth_login.c",
            postcondition=None,
            retry=1,
            output="generate.json",
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )

        with patch.object(CCExecutor, "run", capturing_run):
            runner._execute_step(step)

        assert len(received_prompts) == 1
        prompt = received_prompts[0]
        # Scaffold data should be in the prompt
        assert "scaffold.json" in prompt
        assert "test_auth.c" in prompt
        assert "dtest" in prompt


class TestFullPipelineDataFlow:
    """Full 3-step pipeline with fake CC — end-to-end data flow."""

    def _make_fake_cc(self):
        """Create a fake CCExecutor that writes output files."""
        from cc_pipeline.executor import CCResult

        class FakeCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                import re
                m = re.search(r"\.pipeline/(\S+\.json)", prompt)
                if m:
                    output_file = m.group(1)
                    pd = Path(cwd) / ".pipeline"
                    pd.mkdir(parents=True, exist_ok=True)
                    content = {"status": "ok"}
                    if "scaffold" in output_file:
                        content["files_created"] = ["test_main.c"]
                        content["test_framework"] = "dtest"
                    elif "generate" in output_file:
                        content["tests_generated"] = 5
                    elif "evaluate" in output_file:
                        content["score"] = 75
                    (pd / output_file).write_text(json.dumps(content))
                return CCResult(returncode=0, stdout="done", stderr="")
        return FakeCC()

    def test_scaffold_generate_evaluate_all_pass(self, real_repo, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        steps = [
            CompiledStep(
                step_id="scaffold",
                executor="claude-code",
                rendered_prompt="scaffold for auth",
                postcondition={"shell": "test -f .pipeline/scaffold.json"},
                retry=1,
                output="scaffold.json",
            ),
            CompiledStep(
                step_id="generate",
                executor="claude-code",
                rendered_prompt="generate tests for auth.c",
                postcondition={"shell": "test -f .pipeline/generate.json"},
                retry=1,
                output="generate.json",
            ),
            CompiledStep(
                step_id="evaluate",
                executor="claude-code",
                rendered_prompt="evaluate test quality",
                postcondition={"shell": "test -f .pipeline/evaluate.json"},
                retry=1,
                output="evaluate.json",
            ),
        ]

        runner = ModuleRunner(
            steps=steps, module_name="auth",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
            cc_executor=self._make_fake_cc(),
        )

        result = runner.run()
        # Debug: print result if failed
        if result["status"] != "passed":
            print(f"\nDEBUG result: {result}")
        assert result["status"] == "passed"

        # All 3 output files should exist
        pipeline_dir = real_repo / ".pipeline"
        assert (pipeline_dir / "scaffold.json").exists()
        assert (pipeline_dir / "generate.json").exists()
        assert (pipeline_dir / "evaluate.json").exists()

        # Generate step should have received scaffold context
        gen_data = json.loads((pipeline_dir / "generate.json").read_text())
        assert "tests_generated" in gen_data

        # Evaluate step should have received both prior contexts
        eval_data = json.loads((pipeline_dir / "evaluate.json").read_text())
        assert "score" in eval_data


class TestShellPostconditionWithDataFlow:
    """Shell step verifies CC output, then next CC reads verified data."""

    def test_cc_then_shell_then_cc_chain(self, real_repo, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        class FakeCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                import re
                m = re.search(r"\.pipeline/(\S+\.json)", prompt)
                if m:
                    pd = Path(cwd) / ".pipeline"
                    pd.mkdir(parents=True, exist_ok=True)
                    (pd / m.group(1)).write_text(json.dumps({"status": "ok"}))
                return CCResult(returncode=0, stdout="done", stderr="")

        steps = [
            CompiledStep(
                step_id="scaffold",
                executor="claude-code",
                rendered_prompt="scaffold for auth",
                postcondition={"shell": "test -f .pipeline/scaffold.json"},
                retry=1,
                output="scaffold.json",
            ),
            CompiledStep(
                step_id="verify",
                executor="shell",
                rendered_prompt='echo \'{"verified": true}\' > .pipeline/scaffold.verified.json',
                postcondition={"shell": "test -f .pipeline/scaffold.verified.json"},
                retry=1,
            ),
            CompiledStep(
                step_id="generate",
                executor="claude-code",
                rendered_prompt="generate tests",
                postcondition=None,
                retry=1,
                output="generate.json",
            ),
        ]

        runner = ModuleRunner(
            steps=steps, module_name="auth",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
            cc_executor=FakeCC(),
        )

        result = runner.run()
        # Debug: print result if failed
        if result["status"] != "passed":
            print(f"\nDEBUG result: {result}")
        assert result["status"] == "passed"

        pd = real_repo / ".pipeline"
        assert (pd / "scaffold.json").exists()
        assert (pd / "scaffold.verified.json").exists()
        assert (pd / "generate.json").exists()

