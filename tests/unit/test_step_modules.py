"""step.modules filtering tests — restrict steps to specific modules names."""
import pytest
import subprocess, os
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.compiler import PipelineCompiler


def _config(modules, pipeline):
    return PipelineConfig(
        repo="/tmp/fake",
        pipeline=pipeline,
        modules=modules,
    )


class TestStepModulesFiltering:
    """step.modules restricts which modules a step applies to."""

    def test_no_modules_field_means_all_modules(self):
        """Step without modules field → compiles for all modules."""
        config = _config(
            modules=[Module(name="A", source_dir="src/"), Module(name="B", source_dir="src/")],
            pipeline=[PipelineStep(id="P1", executor="shell", prompt="echo hi")],
        )
        steps_a = PipelineCompiler(config).compile_module("A")
        steps_b = PipelineCompiler(config).compile_module("B")
        assert len(steps_a) == 1
        assert len(steps_b) == 1

    def test_modules_list_restricts_step(self):
        """step.modules: [A] → only compiles for A, not B."""
        config = _config(
            modules=[Module(name="A", source_dir="src/"), Module(name="B", source_dir="src/")],
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo all"),
                PipelineStep(id="P2", executor="shell", prompt="echo A only", modules=["A"]),
            ],
        )
        steps_a = PipelineCompiler(config).compile_module("A")
        steps_b = PipelineCompiler(config).compile_module("B")
        assert len(steps_a) == 2  # P1 + P2
        assert len(steps_b) == 1  # P1 only
        assert steps_b[0].step_id == "P1"

    def test_modules_with_multiple_names(self):
        """modules: [A, C] → compiles for A and C but not B."""
        config = _config(
            modules=[
                Module(name="A", source_dir="src/"),
                Module(name="B", source_dir="src/"),
                Module(name="C", source_dir="src/"),
            ],
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo all"),
                PipelineStep(id="P2", executor="shell", prompt="echo AC", modules=["A", "C"]),
            ],
        )
        steps_a = PipelineCompiler(config).compile_module("A")
        steps_b = PipelineCompiler(config).compile_module("B")
        steps_c = PipelineCompiler(config).compile_module("C")
        assert len(steps_a) == 2
        assert len(steps_b) == 1
        assert len(steps_c) == 2

    def test_modules_with_per_file_loop(self):
        """modules filtering works with loop: per_file."""
        config = _config(
            modules=[
                Module(name="A", source_dir="src/", source_files=["a1.c", "a2.c"]),
                Module(name="B", source_dir="src/", source_files=["b1.c"]),
            ],
            pipeline=[
                PipelineStep(id="gen", executor="shell", prompt="echo gen", loop="per_file",
                             modules=["A"]),
            ],
        )
        steps_a = PipelineCompiler(config).compile_module("A")
        steps_b = PipelineCompiler(config).compile_module("B")
        # A: gen expands to 2 (a1.c, a2.c)
        assert len(steps_a) == 2
        # B: gen skipped entirely (not in modules list)
        assert len(steps_b) == 0

    def test_unknown_module_in_modules_raises(self):
        """modules: [A, X] where X doesn't exist → ValueError."""
        config = _config(
            modules=[Module(name="A", source_dir="src/")],
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo", modules=["A", "NONEXISTENT"]),
            ],
        )
        with pytest.raises(ValueError, match="unknown module"):
            PipelineCompiler(config).compile_module("A")

    def test_empty_compiled_steps_when_all_filtered(self):
        """Module B has all steps filtered to other modules → empty compiled list."""
        config = _config(
            modules=[
                Module(name="A", source_dir="src/"),
                Module(name="B", source_dir="src/"),
            ],
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo", modules=["A"]),
            ],
        )
        steps_b = PipelineCompiler(config).compile_module("B")
        assert len(steps_b) == 0
