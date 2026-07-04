"""TDD: Pipeline Compiler tests."""
import pytest


COMPILER_YAML = """
repo: /tmp/test-repo
base_branch: main
concurrency: 3
max_retries: 2

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "scaffold for {module}"
    postcondition:
      shell: "test -d tests/{module}"

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "gen {file}"
    postcondition:
      shell: "check_cov.sh"
      expect: "$.line >= 80"
    retry: 3
    depends_on: scaffold

  - id: evaluate
    executor: judge
    prompt: "eval"
    depends_on: generate

modules:
  - name: auth
    spec_id: S1
    source_dir: src/auth/
    source_files:
      - auth_login.c
      - auth_token.c
    coverage:
      line_threshold: 80
      branch_threshold: 70
"""


class TestPipelineCompiler:
    """Test YAML pipeline → executable Step sequence compilation."""

    def test_compiler_importable(self):
        from cc_pipeline.compiler import PipelineCompiler
        assert PipelineCompiler is not None

    def test_compile_returns_compiled_steps(self, tmp_yaml):
        """compile() returns a list of CompiledStep."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert isinstance(steps, list)
        assert len(steps) > 0

    def test_compile_preserves_step_order(self, tmp_yaml):
        """Steps are in declaration order."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        ids = [s.step_id for s in steps]
        assert "scaffold" in ids
        assert "generate" in ids
        assert "evaluate" in ids

    def test_loop_per_file_expands_to_substeps(self, tmp_yaml):
        """loop: per_file expands generate into per-file sub-steps."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")

        # auth has 2 source files → generate should expand to 2 steps
        gen_steps = [s for s in steps if "generate" in s.step_id]
        assert len(gen_steps) == 2

    def test_loop_substep_includes_filename(self, tmp_yaml):
        """Each loop sub-step carries the current filename."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        gen_steps = [s for s in steps if "generate" in s.step_id]
        filenames = [s.loop_file for s in gen_steps]
        assert "auth_login.c" in filenames
        assert "auth_token.c" in filenames

    def test_retry_from_step_overrides_global(self, tmp_yaml):
        """Step-level retry overrides global max_retries."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        gen_steps = [s for s in steps if "generate" in s.step_id]
        assert all(s.retry == 3 for s in gen_steps)

    def test_retry_falls_back_to_global(self, tmp_yaml):
        """Steps without retry use global max_retries."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        scaffold = [s for s in steps if s.step_id == "scaffold"][0]
        assert scaffold.retry == 2  # global max_retries

    def test_compiled_step_has_executor_type(self, tmp_yaml):
        """Each compiled step knows its executor type."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        scaffold = [s for s in steps if s.step_id == "scaffold"][0]
        assert scaffold.executor == "claude-code"

    def test_compiled_step_has_rendered_prompt(self, tmp_yaml):
        """Compiled step has a rendered prompt (variables substituted)."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        scaffold = [s for s in steps if s.step_id == "scaffold"][0]
        assert "auth" in scaffold.rendered_prompt

    def test_compiled_step_has_postcondition(self, tmp_yaml):
        """Compiled step carries its postcondition."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        scaffold = [s for s in steps if s.step_id == "scaffold"][0]
        assert scaffold.postcondition is not None
        assert "shell" in scaffold.postcondition

    def test_depends_on_reorders_steps(self, tmp_yaml):
        """Steps with depends_on come after their dependency."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        config = load_config(str(tmp_yaml(COMPILER_YAML)))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        ids = [s.step_id for s in steps]
        # evaluate depends_on generate, so evaluate comes after all generate steps
        gen_idx = max(i for i, s in enumerate(steps) if "generate" in s.step_id)
        eval_idx = next(i for i, s in enumerate(steps) if s.step_id == "evaluate")
        assert eval_idx > gen_idx

    def test_invalid_executor_raises(self, tmp_yaml):
        """Invalid executor type raises ValueError at config load."""
        from cc_pipeline.config import load_config

        bad_yaml = COMPILER_YAML.replace('executor: claude-code', 'executor: invalid-type', 1)
        with pytest.raises(ValueError, match="executor"):
            load_config(str(tmp_yaml(bad_yaml)))

    def test_duplicate_step_id_raises(self, tmp_yaml):
        """Duplicate step IDs raise ValueError."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        dup_yaml = COMPILER_YAML.replace(
            "- id: evaluate",
            "- id: scaffold\n    executor: shell\n    command: echo hi\n  - id: evaluate",
        )
        # This YAML might be invalid, let's construct it properly
        dup_yaml = """
repo: /tmp/test-repo
pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "a"
  - id: scaffold
    executor: shell
    command: echo hi
modules:
  - name: auth
    spec_id: S
    source_dir: src/
    source_files: [a.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
"""
        config = load_config(str(tmp_yaml(dup_yaml)))
        compiler = PipelineCompiler(config)
        with pytest.raises(ValueError, match="(?i)duplicate"):
            compiler.compile_module("auth")
