"""TDD: module.file_order controls per_file loop expansion ordering.

file_order values:
  - batched (default, backward-compatible): all files through stepA, then stepB
  - sequential (new): each file walks the full per_file flow before the next file
"""
import pytest


# Base config: scaffold (non-loop) + gen (per_file) + eval (per_file) + report (non-loop)
# with 3 source files. The exact expansion order differs between batched and sequential.
BASE_YAML = """
repo: /tmp/test-repo
base_branch: main
concurrency: 3
max_retries: 2

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "scaffold for {module}"

  - id: gen
    executor: claude-code
    loop: per_file
    prompt: "gen {file}"

  - id: eval
    executor: judge
    loop: per_file
    prompt: "eval {file}"

  - id: report
    executor: claude-code
    prompt: "report {module}"

modules:
  - name: auth
    spec_id: S1
    source_dir: src/auth/
    source_files:
      - a.c
      - b.c
      - c.c
    variables:
      line_threshold: 80
"""

# Same pipeline but only ONE per_file step (gen). batched and sequential must match.
SINGLE_PER_FILE_YAML = """
repo: /tmp/test-repo
base_branch: main

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "scaffold for {module}"

  - id: gen
    executor: claude-code
    loop: per_file
    prompt: "gen {file}"

  - id: report
    executor: claude-code
    prompt: "report {module}"

modules:
  - name: auth
    spec_id: S1
    source_dir: src/auth/
    source_files:
      - a.c
      - b.c
      - c.c
"""


def _seq(steps):
    """Return list of (step_id, loop_file) tuples for compact assertions."""
    return [(s.step_id, s.loop_file) for s in steps]


class TestFileOrderConfig:
    """Config-level: file_order field exists, defaults, and validates."""

    def test_module_has_file_order_field_default_batched(self, tmp_yaml):
        """Module dataclass exposes file_order, defaulting to 'batched'."""
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(BASE_YAML)))
        assert hasattr(config.modules[0], "file_order")
        assert config.modules[0].file_order == "batched"

    def test_file_order_sequential_parsed(self, tmp_yaml):
        """file_order: sequential is parsed into the module."""
        from cc_pipeline.config import load_config
        yaml_seq = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: sequential\n    variables:\n      line_threshold: 80\n",
        )
        config = load_config(str(tmp_yaml(yaml_seq)))
        assert config.modules[0].file_order == "sequential"

    def test_file_order_batched_explicit(self, tmp_yaml):
        """Explicit file_order: batched parses correctly."""
        from cc_pipeline.config import load_config
        yaml_b = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: batched\n    variables:\n      line_threshold: 80\n",
        )
        config = load_config(str(tmp_yaml(yaml_b)))
        assert config.modules[0].file_order == "batched"

    def test_invalid_file_order_raises_value_error(self, tmp_yaml):
        """An invalid file_order value raises ValueError at config load."""
        from cc_pipeline.config import load_config
        yaml_bad = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: random\n    variables:\n      line_threshold: 80\n",
        )
        with pytest.raises(ValueError, match=r"file_order"):
            load_config(str(tmp_yaml(yaml_bad)))

    def test_invalid_file_error_message_names_module_and_value(self, tmp_yaml):
        """The ValueError message includes the module name and the bad value."""
        from cc_pipeline.config import load_config
        yaml_bad = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: parallel\n    variables:\n      line_threshold: 80\n",
        )
        with pytest.raises(ValueError) as exc_info:
            load_config(str(tmp_yaml(yaml_bad)))
        msg = str(exc_info.value)
        assert "auth" in msg
        assert "parallel" in msg


class TestBatchedExpansion:
    """file_order: batched (default) — all files through each per_file step."""

    def test_batched_groups_by_step_then_file(self, tmp_yaml):
        """batched: scaffold, gen[a,b,c], eval[a,b,c], report."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(BASE_YAML)))
        steps = PipelineCompiler(config).compile_module("auth")
        assert _seq(steps) == [
            ("scaffold", None),
            ("gen", "a.c"),
            ("gen", "b.c"),
            ("gen", "c.c"),
            ("eval", "a.c"),
            ("eval", "b.c"),
            ("eval", "c.c"),
            ("report", None),
        ]

    def test_batched_all_gen_before_all_eval(self, tmp_yaml):
        """In batched, every gen step precedes every eval step."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(BASE_YAML)))
        steps = PipelineCompiler(config).compile_module("auth")
        last_gen = max(i for i, s in enumerate(steps) if s.step_id == "gen")
        first_eval = next(i for i, s in enumerate(steps) if s.step_id == "eval")
        assert last_gen < first_eval


class TestSequentialExpansion:
    """file_order: sequential — each file completes the per_file flow first."""

    def test_sequential_walks_each_file_through_full_flow(self, tmp_yaml):
        """sequential: scaffold, gen[a] eval[a], gen[b] eval[b], gen[c] eval[c], report."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        yaml_seq = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: sequential\n    variables:\n      line_threshold: 80\n",
        )
        config = load_config(str(tmp_yaml(yaml_seq)))
        steps = PipelineCompiler(config).compile_module("auth")
        assert _seq(steps) == [
            ("scaffold", None),
            ("gen", "a.c"),
            ("eval", "a.c"),
            ("gen", "b.c"),
            ("eval", "b.c"),
            ("gen", "c.c"),
            ("eval", "c.c"),
            ("report", None),
        ]

    def test_sequential_file_iteration_order_preserved(self, tmp_yaml):
        """Files are processed in source_files declaration order."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        yaml_seq = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: sequential\n    variables:\n      line_threshold: 80\n",
        )
        config = load_config(str(tmp_yaml(yaml_seq)))
        steps = PipelineCompiler(config).compile_module("auth")
        gen_files = [s.loop_file for s in steps if s.step_id == "gen"]
        assert gen_files == ["a.c", "b.c", "c.c"]

    def test_sequential_no_file_finishes_before_others_start_interleaved(self, tmp_yaml):
        """sequential: gen[b] never appears before eval[a] (file a fully done first)."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        yaml_seq = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: sequential\n    variables:\n      line_threshold: 80\n",
        )
        config = load_config(str(tmp_yaml(yaml_seq)))
        steps = PipelineCompiler(config).compile_module("auth")
        seq = _seq(steps)
        eval_a = seq.index(("eval", "a.c"))
        gen_b = seq.index(("gen", "b.c"))
        assert eval_a < gen_b


class TestNonLoopStepsStayInPlace:
    """Non-loop steps keep their YAML position under sequential."""

    def test_sequential_scaffold_first_report_last(self, tmp_yaml):
        """scaffold stays first, report stays last under sequential."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        yaml_seq = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: sequential\n    variables:\n      line_threshold: 80\n",
        )
        config = load_config(str(tmp_yaml(yaml_seq)))
        steps = PipelineCompiler(config).compile_module("auth")
        assert steps[0].step_id == "scaffold"
        assert steps[-1].step_id == "report"
        assert steps[0].loop_file is None
        assert steps[-1].loop_file is None


class TestSinglePerFileStepEquivalence:
    """With a single per_file step, batched and sequential produce the same order."""

    def test_single_per_file_batched_and_sequential_equal(self, tmp_yaml):
        """One per_file step → no consecutive group to regroup → identical orders."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        batched = PipelineCompiler(load_config(str(tmp_yaml(SINGLE_PER_FILE_YAML)))).compile_module("auth")
        seq_yaml = SINGLE_PER_FILE_YAML.replace(
            "      - c.c\n",
            "      - c.c\n    file_order: sequential\n",
        )
        sequential = PipelineCompiler(load_config(str(tmp_yaml(seq_yaml)))).compile_module("auth")

        assert _seq(batched) == _seq(sequential)
        assert _seq(batched) == [
            ("scaffold", None),
            ("gen", "a.c"),
            ("gen", "b.c"),
            ("gen", "c.c"),
            ("report", None),
        ]


class TestMixedScenario:
    """Realistic mixed pipeline: scaffold + gen(per_file) + eval(per_file) + report."""

    def test_mixed_batched_count_is_8(self, tmp_yaml):
        """3 files × 2 per_file steps + 2 non-loop steps = 8 compiled steps (batched)."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        config = load_config(str(tmp_yaml(BASE_YAML)))
        steps = PipelineCompiler(config).compile_module("auth")
        assert len(steps) == 8

    def test_mixed_sequential_count_is_8(self, tmp_yaml):
        """Same total count under sequential (only ordering differs)."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config
        yaml_seq = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: sequential\n    variables:\n      line_threshold: 80\n",
        )
        config = load_config(str(tmp_yaml(yaml_seq)))
        steps = PipelineCompiler(config).compile_module("auth")
        assert len(steps) == 8

    def test_mixed_batched_vs_sequential_same_steps_different_order(self, tmp_yaml):
        """Both modes compile the same multiset of (step_id, file) but in different order."""
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.config import load_config

        batched = PipelineCompiler(load_config(str(tmp_yaml(BASE_YAML)))).compile_module("auth")
        yaml_seq = BASE_YAML.replace(
            "    variables:\n      line_threshold: 80\n",
            "    file_order: sequential\n    variables:\n      line_threshold: 80\n",
        )
        sequential = PipelineCompiler(load_config(str(tmp_yaml(yaml_seq)))).compile_module("auth")

        # Different order
        assert _seq(batched) != _seq(sequential)
        # Same multiset (sorted)
        assert sorted(_seq(batched)) == sorted(_seq(sequential))
