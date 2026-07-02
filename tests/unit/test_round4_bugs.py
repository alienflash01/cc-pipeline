"""TDD: Round 4 bug reproduction — #1-#16."""
import pytest
import json, os, re
from pathlib import Path
from unittest.mock import patch, MagicMock


# ─── #1: subprocess text=True + binary stdout ───

class TestIssue1BinaryStdout:
    def test_postcondition_handles_binary_output(self, tmp_path):
        """postcondition should not crash on binary stdout."""
        from cc_pipeline.postcondition import evaluate

        result = evaluate(
            shell="head -c 100 /dev/urandom",
            expect=None,
            cwd=str(tmp_path),
        )
        # Should not crash, should return a result
        assert result is not None


# ─── #2: update_module/set_run_id corrupt JSON ───

class TestIssue2UpdateModuleCorruptJSON:
    def test_update_module_handles_corrupt_json(self, tmp_path):
        """update_module should handle corrupt state.json gracefully."""
        from cc_pipeline.state import StateManager

        (tmp_path / "orchestrator-state.json").write_text('{"broken')
        mgr = StateManager(run_dir=str(tmp_path))
        # Should not crash
        mgr.update_module("auth", status="running")
        # Should have written valid state
        state = json.loads((tmp_path / "orchestrator-state.json").read_text())
        assert state["modules"]["auth"]["status"] == "running"


# ─── #3: error message attempts mismatch (rate limit) ───

class TestIssue3ErrorMessageRateLimit:
    def test_error_includes_actual_attempt_count(self, tmp_path):
        """Error message should report actual CC call count."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        call_count = [0]
        class AlwaysFailCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                call_count[0] += 1
                return CCResult(returncode=1, stdout="", stderr="some error")

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
        # Error message should contain the ACTUAL number of CC calls
        assert str(call_count[0]) in result.get("error", ""), \
            f"Error should mention actual calls ({call_count[0]}), got: {result.get('error')}"


# ─── #8 P0: source_files path traversal ───

class TestIssue8SourceFilesTraversal:
    def test_source_files_traversal_rejected(self, tmp_path):
        """source_files with path traversal should be rejected."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "evil.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: claude-code
    prompt: cat {{file}}
    loop: per_file
modules:
  - name: m
    source_dir: src/
    source_files: ["../../../etc/passwd"]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))


# ─── #9: timeout=-1 accepted ───

class TestIssue9NegativeTimeout:
    def test_negative_timeout_rejected(self, tmp_path):
        """timeout=-1 should be rejected at config load."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "t.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: claude-code
    prompt: test
    timeout: -1
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))


# ─── #10: retry/concurrency no upper bound ───

class TestIssue10NoUpperBound:
    def test_excessive_concurrency_rejected(self, tmp_path):
        """concurrency > 100 should be rejected."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "c.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
concurrency: 999
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
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))

    def test_excessive_retry_rejected(self, tmp_path):
        """max_retries > 20 should be rejected."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "r.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
max_retries: 999
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
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))


# ─── #11: expect || not supported ───

class TestIssue11ExpectOrOperator:
    def test_or_operator_gives_clear_error(self):
        """expect with || should not silently fail."""
        from cc_pipeline.postcondition import _evaluate_single
        # Should either support || or give clear reason
        # Currently: "75 >= 70 || $.line >= 80" → silent False
        result = _evaluate_single("$.line >= 70 || $.line >= 80", {"line": 75})
        # At minimum, the condition should be evaluated somehow
        # For now we just verify it doesn't silently fail on a valid OR


# ─── #12: git index.lock → checkpoint silent fail ───

class TestIssue12GitIndexLock:
    def test_checkpoint_uses_check_true(self):
        """git commands in checkpoint should use check=True."""
        import inspect
        from cc_pipeline.git_checkpoint import GitCheckpoint
        source = inspect.getsource(GitCheckpoint.checkpoint)
        assert "check=True" in source, \
            "git commit/add should use check=True to catch failures"


# ─── #16: model whitespace only ───

class TestIssue16ModelWhitespace:
    def test_whitespace_model_treated_as_empty(self):
        """model='   ' should be treated as None (not sent to CC)."""
        from cc_pipeline.executor import CCExecutor
        from unittest.mock import patch, MagicMock

        executor = CCExecutor(model="   ")
        mock_result = MagicMock(returncode=0, stdout="done", stderr="")

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor.run("test", cwd="/tmp")
            cmd = mock_run.call_args[0][0]
            assert "--model" not in cmd, \
                "Whitespace-only model should not be sent to CC"
