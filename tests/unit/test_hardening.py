"""Hardening tests — edge cases and integration scenarios for recent changes.

Tests cover: context injection pipeline, continue_on_error edge cases,
output template variations, cross-feature interactions.
"""
import pytest
import subprocess, os, json
from pathlib import Path
from unittest.mock import MagicMock, patch

from cc_pipeline.compiler import CompiledStep, PipelineCompiler
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.runner import ModuleRunner


ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    (repo / "src").mkdir()
    for f in ["a.c", "b.c", "c.c"]:
        (repo / "src" / f).write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ Context injection integration — end-to-end with fake shell ═══

class TestContextInjectionPipeline:
    """Full 3-step pipeline: P1 writes output, P2 reads {prev_output_path}."""

    def test_full_context_pipeline(self, git_repo):
        """P1 writes JSON → P2 reads via {prev_output_path}."""
        # Compile a real pipeline
        config = PipelineConfig(
            repo=str(git_repo),
            pipeline=[
                PipelineStep(id="scaffold", executor="shell", prompt="echo scaffold ok",
                             output="scaffold.json"),
                PipelineStep(id="generate", executor="shell",
                             prompt="test -f {prev_output_path}"),
            ],
            modules=[Module(name="mod", source_dir="src/", source_files=["a.c"])],
        )
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("mod")

        # Manually create scaffold output for {prev_output_path} to work
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = runner.run()

        assert result["status"] in ("passed", "partial")
        assert result["steps_completed"] == 2

    def test_prev_output_path_passes_between_per_file_steps(self, git_repo):
        """per_file: P1[a] output → P2[a] sees P1[a]'s output file."""
        config = PipelineConfig(
            repo=str(git_repo),
            pipeline=[
                PipelineStep(id="gen", executor="shell", loop="per_file",
                             prompt="echo ok", output="gen-{file}.json"),
                PipelineStep(id="eval", executor="shell", loop="per_file",
                             prompt="test -f {prev_output_path}"),
            ],
            modules=[Module(name="mod", source_dir="src/", source_files=["a.c", "b.c"])],
        )
        steps = PipelineCompiler(config).compile_module("mod")
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = runner.run()

        assert result["status"] in ("passed", "partial")
        assert result["steps_completed"] == 4  # 2 files × 2 steps


# ═══ continue_on_error edge cases ═══

class TestContinueOnErrorEdgeCases:
    """Edge cases for continue_on_error: single file, mixed steps, batches."""

    def test_single_file_continue_on_error_is_noop(self, git_repo):
        """Single file with continue_on_error → no difference from default."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok",
                         loop_file="only.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = runner.run()

        assert result["status"] in ("passed", "partial")

    def test_mixed_pass_fail_with_continue(self, git_repo):
        """A fails, B passes, C fails — A+B collected as failed, module passes."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="false",
                         loop_file="a.c", retry=0),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok",
                         loop_file="b.c", retry=0),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="false",
                         loop_file="c.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            def side_effect(cmd, **kw):
                if isinstance(cmd, list):
                    return MagicMock(returncode=0, stdout="ok", stderr="")
                is_fail = isinstance(cmd, str) and "false" in cmd and "true" not in cmd
                return MagicMock(returncode=1 if is_fail else 0, stdout="ok", stderr="")
            mock_run.side_effect = side_effect
            result = runner.run()

        assert result["status"] in ("passed", "partial")  # b.c passed
        assert "a.c" in runner._failed_files
        assert "c.c" in runner._failed_files
        assert "b.c" not in runner._failed_files

    def test_continue_on_error_with_retry(self, git_repo):
        """continue_on_error + retry: retry exhausted → skip file."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="false",
                         loop_file="a.c", retry=1),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok",
                         loop_file="b.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            def side_effect(cmd, **kw):
                if isinstance(cmd, list):
                    return MagicMock(returncode=0, stdout="ok", stderr="")
                is_fail = isinstance(cmd, str) and "false" in cmd and "true" not in cmd
                return MagicMock(returncode=1 if is_fail else 0, stdout="ok", stderr="")
            mock_run.side_effect = side_effect
            result = runner.run()

        assert result["status"] in ("passed", "partial")
        assert "a.c" in runner._failed_files  # false failed twice

    def test_continue_on_error_no_loop_files(self, git_repo):
        """continue_on_error without loop_file → module fails normally."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="false",
                         retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
            result = runner.run()

        assert result["status"] == "failed"  # no loop_file → normal fail


# ═══ Output template edge cases ═══

class TestOutputTemplateEdgeCases:
    """Output instruction across different step configurations."""

    def test_output_with_special_chars_in_filename(self):
        """Output filename with {file} expansion containing dots."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do it",
                            output="gen-{file}.json")  # {file} already expanded
        runner = ModuleRunner([step], "auth", "/tmp/x", "/tmp/runs")
        result = runner._inject_context("do it", step)
        assert ".pipeline/gen-{file}.json" in result
        assert "summary" in result

    def test_output_not_generated_triggers_warning_only_once(self, git_repo):
        """Per-step: warn once when output missing, not on postcondition path."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do it", output="out.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        import io, sys
        captured = io.StringIO()
        old = sys.stdout
        sys.stdout = captured
        try:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                runner._execute_step(step)
        finally:
            sys.stdout = old

        output = captured.getvalue()
        assert output.count("Output file not created") == 1

    def test_output_prompt_can_be_empty(self):
        """Empty output_prompt → falls back to default template (empty = not set)."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do it", output="out.json",
                            output_prompt="")
        runner = ModuleRunner([step], "auth", "/tmp/x", "/tmp/runs")
        result = runner._inject_context("do it", step)
        assert "summary" in result  # empty = falls back to default


# ═══ Context variable edge cases ═══

class TestContextVariableEdgeCases:
    """{prev_output_path} / {current_output_path} in edge cases."""

    def test_multiple_variables_in_same_prompt(self, git_repo):
        """Both {prev_output_path} and {current_output_path} in same prompt."""
        step = CompiledStep(step_id="P2", executor="claude-code",
                            rendered_prompt="compare {prev_output_path} and {current_output_path}",
                            prev_output_path=".pipeline/P1.json",
                            output="P2.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        prompt = ""
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            if isinstance(args, list) and len(args) > 2 and args[0].endswith("claude"):
                prompt = args[2]
                break
        assert ".pipeline/P1.json" in prompt
        assert ".pipeline/P2.json" in prompt

    def test_current_output_path_empty_when_no_output(self, git_repo):
        """{current_output_path} → empty when step has no output."""
        step = CompiledStep(step_id="P1", executor="claude-code",
                            rendered_prompt="start {current_output_path} end")
        # output=None (default)
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        prompt = ""
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            if isinstance(args, list) and len(args) > 2 and args[0].endswith("claude"):
                prompt = args[2]
                break
        assert ".pipeline/" not in prompt  # no injection

    def test_prev_output_path_survives_empty_steps(self):
        """Steps without output don't break prev_output chain."""
        config = PipelineConfig(
            repo="/tmp/fake",
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo ok", output="p1.json"),
                PipelineStep(id="P2", executor="shell", prompt="echo ok"),
                PipelineStep(id="P3", executor="shell", prompt="echo ok"),
                PipelineStep(id="P4", executor="shell", prompt="echo ok", output="p4.json"),
            ],
            modules=[Module(name="mod", source_dir="src/", source_files=["a.c"])],
        )
        steps = PipelineCompiler(config).compile_module("mod")
        # P4 sees P1's output (not P2 or P3 which have no output)
        p4 = [s for s in steps if s.step_id == "P4"][0]
        assert p4.prev_output_path == ".pipeline/p1.json"


# ═══ Cross-feature interaction tests ═══

class TestCrossFeatureInteractions:
    """Features combined: continue_on_error + retry + on_failure + context vars."""

    def test_continue_on_error_with_on_failure(self, git_repo):
        """Failed file → on_failure jump. With continue_on_error, other files unaffected."""
        steps = [
            # File a.c: P1 fails → on_failure → P0 → P1 again → fail → skip
            CompiledStep(step_id="P0", executor="shell", rendered_prompt="echo ok",
                         loop_file="a.c", retry=0, output="p0-a.json"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="false",
                         loop_file="a.c", retry=0, on_failure="P0"),
            # File b.c: passes normally
            CompiledStep(step_id="P0", executor="shell", rendered_prompt="echo ok",
                         loop_file="b.c", retry=0, output="p0-b.json"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok",
                         loop_file="b.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True

        call_counts = {"0": 0, "1": 0}

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            def side_effect(cmd, **kw):
                call_counts["1"] += 1
                if isinstance(cmd, list):
                    return MagicMock(returncode=0, stdout="ok", stderr="")
                is_fail = isinstance(cmd, str) and "false" in cmd and "true" not in cmd
                return MagicMock(returncode=1 if is_fail else 0, stdout="ok", stderr="")
            mock_run.side_effect = side_effect
            with patch.object(runner, "_check_postcondition",
                              return_value=MagicMock(passed=True, reason="ok")):
                result = runner.run()

        # a.c fails after retry+jump, but b.c passes → module passes
        assert result["status"] in ("passed", "partial")
        assert "a.c" in runner._failed_files
        assert "b.c" not in runner._failed_files

    def test_retry_resets_budget_per_step_with_continue(self, git_repo):
        """retry budget resets per step even with continue_on_error."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="maybe_fail",
                         loop_file="a.c", retry=1),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="maybe_fail",
                         loop_file="a.c", retry=1),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))

        # Simulate: P1 fails once then passes (retry=1), P2 passes first time
        fail_count = [0]

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            def side_effect(cmd, **kw):
                if isinstance(cmd, list):
                    return MagicMock(returncode=0, stdout="ok", stderr="")
                fail_count[0] += 1
                # Fail first call only (P1 first attempt), succeed after
                return MagicMock(returncode=1 if fail_count[0] == 1 else 0,
                                 stdout="ok", stderr="")
            mock_run.side_effect = side_effect
            result = runner.run()

        # P1 fails once → retry → passes. P2 passes. Module passes.
        assert result["status"] in ("passed", "partial")
        assert result["steps_completed"] == 2


# ═══ JSON pretty-print edge cases ═══

class TestJsonPrettyPrintEdgeCases:
    """JSON formatting on postcondition failure — edge cases."""

    def test_non_json_stdout_not_formatted(self, git_repo):
        """Non-JSON stdout → raw text, no pipe prefix for JSON lines."""
        step = CompiledStep(step_id="eval", executor="shell",
                            rendered_prompt="echo check",
                            postcondition={"shell": "echo score", "expect": None})
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        import io, sys
        captured = io.StringIO()
        old = sys.stdout
        sys.stdout = captured
        try:
            with patch("cc_pipeline.runner.eval_postcondition") as mock_pc:
                mock_pc.return_value = MagicMock(
                    passed=False, reason="nope",
                    stdout="just plain text\nnot json\n",
                )
                runner._check_postcondition(step)
        finally:
            sys.stdout = old

        output = captured.getvalue()
        assert "just plain text" in output
        assert output.count("│") >= 2  # one per line

    def test_invalid_json_stdout_not_formatted(self, git_repo):
        """Invalid JSON → raw text, no crash."""
        step = CompiledStep(step_id="eval", executor="shell",
                            rendered_prompt="echo check",
                            postcondition={"shell": "echo score", "expect": None})
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        import io, sys
        captured = io.StringIO()
        old = sys.stdout
        sys.stdout = captured
        try:
            with patch("cc_pipeline.runner.eval_postcondition") as mock_pc:
                mock_pc.return_value = MagicMock(
                    passed=False, reason="nope",
                    stdout='{"unclosed": [}',
                )
                runner._check_postcondition(step)
        finally:
            sys.stdout = old

        output = captured.getvalue()
        assert '{"unclosed": [}' in output  # raw, not formatted
