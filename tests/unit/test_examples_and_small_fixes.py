"""TDD: runnable examples + small config fixes.

Covers:
  - examples/quickstart-shell/config.yaml exists and is parseable by load_config
  - examples/quickstart-cc/config.yaml exists and is parseable by load_config
  - PipelineConfig.output_branch_prefix default is the neutral 'cc-auto'
  - load_config warns when a step sets both prompt and prompt_file
"""
import warnings
from pathlib import Path

from cc_pipeline.config import PipelineConfig, load_config

REPO_ROOT = Path(__file__).parents[2]
SHELL_CFG = REPO_ROOT / "examples" / "quickstart-shell" / "config.yaml"
CC_CFG = REPO_ROOT / "examples" / "quickstart-cc" / "config.yaml"


class TestQuickstartShellExample:
    """examples/quickstart-shell: 3-step shell pipeline, no CC, no API key."""

    def test_config_exists(self):
        assert SHELL_CFG.exists(), f"missing example: {SHELL_CFG}"

    def test_config_loads(self):
        config = load_config(str(SHELL_CFG))
        assert isinstance(config, PipelineConfig)
        # 3-step pipeline, all shell executor
        assert len(config.pipeline) == 3
        assert all(step.executor == "shell" for step in config.pipeline)
        # step 2 carries a postcondition (exit-code check)
        assert config.pipeline[1].postcondition is not None


class TestQuickstartCcExample:
    """examples/quickstart-cc: 3-step CC pipeline with per-file loop + on_failure."""

    def test_config_exists(self):
        assert CC_CFG.exists(), f"missing example: {CC_CFG}"

    def test_config_loads(self):
        config = load_config(str(CC_CFG))
        assert isinstance(config, PipelineConfig)
        assert [s.id for s in config.pipeline] == ["scaffold", "generate", "evaluate"]
        # 2 modules
        assert len(config.modules) == 2
        # generate: per_file loop, depends_on scaffold
        gen = next(s for s in config.pipeline if s.id == "generate")
        assert gen.loop == "per_file"
        assert gen.depends_on == "scaffold"
        # evaluate: depends_on generate, on_failure jumps back to generate
        eva = next(s for s in config.pipeline if s.id == "evaluate")
        assert eva.depends_on == "generate"
        assert eva.on_failure == "generate"
        # source_files use dict (per-file param) format
        for mod in config.modules:
            assert all(isinstance(sf, dict) for sf in mod.source_files)


class TestOutputBranchPrefixDefault:
    """Default output_branch_prefix is the neutral 'cc-auto' (not 'ut-auto')."""

    def test_default_is_cc_auto(self, tmp_path):
        config_file = tmp_path / "c.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    prompt: "echo hi"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
""")
        config = load_config(str(config_file))
        assert config.output_branch_prefix == "cc-auto"


class TestPromptAndPromptFileWarn:
    """When a step sets both prompt and prompt_file, load_config warns (prompt wins)."""

    def test_warns_when_both_set(self, tmp_path):
        prompt_md = tmp_path / "p.md"
        prompt_md.write_text("FROM FILE")
        config_file = tmp_path / "c.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: dup
    executor: claude-code
    prompt: "FROM INLINE"
    prompt_file: {prompt_md}
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
""")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            load_config(str(config_file))
            messages = [str(x.message) for x in w]
        assert any(
            "both prompt and prompt_file" in m and "dup" in m for m in messages
        ), f"expected prompt+prompt_file warning, got: {messages}"
