"""TDD: prompt_file field — load prompt from external Markdown file."""
import pytest
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module


class TestPromptFile:
    """prompt_file loads prompt content from an external file."""

    def test_prompt_file_loaded(self, tmp_path):
        prompt_md = tmp_path / "generate.md"
        prompt_md.write_text("Generate tests for {module}")

        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            pipeline=[PipelineStep(
                id="gen",
                prompt=None,
                prompt_file=str(prompt_md),
                executor="claude-code",
            )],
            modules=[Module(
                name="auth", source_dir="src/", source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert "Generate tests for auth" in steps[0].rendered_prompt

    def test_prompt_takes_priority_over_prompt_file(self, tmp_path):
        prompt_md = tmp_path / "gen.md"
        prompt_md.write_text("FROM FILE")

        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            pipeline=[PipelineStep(
                id="gen",
                prompt="FROM INLINE",
                prompt_file=str(prompt_md),
                executor="claude-code",
            )],
            modules=[Module(
                name="auth", source_dir="src/", source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert "FROM INLINE" in steps[0].rendered_prompt
        assert "FROM FILE" not in steps[0].rendered_prompt

    def test_prompt_file_not_found_raises(self, tmp_path):
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            pipeline=[PipelineStep(
                id="gen",
                prompt=None,
                prompt_file=str(tmp_path / "nonexistent.md"),
                executor="claude-code",
            )],
            modules=[Module(
                name="auth", source_dir="src/", source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        with pytest.raises(FileNotFoundError):
            compiler.compile_module("auth")

    def test_prompt_file_supports_variables(self, tmp_path):
        prompt_md = tmp_path / "gen.md"
        prompt_md.write_text("Module: {module}, Dir: {source_dir}")

        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            pipeline=[PipelineStep(
                id="gen",
                prompt=None,
                prompt_file=str(prompt_md),
                executor="claude-code",
            )],
            modules=[Module(
                name="payment", source_dir="src/payment/", source_files=["pay.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("payment")
        assert "payment" in steps[0].rendered_prompt
        assert "src/payment/" in steps[0].rendered_prompt

    def test_prompt_file_markdown_formatting_preserved(self, tmp_path):
        prompt_md = tmp_path / "gen.md"
        prompt_md.write_text("# Generate Tests\n\n## Requirements\n- Edge cases\n```python\nimport pytest\n```")

        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            pipeline=[PipelineStep(
                id="gen",
                prompt=None,
                prompt_file=str(prompt_md),
                executor="claude-code",
            )],
            modules=[Module(
                name="auth", source_dir="src/", source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert "Generate Tests" in steps[0].rendered_prompt
        assert "pytest" in steps[0].rendered_prompt
