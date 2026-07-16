"""Additional tests for this week's changes — audit, rerun signal, verbose alignment."""
import pytest, io, sys, json, os, subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from cc_pipeline.compiler import CompiledStep, PipelineCompiler
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.logger import Logger
from cc_pipeline.executor import CCExecutor


ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.c").write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ Command audit tests ═══

class TestCommandAudit:
    """log_command_audit and log_file_changes write to transcript."""

    def test_shell_command_audited(self, tmp_path):
        """Shell executor logs command_audit event."""
        runner = ModuleRunner([], "mod", "/tmp", str(tmp_path / "runs"))
        runner.logger.log_command_audit(
            step="gen", command="make test", cwd="/tmp",
            executor="shell", returncode=0, duration_ms=150,
        )
        # Read transcript
        with open(tmp_path / "runs" / "mod" / "transcript.jsonl") as f:
            events = [json.loads(line) for line in f if line.strip()]
        audit_events = [e for e in events if e["event"] == "command_audit"]
        assert len(audit_events) == 1
        assert audit_events[0]["command"] == "make test"
        assert audit_events[0]["executor"] == "shell"
        assert audit_events[0]["returncode"] == 0

    def test_command_truncated_to_500_chars(self, tmp_path):
        """Long command truncated to 500 chars in audit."""
        runner = ModuleRunner([], "mod", "/tmp", str(tmp_path / "runs"))
        long_cmd = "x" * 1000
        runner.logger.log_command_audit(
            step="gen", command=long_cmd, cwd="/tmp", executor="shell",
        )
        with open(tmp_path / "runs" / "mod" / "transcript.jsonl") as f:
            events = [json.loads(line) for line in f if line.strip()]
        audit = [e for e in events if e["event"] == "command_audit"][0]
        assert len(audit["command"]) == 500

    def test_file_changes_audited(self, tmp_path):
        """CC execution logs file_changes event."""
        runner = ModuleRunner([], "mod", "/tmp", str(tmp_path / "runs"))
        runner.logger.log_file_changes(step="gen",
                                       changes=["?? tests/test_a.c", "M  Makefile"])
        with open(tmp_path / "runs" / "mod" / "transcript.jsonl") as f:
            events = [json.loads(line) for line in f if line.strip()]
        fc = [e for e in events if e["event"] == "file_changes"][0]
        assert len(fc["changes"]) == 2
        assert "?? tests/test_a.c" in fc["changes"]

    def test_file_changes_empty_not_logged(self, tmp_path):
        """Empty changes list not written to transcript."""
        runner = ModuleRunner([], "mod", "/tmp", str(tmp_path / "runs"))
        runner.logger.log_file_changes(step="gen", changes=[])
        with open(tmp_path / "runs" / "mod" / "transcript.jsonl") as f:
            events = [json.loads(line) for line in f if line.strip()]
        fc = [e for e in events if e["event"] == "file_changes"]
        assert len(fc) == 0

    def test_audit_in_real_execution(self, git_repo):
        """Shell step execution triggers command_audit in transcript."""
        step = CompiledStep(step_id="shell_cmd", executor="shell",
                            rendered_prompt="echo audit test")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        with open(git_repo / "runs" / "mod" / "transcript.jsonl") as f:
            events = [json.loads(line) for line in f if line.strip()]
        audit = [e for e in events if e["event"] == "command_audit"]
        assert len(audit) == 1
        assert audit[0]["executor"] == "shell"
        assert "echo" in audit[0]["command"]


# ═══ On-failure rerun signal tests ═══

class TestRerunSignal:
    """on_failure jump injects rerun_reason into next step's context."""

    def test_rerun_signal_injected_when_reason_set(self):
        """rerun_reason is appended to prompt as warning."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do generate", output="gen.json")
        runner = ModuleRunner([step], "mod", "/tmp", "/tmp/runs")
        result = runner._inject_context("do generate", step,
                                        rerun_reason="步骤 'P3' 使用你的输出后失败了。")
        assert "重新执行信号" in result
        assert "P3" in result
        assert "完整重新生成" in result

    def test_no_rerun_signal_when_reason_empty(self):
        """Empty rerun_reason → no signal injected."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do generate", output="gen.json")
        runner = ModuleRunner([step], "mod", "/tmp", "/tmp/runs")
        result = runner._inject_context("do generate", step, rerun_reason="")
        assert "重新执行信号" not in result

    def test_rerun_signal_replaced_in_execute_step(self, git_repo):
        """_execute_step reads _rerun_reason and clears it after use."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do it", output="gen.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        runner._rerun_reason = "步骤 'eval' 失败了"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        # Find CC call prompt
        prompt = ""
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            if isinstance(args, list) and len(args) > 2 and args[0].endswith("claude"):
                prompt = args[2]
                break
        assert "重新执行信号" in prompt
        assert "eval" in prompt
        # _rerun_reason should be cleared
        assert runner._rerun_reason == ""


# ═══ Verbose output alignment tests ═══

class TestVerboseAlignment:
    """_label() produces aligned module+file labels."""

    def test_label_without_file(self):
        """Module name only → padded to column width."""
        step = CompiledStep(step_id="gen", executor="shell", rendered_prompt="echo ok")
        runner = ModuleRunner([step], "auth", "/tmp", "/tmp/runs")
        label = runner._label("")
        # Check format: [auth] followed by spaces to reach _label_width
        assert label.startswith("[auth]")
        assert len(label) >= len("[auth]")

    def test_label_with_file(self):
        """Module + file → padded to same column width."""
        step = CompiledStep(step_id="gen", executor="shell",
                            rendered_prompt="echo ok", loop_file="auth_login.c")
        runner = ModuleRunner([step], "auth", "/tmp", "/tmp/runs")
        label_with = runner._label("auth_login.c")
        assert "[auth] [auth_login.c]" in label_with
        assert len(label_with) >= len("[auth] [auth_login.c]")

    def test_label_consistent_width(self):
        """With and without file produce same-width labels."""
        steps = [
            CompiledStep(step_id="gen", executor="shell", rendered_prompt="echo ok",
                         loop_file="long_file_name.c"),
            CompiledStep(step_id="eval", executor="shell", rendered_prompt="echo ok"),
        ]
        runner = ModuleRunner(steps, "mod", "/tmp", "/tmp/runs")
        with_file = runner._label("long_file_name.c")
        without_file = runner._label("")
        assert len(with_file) == len(without_file)

    def test_different_modules_different_widths(self):
        """Different module name → different label width."""
        steps_a = [CompiledStep(step_id="gen", executor="shell", rendered_prompt="ok")]
        steps_b = [CompiledStep(step_id="gen", executor="shell", rendered_prompt="ok")]
        ra = ModuleRunner(steps_a, "a", "/tmp", "/tmp/runs")
        rb = ModuleRunner(steps_b, "long_module_name", "/tmp", "/tmp/runs")
        assert len(ra._label("")) < len(rb._label(""))


# ═══ Ctrl+C / signal handling tests ═══

class TestSignalHandling:
    """Ctrl+C / KeyboardInterrupt handling."""

    def test_keyboard_interrupt_caught_in_executor(self):
        """CCExecutor catches KeyboardInterrupt and re-raises."""
        executor = CCExecutor()
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = KeyboardInterrupt()
            import io, sys
            captured = io.StringIO()
            old = sys.stdout
            sys.stdout = captured
            try:
                with pytest.raises(KeyboardInterrupt):
                    executor.run(prompt="test", cwd="/tmp")
            finally:
                sys.stdout = old
        assert "Interrupted by user" in captured.getvalue()

    def test_shutdown_flag_stops_runner(self, git_repo):
        """shutdown_check returns True → runner stops before next step."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok",
                         loop_file="a.c", retry=0),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok",
                         loop_file="b.c", retry=0),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok",
                         loop_file="c.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

            # Shutdown after first step
            def shutdown_check():
                return True
            runner._shutdown_check = shutdown_check

            result = runner.run()

        # Should stop before executing any steps
        assert result["status"] == "passed"
        assert result["steps_completed"] == 0  # no steps executed


# ═══ step.modules edge cases ═══

class TestStepModulesEdgeCases:
    """step.modules edge cases."""

    def test_all_steps_filtered_out_returns_empty(self):
        """All steps restricted to other modules → empty compiled list."""
        config = PipelineConfig(
            repo="/tmp/fake",
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo ok",
                             modules=["A"]),
                PipelineStep(id="P2", executor="shell", prompt="echo ok",
                             modules=["A"]),
            ],
            modules=[
                Module(name="A", source_dir="src/"),
                Module(name="B", source_dir="src/"),
            ],
        )
        steps_a = PipelineCompiler(config).compile_module("A")
        steps_b = PipelineCompiler(config).compile_module("B")
        assert len(steps_a) == 2
        assert len(steps_b) == 0

    def test_modules_with_depends_on(self):
        """step.modules + depends_on: filtering works with dependency sort."""
        config = PipelineConfig(
            repo="/tmp/fake",
            pipeline=[
                PipelineStep(id="scaffold", executor="shell", prompt="echo ok"),
                PipelineStep(id="generate", executor="shell", prompt="echo ok",
                             depends_on="scaffold", modules=["auth"]),
                PipelineStep(id="evaluate", executor="shell", prompt="echo ok",
                             depends_on="generate", modules=["auth"]),
            ],
            modules=[
                Module(name="auth", source_dir="src/", source_files=["a.c"]),
                Module(name="util", source_dir="src/"),
            ],
        )
        steps_auth = PipelineCompiler(config).compile_module("auth")
        steps_util = PipelineCompiler(config).compile_module("util")
        assert len(steps_auth) == 3  # all steps
        assert len(steps_util) == 1  # only scaffold
        assert steps_util[0].step_id == "scaffold"


# ═══ on_failure clear_step_completed tests ═══

class TestClearStepCompleted:
    """on_failure jump clears completed marks for subsequent steps."""

    def test_clear_removes_existing_mark(self, tmp_path):
        """clear_step_completed removes a step from completed list."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("run1", {"auth": {"status": "running"}})
        sm.mark_step_completed("auth", "generate", "a.c")
        sm.mark_step_completed("auth", "evaluate", "a.c")

        before = sm.get_completed_steps("auth")
        assert "generate/a.c" in before

        sm.clear_step_completed("auth", "generate", "a.c")
        after = sm.get_completed_steps("auth")
        assert "generate/a.c" not in after
        assert "evaluate/a.c" in after  # not cleared

    def test_clear_nonexistent_step_no_error(self, tmp_path):
        """Clearing a step that was never marked → no error."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("run1", {"auth": {"status": "running"}})
        sm.mark_step_completed("auth", "generate", "a.c")

        # This should not raise
        sm.clear_step_completed("auth", "nonexistent", "a.c")
        steps = sm.get_completed_steps("auth")
        assert "generate/a.c" in steps

    def test_clear_no_loop_file(self, tmp_path):
        """clear_step_completed with empty loop_file matches step_id only."""
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("run1", {"auth": {"status": "running"}})
        sm.mark_step_completed("auth", "scaffold")

        sm.clear_step_completed("auth", "scaffold")
        assert "scaffold" not in sm.get_completed_steps("auth")
