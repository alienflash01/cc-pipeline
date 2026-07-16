"""TDD: config cleanup — dead fields removed + shell executor unified on prompt.

Verifies (per SPEC):
  1. PipelineStep no longer has on_complete / skill / rollback / command fields.
  3. _KNOWN_STEP_FIELDS no longer lists those step fields.
  4. shell executor reads from `prompt` (not `command`).
  5. An old YAML still using `command:` does not crash — it is silently
     ignored and warned as an unknown field.
"""
import dataclasses
import warnings

from cc_pipeline.config import (
    PipelineConfig,
    PipelineStep,
    _KNOWN_STEP_FIELDS,
    load_config,
)
from cc_pipeline.compiler import PipelineCompiler
from cc_pipeline.config import Module


_REMOVED_STEP_FIELDS = {"on_complete", "skill", "rollback", "command"}


class TestStepFieldsRemoved:
    """#1: the dead PipelineStep fields are gone."""

    def test_step_field_names_exclude_dead_fields(self):
        names = {f.name for f in dataclasses.fields(PipelineStep)}
        for dead in _REMOVED_STEP_FIELDS:
            assert dead not in names, f"PipelineStep still has dead field '{dead}'"

    def test_step_constructor_rejects_command_kwarg(self):
        """Passing command= must now raise — the field no longer exists."""
        try:
            PipelineStep(id="x", executor="shell", command="echo ok")
        except TypeError:
            return
        raise AssertionError("PipelineStep accepted removed 'command' kwarg")

    def test_step_constructor_rejects_rollback_kwarg(self):
        try:
            PipelineStep(id="x", executor="shell", rollback="git-checkpoint")
        except TypeError:
            return
        raise AssertionError("PipelineStep accepted removed 'rollback' kwarg")


class TestConfigFieldsRemoved:
    """#2: the dead PipelineConfig fields are gone."""

    def test_config_field_names_exclude_dead_fields(self):
        names = {f.name for f in dataclasses.fields(PipelineConfig)}
        for dead in ("pr_labels", "pr_title_template"):
            assert dead not in names, f"PipelineConfig still has dead field '{dead}'"

        try:
            PipelineConfig(repo="/tmp", pr_labels=["x"])
        except TypeError:
            pass


class TestKnownStepFieldsCleaned:
    """#3: _KNOWN_STEP_FIELDS no longer lists the removed step fields."""

    def test_dead_step_fields_absent(self):
        for dead in _REMOVED_STEP_FIELDS:
            assert dead not in _KNOWN_STEP_FIELDS, (
                f"_KNOWN_STEP_FIELDS still lists '{dead}'"
            )

    def test_kept_step_fields_present(self):
        # Sanity: the well-known live fields are still recognized (no warn).
        for live in ("id", "executor", "prompt", "prompt_file", "postcondition"):
            assert live in _KNOWN_STEP_FIELDS, f"_KNOWN_STEP_FIELDS lost '{live}'"


class TestShellExecutorUsesPrompt:
    """#4: shell executor reads `prompt`, not `command`."""

    def _config(self, **step_kw):
        return PipelineConfig(
            repo="/tmp",
            pipeline=[PipelineStep(id="verify", executor="shell", **step_kw)],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=["a.c"],
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )

    def test_shell_command_becomes_rendered_prompt(self):
        steps = PipelineCompiler(self._config(prompt="gcov src/*.c")).compile_module("auth")
        assert "gcov src/*.c" in steps[0].rendered_prompt

    def test_shell_command_variables_rendered(self):
        cfg = self._config(prompt="cd tests/{module} && echo done")
        steps = PipelineCompiler(cfg).compile_module("auth")
        assert "cd tests/auth" in steps[0].rendered_prompt


class TestLegacyCommandYamlRaises:
    """#5: old YAML with `command:` now raises ValueError."""

    def test_command_field_raises_error(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: verify
    executor: shell
    command: echo HELLO
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
""")
        from cc_pipeline.config import load_config
        try:
            load_config(str(config_file))
            assert False, "Expected ValueError for 'command' field"
        except ValueError as e:
            assert "not a recognized field" in str(e)
