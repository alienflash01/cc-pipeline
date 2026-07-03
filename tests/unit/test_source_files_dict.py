"""TDD: source_files dict format — per-file custom params injected into prompt."""
import pytest
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.compiler import PipelineCompiler


def _make_config(prompt, source_files, extra_step_fields=None):
    """Helper: build a minimal config with loop=per_file."""
    step_kwargs = {"id": "gen", "executor": "claude-code", "prompt": prompt, "loop": "per_file"}
    if extra_step_fields:
        step_kwargs.update(extra_step_fields)
    return PipelineConfig(
        repo="/tmp/test",
        pipeline=[PipelineStep(**step_kwargs)],
        modules=[Module(
            name="auth",
            source_dir="src/auth/",
            source_files=source_files,
        )],
    )


class TestSourceFilesDictFormat:
    """source_files entries can be dicts with path + custom params."""

    def test_dict_entry_path_becomes_file_var(self):
        """source_files dict entry: path key maps to {file} variable."""
        config = _make_config("test {file}", [{"path": "auth_login.c", "param_a": "TYPE1"}])
        steps = PipelineCompiler(config).compile_module("auth")
        assert len(steps) == 1
        assert "auth_login.c" in steps[0].rendered_prompt

    def test_dict_entry_custom_params_in_prompt(self):
        """Custom params from dict entry appear in rendered prompt."""
        config = _make_config(
            "file={file} param_a={param_a} param_b={param_b}",
            [{"path": "a.c", "param_a": "TYPE1", "param_b": "CHECK"}],
        )
        steps = PipelineCompiler(config).compile_module("auth")
        assert "param_a=TYPE1" in steps[0].rendered_prompt
        assert "param_b=CHECK" in steps[0].rendered_prompt

    def test_string_entry_still_works(self):
        """Backward compat: plain string entries still work."""
        config = _make_config("test {file}", ["a.c", "b.c"])
        steps = PipelineCompiler(config).compile_module("auth")
        assert len(steps) == 2
        assert "a.c" in steps[0].rendered_prompt
        assert "b.c" in steps[1].rendered_prompt

    def test_mixed_string_and_dict_entries(self):
        """Mix of string and dict entries in same module."""
        config = _make_config("file={file}", ["a.c", {"path": "b.c", "param_a": "X"}])
        steps = PipelineCompiler(config).compile_module("auth")
        assert len(steps) == 2
        assert "a.c" in steps[0].rendered_prompt
        assert "b.c" in steps[1].rendered_prompt

    def test_dict_entry_without_path_raises(self):
        """Dict entry without 'path' key should raise ValueError."""
        config = _make_config("test", [{"param_a": "TYPE1"}])  # no path!
        with pytest.raises(ValueError, match="path"):
            PipelineCompiler(config).compile_module("auth")

    def test_dict_entry_custom_param_name_spec_id(self):
        """User-defined param name like spec_id works in prompt."""
        config = _make_config(
            "spec={spec_id}",
            [{"path": "a.c", "spec_id": "SPEC-001"}],
        )
        steps = PipelineCompiler(config).compile_module("auth")
        assert "spec=SPEC-001" in steps[0].rendered_prompt

    def test_dict_entry_params_in_postcondition(self):
        """Custom params also available in postcondition shell command."""
        config = _make_config(
            "test",
            [{"path": "a.c", "param_a": "TYPE1"}],
            extra_step_fields={
                "postcondition": {"shell": "echo {param_a}", "expect": "contains('{param_a}')"},
            },
        )
        steps = PipelineCompiler(config).compile_module("auth")
        assert "TYPE1" in steps[0].postcondition["shell"]

    def test_dict_entry_loop_file_set_correctly(self):
        """loop_file attribute on CompiledStep is the path value."""
        config = _make_config("test", [{"path": "login.c", "param_a": "X"}])
        steps = PipelineCompiler(config).compile_module("auth")
        assert steps[0].loop_file == "login.c"
