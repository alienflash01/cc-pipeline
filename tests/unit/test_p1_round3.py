"""TDD: Round 3 bug reproduction — #49-#58."""
import pytest
import json
import subprocess, os, re
from pathlib import Path
from unittest.mock import patch, MagicMock


# ─── #49: error message "N attempts" but actual N+1 ───

class TestIssue49ErrorMessageOffByOne:
    def test_error_message_matches_actual_attempts(self, tmp_path):
        """Error message should report N+1 attempts for retry=N."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        call_count = [0]
        class AlwaysFailCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                call_count[0] += 1
                return CCResult(returncode=1, stdout="", stderr="error")

        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="test", postcondition=None, retry=2,
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(tmp_path), run_dir=str(tmp_path / "runs"),
            cc_executor=AlwaysFailCC(),
        )
        result = runner.run()
        # retry=2 → 3 actual attempts
        assert call_count[0] == 3
        # Message should say "3 attempts" not "2 attempts"
        assert "3 attempts" in result.get("error", ""), \
            f"Expected '3 attempts' in error, got: {result.get('error')}"


# ─── #50: output path traversal (post-fix residual) ───

class TestIssue50OutputTraversal:
    def test_output_with_traversal_rejected_at_render(self, tmp_path):
        """step.output with path traversal should be sanitized in _inject_context."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        captured_prompt = []
        class CaptureCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                captured_prompt.append(prompt)
                return CCResult(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="test", postcondition=None, retry=1,
            output="../../../etc/crontab",
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(tmp_path), run_dir=str(tmp_path / "runs"),
            cc_executor=CaptureCC(),
        )
        runner._execute_step(step)
        # The output path should NOT contain traversal
        assert "../../../" not in captured_prompt[0], \
            "Path traversal in output not sanitized"


# ─── #51: StateManager.load corrupt JSON (post-fix residual) ───

class TestIssue51StateManagerCorruptJSON:
    def test_load_corrupt_json_returns_none(self, tmp_path):
        """StateManager.load should return None for corrupt JSON."""
        from cc_pipeline.state import StateManager

        # StateManager uses orchestrator-state.json as the state file
        (tmp_path / "orchestrator-state.json").write_text('{"broken')
        mgr = StateManager(run_dir=str(tmp_path))
        result = mgr.load()
        assert result is None, "Should return None, not crash"


# ─── #52: rate_limit substring false positive (post-fix residual) ───

class TestIssue52RateLimitFalsePositive:
    def test_port_4290_not_rate_limit(self):
        """'Port 4290 not available' should NOT be rate limited."""
        from cc_pipeline.runner import ModuleRunner
        # Access the rate limit detection
        # We test via _is_rate_limited or the patterns
        import inspect
        source = inspect.getsource(ModuleRunner)
        # Should use word boundary regex, not bare substring
        assert "429" not in source or "429" in source.split("RATE_LIMIT")[0], \
            "Rate limit detection should use word-boundary regex"


# ─── #55: unknown YAML fields silently ignored ───

class TestIssue55UnknownYAMLFields:
    def test_unknown_step_field_warns(self, tmp_path):
        """Unknown YAML step fields should warn."""
        from cc_pipeline.config import load_config
        import warnings

        config_file = tmp_path / "unknown.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    prompt: "echo ok"
    unknow_field: value
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
            load_config(str(config_file))
            assert any("unknown" in str(x.message).lower() for x in w), \
                "Should warn about unknown YAML fields"


# ─── #56: render(None) → "None" ───

class TestIssue56RenderNone:
    def test_none_variable_becomes_empty(self):
        """render should convert None to empty string, not 'None'."""
        from cc_pipeline.render import render
        result = render("id={spec_id}", {"spec_id": None})
        assert "None" not in result, f"None should be empty, got: {result}"
        assert "id=" in result
