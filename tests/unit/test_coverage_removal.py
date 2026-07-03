"""TDD: Remove coverage field from Module — fold into variables."""
import pytest
import warnings
from cc_pipeline.config import load_config, Module


class TestCoverageRemoval:
    """coverage field removed, content folded into variables."""

    def test_module_has_no_coverage_field(self):
        """Module dataclass should not have coverage attribute."""
        mod = Module(name="test")
        assert not hasattr(mod, "coverage"), \
            "Module should not have 'coverage' field — use variables instead"

    def test_coverage_in_yaml_folds_into_variables(self, tmp_path):
        """YAML with coverage: should auto-fold into variables + warn deprecated."""
        config_file = tmp_path / "c.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config = load_config(str(config_file))
            # Should warn about deprecated coverage
            assert any("coverage" in str(x.message).lower() and "deprecated" in str(x.message).lower()
                       for x in w), \
                "Should warn that 'coverage' is deprecated"
        # coverage values should be in variables
        assert config.modules[0].variables.get("line_threshold") == 80
        assert config.modules[0].variables.get("branch_threshold") == 70

    def test_variables_directly_works_without_coverage(self, tmp_path):
        """YAML using variables directly (no coverage key) should work cleanly."""
        config_file = tmp_path / "v.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    variables:
      line_threshold: 80
      branch_threshold: 70
""")
        config = load_config(str(config_file))
        assert config.modules[0].variables["line_threshold"] == 80
        assert config.modules[0].variables["branch_threshold"] == 70

    def test_coverage_values_reachable_in_prompt(self, tmp_path):
        """{line_threshold} in prompt should resolve from variables."""
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import PipelineStep

        config_file = tmp_path / "p.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: gen
    executor: claude-code
    prompt: |
      coverage target {{line_threshold}}%
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    variables:
      line_threshold: 80
""")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            config = load_config(str(config_file))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("m")
        assert "80" in steps[0].rendered_prompt

    def test_no_coverage_in_variables_no_warn(self, tmp_path):
        """No coverage key in YAML → no deprecation warning."""
        config_file = tmp_path / "nc.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    variables:
      foo: bar
""")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            load_config(str(config_file))
            coverage_warns = [x for x in w if "coverage" in str(x.message).lower()]
            assert len(coverage_warns) == 0, "Should not warn when coverage key absent"
