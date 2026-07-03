"""TDD: Shell executor uses 'command' field, not 'prompt'."""
import pytest
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module


class TestShellCommandField:
    """Shell executor reads from 'command' field, not 'prompt'."""

    def test_shell_step_uses_command_field(self):
        """When step has command set, it's used as the rendered prompt."""
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(
                id="verify",
                executor="shell",
                command="gcov src/*.c",
            )],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert "gcov src/*.c" in steps[0].rendered_prompt

    def test_command_takes_priority_over_prompt_for_shell(self):
        """If both command and prompt set for shell, command wins."""
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(
                id="verify",
                executor="shell",
                prompt="echo OLD",
                command="echo NEW",
            )],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert "NEW" in steps[0].rendered_prompt
        assert "OLD" not in steps[0].rendered_prompt

    def test_cc_executor_still_uses_prompt(self):
        """CC executor ignores command field, uses prompt."""
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(
                id="gen",
                executor="claude-code",
                prompt="generate tests for {module}",
                command="echo ignored",
            )],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert "generate tests for auth" in steps[0].rendered_prompt
        assert "ignored" not in steps[0].rendered_prompt

    def test_shell_with_only_prompt_still_works_backward_compat(self):
        """Shell executor with only prompt (no command) works for backward compat."""
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(
                id="verify",
                executor="shell",
                prompt="echo compat",  # legacy: no command field
            )],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert "echo compat" in steps[0].rendered_prompt

    def test_command_variables_rendered(self):
        """Variables in command field are rendered like prompts."""
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(
                id="verify",
                executor="shell",
                command="cd tests/{module} && echo done",
            )],
            modules=[Module(
                name="payment", source_dir="src/payment/",
                source_files=["pay.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("payment")
        assert "cd tests/payment" in steps[0].rendered_prompt
