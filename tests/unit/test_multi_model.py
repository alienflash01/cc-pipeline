"""TDD: Multi-model support — per-step model with fallback chain.

Priority: step.model > --model arg > config.model > None (CC default)
"""
import pytest
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module


class TestStepModelField:
    """PipelineStep has optional model field."""

    def test_step_has_model_field(self):
        step = PipelineStep(id="x", executor="claude-code", model="deepseek-v4-pro")
        assert step.model == "deepseek-v4-pro"

    def test_step_model_defaults_empty(self):
        step = PipelineStep(id="x", executor="claude-code")
        assert step.model == ""

    def test_config_has_model_field(self):
        config = PipelineConfig(
            repo="/tmp",
            model="glm-4.6",
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok")],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                          coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        assert config.model == "glm-4.6"

    def test_config_model_defaults_empty(self):
        config = PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok")],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                          coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        assert config.model == ""


class TestModelResolution:
    """Model resolution priority: step > cli > config > None."""

    def test_step_model_wins_over_everything(self, tmp_path):
        """step.model takes priority over config.model and --model."""
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            model="glm-4.6",
            pipeline=[PipelineStep(
                id="gen", executor="claude-code",
                prompt="test", model="deepseek-v4-pro",
            )],
            modules=[Module(name="auth", source_dir="src/", source_files=["a.c"],
                          coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert steps[0].model == "deepseek-v4-pro"

    def test_no_step_model_uses_empty(self, tmp_path):
        """Without step.model, compiled step has model=''."""
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            pipeline=[PipelineStep(
                id="gen", executor="claude-code",
                prompt="test",
            )],
            modules=[Module(name="auth", source_dir="src/", source_files=["a.c"],
                          coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert steps[0].model == ""


class TestCCExecutorNoModel:
    """When model is None/empty, CCExecutor doesn't pass --model to CC."""

    def test_no_model_omits_flag(self):
        """CCExecutor with model=None should not add --model to command."""
        from cc_pipeline.executor import CCExecutor, CCResult
        from unittest.mock import patch, MagicMock

        executor = CCExecutor(model=None)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "done"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor.run("test prompt", cwd="/tmp")

            cmd = mock_run.call_args[0][0]
            assert "--model" not in cmd
            assert "-p" in cmd

    def test_with_model_adds_flag(self):
        """CCExecutor with model='glm-4.6' should add --model."""
        from cc_pipeline.executor import CCExecutor
        from unittest.mock import patch, MagicMock

        executor = CCExecutor(model="glm-4.6")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "done"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor.run("test prompt", cwd="/tmp")

            cmd = mock_run.call_args[0][0]
            assert "--model" in cmd
            assert "glm-4.6" in cmd


class TestCLIDefaultModel:
    """CLI --model defaults to None (not hardcoded glm-4.6)."""

    def test_model_defaults_none(self):
        from cc_pipeline.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["run", "config.yaml"])
        assert args.model is None
