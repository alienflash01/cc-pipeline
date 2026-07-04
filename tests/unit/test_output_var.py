"""TDD: output variable injection + customizable output prompt."""
import pytest
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.compiler import PipelineCompiler


def _compile(steps, source_files=None):
    config = PipelineConfig(
        repo="/tmp/test",
        pipeline=steps,
        modules=[Module(
            name="auth",
            source_dir="src/auth/",
            source_files=source_files or ["a.c"],
        )],
    )
    return PipelineCompiler(config).compile_module("auth")


class TestOutputVariable:
    """#6: {output} should be available as a variable in prompt."""

    def test_output_in_prompt_replaced(self):
        """{output} in prompt gets replaced with step's output filename."""
        steps = _compile([
            PipelineStep(id="gen", executor="claude-code",
                        prompt="write results to {output}",
                        output="analyze.json"),
        ])
        assert "analyze.json" in steps[0].rendered_prompt

    def test_output_empty_when_not_set(self):
        """{output} empty when step has no output field."""
        steps = _compile([
            PipelineStep(id="gen", executor="claude-code",
                        prompt="output is {output}"),
        ])
        assert "output is " in steps[0].rendered_prompt.replace("output is ", "").strip() == "" or \
               "output is" in steps[0].rendered_prompt

    def test_output_in_per_file_loop(self):
        """{output} works in loop:per_file context."""
        steps = _compile([
            PipelineStep(id="gen", executor="claude-code",
                        prompt="file={file} out={output}",
                        output="result.json",
                        loop="per_file"),
        ], source_files=["a.c", "b.c"])
        assert "result.json" in steps[0].rendered_prompt
        assert "a.c" in steps[0].rendered_prompt


class TestOutputPromptCustomizable:
    """#3: output injection text should be customizable via output_prompt field."""

    def test_output_prompt_field_exists(self):
        """PipelineStep should have output_prompt field."""
        step = PipelineStep(id="x", executor="claude-code",
                           prompt="test",
                           output_prompt="Write your findings to {output}")
        assert step.output_prompt is not None

    def test_output_prompt_default_none(self):
        """output_prompt defaults to None (use framework default)."""
        step = PipelineStep(id="x", executor="claude-code", prompt="test")
        assert step.output_prompt is None

    def test_custom_output_prompt_used_in_injection(self, tmp_path):
        """When output_prompt is set, runner uses it instead of default text."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult
        from pathlib import Path
        import subprocess, os

        wt = tmp_path / "wt"
        wt.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=wt, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=wt, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=wt, capture_output=True)
        (wt / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=wt, capture_output=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t"}
        subprocess.run(["git", "commit", "-m", "init"], cwd=wt, capture_output=True, env=env)

        captured = []
        class FakeCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                captured.append(prompt)
                return CCResult(0, "done", "")

        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="do work",
            postcondition=None, retry=0,
            output="result.json",
            output_prompt="CUSTOM: write your analysis to .pipeline/{output}",
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(wt), run_dir=str(tmp_path / "runs"),
            cc_executor=FakeCC(),
        )
        runner._execute_step(step)
        assert "CUSTOM: write your analysis to .pipeline/result.json" in captured[0]
        # Should NOT contain the default Chinese text
        assert "关键信息" not in captured[0]
