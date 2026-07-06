"""TDD: unified config validation — 5 validation gaps.

Covers:
  1. YAML syntax error → friendly ValueError (so cli.py 'except ValueError' catches it)
  2. executor must be a string
  3. depends_on cannot reference itself (self-loop)
  4. postcondition must be a dict
  5. postcondition missing required 'shell' field → ValueError

Plus regression tests confirming existing validations are unaffected.
"""
import pytest


# Minimal valid config — every gap test mutates a copy of this.
VALID_YAML = """
repo: /tmp/test-repo

pipeline:
  - id: build
    executor: claude-code
    prompt: "Build {module}"
    postcondition:
      shell: "test -d out/{module}"
      expect: "$.ok == true"

modules:
  - name: auth
    source_dir: src/auth/
    source_files:
      - auth.c
"""


class TestYamlSyntaxError:
    """Gap 1: malformed YAML must surface as ValueError, not yaml.YAMLError."""

    def test_invalid_yaml_raises_valueerror(self, tmp_yaml):
        """A YAML syntax error is converted to ValueError so cli.py catches it."""
        from cc_pipeline.config import load_config
        # Unterminated quote → yaml.scanner.ScannerError (a yaml.YAMLError)
        bad = tmp_yaml('repo: "unterminated')
        with pytest.raises(ValueError, match="YAML syntax error"):
            load_config(str(bad))

    def test_invalid_yaml_not_raw_yamlerror(self, tmp_yaml):
        """The raised exception must be ValueError, not a bare yaml.YAMLError."""
        from cc_pipeline.config import load_config
        import yaml
        bad = tmp_yaml('pipeline: ][\n')
        try:
            load_config(str(bad))
        except ValueError:
            pass  # expected
        except yaml.YAMLError:
            pytest.fail("yaml.YAMLError leaked out of load_config instead of ValueError")


class TestExecutorType:
    """Gap 2: executor must be a string."""

    def test_executor_as_int_raises(self, tmp_yaml):
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace("    executor: claude-code\n", "    executor: 123\n")
        with pytest.raises(ValueError, match="executor must be a string"):
            load_config(str(tmp_yaml(bad)))

    def test_executor_as_list_raises(self, tmp_yaml):
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace("    executor: claude-code\n", "    executor: [claude-code]\n")
        with pytest.raises(ValueError, match="executor must be a string"):
            load_config(str(tmp_yaml(bad)))

    def test_executor_missing_still_defaults(self, tmp_yaml):
        """No executor key defaults to 'claude-code' — must NOT trip the type check."""
        from cc_pipeline.config import load_config
        good = VALID_YAML.replace("    executor: claude-code\n", "")
        config = load_config(str(tmp_yaml(good)))
        assert config.pipeline[0].executor == "claude-code"


class TestDependsOnSelfLoop:
    """Gap 3: a step cannot depend_on itself."""

    def test_self_reference_raises(self, tmp_yaml):
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace("    prompt: \"Build {module}\"",
                                 "    prompt: \"Build {module}\"\n    depends_on: build")
        with pytest.raises(ValueError, match="depends_on cannot reference itself"):
            load_config(str(tmp_yaml(bad)))


class TestPostconditionType:
    """Gap 4: postcondition must be a dict."""

    def test_postcondition_as_string_raises(self, tmp_yaml):
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace(
            "    postcondition:\n      shell: \"test -d out/{module}\"\n      expect: \"$.ok == true\"",
            "    postcondition: \"not a dict\"",
        )
        with pytest.raises(ValueError, match="postcondition must be a dict"):
            load_config(str(tmp_yaml(bad)))

    def test_postcondition_as_int_raises(self, tmp_yaml):
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace(
            "    postcondition:\n      shell: \"test -d out/{module}\"\n      expect: \"$.ok == true\"",
            "    postcondition: 42",
        )
        with pytest.raises(ValueError, match="postcondition must be a dict"):
            load_config(str(tmp_yaml(bad)))

    def test_postcondition_none_ok(self, tmp_yaml):
        """No postcondition at all must still load fine."""
        from cc_pipeline.config import load_config
        good = VALID_YAML.replace(
            "    postcondition:\n      shell: \"test -d out/{module}\"\n      expect: \"$.ok == true\"\n",
            "",
        )
        config = load_config(str(tmp_yaml(good)))
        assert config.pipeline[0].postcondition is None


class TestPostconditionMissingShell:
    """Gap 5: a dict postcondition must include 'shell'."""

    def test_missing_shell_raises(self, tmp_yaml):
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace(
            "    postcondition:\n      shell: \"test -d out/{module}\"\n      expect: \"$.ok == true\"",
            "    postcondition:\n      expect: \"$.ok == true\"",
        )
        with pytest.raises(ValueError, match=r"postcondition missing required 'shell' field"):
            load_config(str(tmp_yaml(bad)))

    def test_shell_only_ok(self, tmp_yaml):
        """A postcondition with only 'shell' (no 'expect') must load fine."""
        from cc_pipeline.config import load_config
        good = VALID_YAML.replace(
            "    postcondition:\n      shell: \"test -d out/{module}\"\n      expect: \"$.ok == true\"",
            "    postcondition:\n      shell: \"test -d out/{module}\"",
        )
        config = load_config(str(tmp_yaml(good)))
        assert config.pipeline[0].postcondition["shell"] == "test -d out/{module}"


class TestExistingValidationsUnaffected:
    """Regression: existing validations must still fire after the new checks."""

    def test_valid_config_loads(self, tmp_yaml):
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(VALID_YAML)))
        assert config.repo == "/tmp/test-repo"
        assert config.pipeline[0].id == "build"

    def test_invalid_executor_value_still_caught(self, tmp_yaml):
        """A string executor with a bad value still hits the existing executor check."""
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace("    executor: claude-code\n", "    executor: codex\n")
        with pytest.raises(ValueError, match="invalid executor"):
            load_config(str(tmp_yaml(bad)))

    def test_missing_repo_still_caught(self, tmp_yaml):
        from cc_pipeline.config import load_config
        bad = VALID_YAML.replace("repo: /tmp/test-repo\n", "")
        with pytest.raises(ValueError, match="repo"):
            load_config(str(tmp_yaml(bad)))
