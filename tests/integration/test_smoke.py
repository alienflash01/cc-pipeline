"""TDD: Integration smoke test — config + render + executor chain."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock


SMOKE_YAML = """
repo: /tmp/smoke-repo
base_branch: main
concurrency: 1

pipeline:
  - id: generate
    executor: claude-code
    prompt: |
      你在为 {module} 模块生成单元测试。
      源码目录：{source_dir}
      读取所有源文件，为每个函数生成测试。
    postcondition:
      shell: "test -f tests/test_{module}.py"
    output: generate.json

modules:
  - name: math
    spec_id: SPEC-SMOKE
    source_dir: src/
    source_files:
      - math_utils.py
    coverage:
      line_threshold: 80
      branch_threshold: 70
"""


class TestSmokeIntegration:
    """Integration test: config → render → executor → result."""

    def test_config_loads_and_renders_prompt(self, tmp_yaml):
        """Config loads, and prompt template renders with module variables."""
        from cc_pipeline.config import load_config
        from cc_pipeline.render import render

        config = load_config(str(tmp_yaml(SMOKE_YAML)))
        assert len(config.modules) == 1
        assert config.modules[0].name == "math"

        # Build variables dict
        mod = config.modules[0]
        variables = {
            "module": mod.name,
            "source_dir": mod.source_dir,
            "spec_id": mod.spec_id,
            **mod.variables,
            **{f"{k}": v for k, v in mod.coverage.items()},
        }

        step = config.pipeline[0]
        rendered = render(step.prompt, variables)
        assert "math" in rendered
        assert "src/" in rendered

    @patch("cc_pipeline.executor.subprocess.run")
    def test_full_chain_config_render_execute(self, mock_run, tmp_yaml, tmp_path):
        """Full chain: load config → render prompt → call executor."""
        from cc_pipeline.config import load_config
        from cc_pipeline.render import render
        from cc_pipeline.executor import CCExecutor

        # Simulate CC writing a test file
        mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")

        config = load_config(str(tmp_yaml(SMOKE_YAML)))
        mod = config.modules[0]
        step = config.pipeline[0]

        # Render
        variables = {
            "module": mod.name,
            "source_dir": mod.source_dir,
            "spec_id": mod.spec_id,
        }
        rendered_prompt = render(step.prompt, variables)

        # Execute
        executor = CCExecutor(model="glm-4.6")
        result = executor.run(prompt=rendered_prompt, cwd=str(tmp_path))

        # Verify
        assert result.returncode == 0
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "math" in " ".join(cmd)

    @patch("cc_pipeline.executor.subprocess.run")
    def test_logger_records_full_chain(self, mock_run, tmp_yaml, tmp_path):
        """Logger captures events from the full chain."""
        from cc_pipeline.config import load_config
        from cc_pipeline.executor import CCExecutor
        from cc_pipeline.logger import Logger

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        config = load_config(str(tmp_yaml(SMOKE_YAML)))
        logger = Logger(run_dir=str(tmp_path / "runs"), module_name="math")

        logger.event("step_start", step="generate", attempt=1)
        logger.log_pass(step="generate", attempt=1, info={"returncode": 0})

        # Verify transcript
        log_file = tmp_path / "runs" / "math" / "transcript.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        events = [json.loads(l)["event"] for l in lines]
        assert "step_start" in events
        assert "pass" in events

    def test_pipeline_step_has_postcondition(self, tmp_yaml):
        """Pipeline step has a postcondition with shell command."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(SMOKE_YAML)))
        step = config.pipeline[0]
        assert step.postcondition is not None
        assert "shell" in step.postcondition
        assert "test_" in step.postcondition["shell"]
