"""TDD: Config Loader tests."""
import pytest
from pathlib import Path


# Test data
VALID_YAML = """
repo: /tmp/test-repo
base_branch: main
concurrency: 3
max_retries: 2

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "Generate scaffold for {module}"
    postcondition:
      shell: "test -d tests/{module}"
    output: scaffold.json

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "Generate test for {file}"
    postcondition:
      shell: "check_coverage.sh {module} {file}"
      expect: "$.line >= {line_threshold}"
    retry: 3

modules:
  - name: auth
    spec_id: SPEC-001
    source_dir: src/auth/
    source_files:
      - auth_login.c
      - auth_token.c
    coverage:
      line_threshold: 80
      branch_threshold: 70
    variables:
      mock_strategy: link-time

  - name: payment
    spec_id: SPEC-002
    source_dir: src/payment/
    source_files:
      - payment_process.c
    coverage:
      line_threshold: 85
      branch_threshold: 75
"""


class TestConfigLoader:
    """Test YAML config parsing."""

    def test_loads_config_from_file(self, tmp_yaml):
        """Can load a YAML config file."""
        from cc_pipeline.config import load_config
        path = tmp_yaml(VALID_YAML)
        config = load_config(str(path))
        assert config is not None

    def test_repo_path_parsed(self, tmp_yaml):
        """repo field is parsed correctly."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.repo == "/tmp/test-repo"

    def test_base_branch_defaults_to_main(self, tmp_yaml):
        """base_branch defaults to 'main'."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.base_branch == "main"

    def test_concurrency_parsed(self, tmp_yaml):
        """concurrency is parsed as int."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.concurrency == 3

    def test_default_concurrency_is_5(self, tmp_yaml):
        """concurrency defaults to 5 when not specified."""
        from cc_pipeline.config import load_config
        yaml_no_concurrency = VALID_YAML.replace("concurrency: 3\n", "")
        config = load_config(str(tmp_yaml(yaml_no_concurrency)))
        assert config.concurrency == 5

    def test_max_retries_parsed(self, tmp_yaml):
        """max_retries is parsed."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.max_retries == 2

    def test_modules_parsed(self, tmp_yaml):
        """modules list is parsed with correct count."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert len(config.modules) == 2

    def test_module_name_parsed(self, tmp_yaml):
        """module name is correct."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.modules[0].name == "auth"
        assert config.modules[1].name == "payment"

    def test_module_spec_id_parsed(self, tmp_yaml):
        """module spec_id is correct."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.modules[0].spec_id == "SPEC-001"

    def test_module_source_files_parsed(self, tmp_yaml):
        """module source_files list is correct."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.modules[0].source_files == ["auth_login.c", "auth_token.c"]

    def test_module_coverage_thresholds_as_int(self, tmp_yaml):
        """coverage thresholds are integers."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.modules[0].coverage["line_threshold"] == 80
        assert isinstance(config.modules[0].coverage["line_threshold"], int)

    def test_module_variables_parsed(self, tmp_yaml):
        """module-level variables are parsed."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.modules[0].variables["mock_strategy"] == "link-time"

    def test_pipeline_steps_parsed(self, tmp_yaml):
        """pipeline steps are parsed in order."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert len(config.pipeline) == 2
        assert config.pipeline[0].id == "scaffold"
        assert config.pipeline[1].id == "generate"

    def test_step_executor_type_parsed(self, tmp_yaml):
        """step executor type is correct."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.pipeline[0].executor == "claude-code"

    def test_step_loop_parsed(self, tmp_yaml):
        """step loop directive is parsed."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.pipeline[1].loop == "per_file"

    def test_step_retry_parsed(self, tmp_yaml):
        """step retry count is parsed."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.pipeline[1].retry == 3

    def test_step_postcondition_shell(self, tmp_yaml):
        """step postcondition shell command is parsed."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert "test -d" in config.pipeline[0].postcondition["shell"]

    def test_step_postcondition_expect(self, tmp_yaml):
        """step postcondition expect expression is parsed."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert "$.line" in config.pipeline[1].postcondition["expect"]

    def test_missing_repo_raises(self, tmp_yaml):
        """Missing repo field raises ValueError."""
        from cc_pipeline.config import load_config
        bad_yaml = VALID_YAML.replace("repo: /tmp/test-repo\n", "")
        with pytest.raises(ValueError, match="repo"):
            load_config(str(tmp_yaml(bad_yaml)))

    def test_missing_modules_raises(self, tmp_yaml):
        """Missing modules list raises ValueError."""
        from cc_pipeline.config import load_config
        bad_yaml = "repo: /tmp/test-repo\nbase_branch: main\npipeline:\n  - id: x\n    executor: shell\nmodules: []\n"
        with pytest.raises(ValueError, match="module"):
            load_config(str(tmp_yaml(bad_yaml)))

    def test_missing_pipeline_raises(self, tmp_yaml):
        """Missing pipeline raises ValueError."""
        from cc_pipeline.config import load_config
        bad_yaml = "\n".join(
            line for line in VALID_YAML.split("\n")
            if not line.strip().startswith("- id:")
            and "pipeline:" not in line
            and "executor:" not in line
            and "prompt:" not in line
        )
        bad_yaml = "repo: /tmp/test-repo\nbase_branch: main\nmodules:\n  - name: x\n    spec_id: s\n    source_dir: src/\n    source_files: [a.c]\n    coverage: {line_threshold: 80, branch_threshold: 70}\n"
        with pytest.raises(ValueError, match="pipeline"):
            load_config(str(tmp_yaml(bad_yaml)))
