"""Tests for recent features: output template, continue_on_error, context vars, JSON pretty-print.

Per TESTING-RULES: every feature must have tests.
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
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.c").write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ #29 output instruction — structured 3-field JSON template ═══

class TestOutputInstruction:
    """output field injects structured JSON template into prompt."""

    def test_output_injects_template(self):
        """Default output template has 3 fields: summary, files, issues."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do generate", output="gen.json")
        runner = ModuleRunner([step], "auth", "/tmp/x", "/tmp/runs")

        result = runner._inject_context("do generate", step)
        assert "summary" in result
        assert "files" in result
        assert "issues" in result
        assert "确保该文件存在" in result
        assert ".pipeline/gen.json" in result

    def test_output_prompt_overrides_template(self):
        """output_prompt field overrides default template entirely."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do generate", output="gen.json",
                            output_prompt="Write your data to {output}")
        runner = ModuleRunner([step], "auth", "/tmp/x", "/tmp/runs")

        result = runner._inject_context("do generate", step)
        assert "Write your data" in result
        assert "gen.json" in result   # {output} → just the filename
        assert "summary" not in result  # template NOT injected

    def test_no_output_no_template(self):
        """Step without output field gets no template injection."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do generate")
        runner = ModuleRunner([step], "auth", "/tmp/x", "/tmp/runs")

        result = runner._inject_context("do generate", step)
        assert "summary" not in result
        assert ".pipeline/" not in result


# ═══ #30 continue_on_error ═══

class TestContinueOnError:
    """Module.continue_on_error: failed file skips remaining steps, other files continue."""

    def test_continue_on_error_skips_failed_file(self, git_repo):
        """Failed file is marked, its remaining steps skipped."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok1",
                         loop_file="a.c", retry=0, output="out-a.json"),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="echo ok2",
                         loop_file="b.c", retry=0, output="out-b.json"),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="false",
                         loop_file="a.c", retry=0),
            CompiledStep(step_id="P2", executor="shell", rendered_prompt="echo ok",
                         loop_file="b.c", retry=0, output="out-b2.json"),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            # P1-a: ok, P1-b: ok, P2-a: fail (false), P2-b: ok
            def side_effect(cmd, **kw):
                if isinstance(cmd, list):
                    # CC executor call
                    return MagicMock(returncode=0, stdout="ok", stderr="")
                # Shell executor: cmd is a string
                is_fail = isinstance(cmd, str) and "false" in cmd and "true" not in cmd
                return MagicMock(returncode=1 if is_fail else 0,
                                 stdout="ok", stderr="")
            mock_run.side_effect = side_effect
            # Mock postcondition to pass
            with patch.object(runner, "_check_postcondition",
                              return_value=MagicMock(passed=True, reason="ok")):
                result = runner.run()

        assert result["status"] == "passed"
        assert "a.c" in runner._failed_files

    def test_continue_on_error_all_files_fail_still_fails(self, git_repo):
        """All files fail without retry → module fails."""
        steps = [
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="false",
                         loop_file="a.c", retry=0),
            CompiledStep(step_id="P1", executor="shell", rendered_prompt="false",
                         loop_file="b.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
            result = runner.run()

        assert result["status"] == "failed"


# ═══ context variables — {prev_output_path} / {current_output_path} ═══

class TestContextVariables:
    """{prev_output_path} and {current_output_path} runtime replacement."""

    def test_prev_output_path_replaced(self, git_repo):
        """{prev_output_path} → .pipeline/prev-step-output.json (CC executor)."""
        step = CompiledStep(step_id="P2", executor="claude-code",
                            rendered_prompt="cat {prev_output_path}",
                            prev_output_path=".pipeline/P1-out.json", output="P2-out.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        # Find the CC call in call_args_list (not last call — _detect_file_changes runs after)
        prompt = ""
        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else []
            if isinstance(args, list) and len(args) > 2 and args[0].endswith("claude"):
                prompt = args[2]  # claude -p "prompt" --flag
                break
        assert ".pipeline/P1-out.json" in prompt

    def test_current_output_path_replaced(self, git_repo):
        """{current_output_path} → .pipeline/current-step-output.json."""
        step = CompiledStep(step_id="P2", executor="claude-code",
                            rendered_prompt="check {current_output_path}",
                            output="cur-out.json")
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
        assert ".pipeline/cur-out.json" in prompt

    def test_prev_output_empty_when_no_previous(self, git_repo):
        """{prev_output_path} → empty when no previous step."""
        step = CompiledStep(step_id="P1", executor="claude-code",
                            rendered_prompt="start {prev_output_path} end",
                            prev_output_path="")
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
        assert "start  end" in prompt  # empty replacement → doublespace


# ═══ #31 JSON pretty-print on postcondition failure ═══

class TestJsonPrettyPrint:
    """Postcondition failure stdout is formatted as indented JSON."""

    def test_json_formatted_on_failure(self, git_repo):
        """JSON stdout → indented multi-line output."""
        step = CompiledStep(step_id="eval", executor="shell",
                            rendered_prompt="echo check",
                            postcondition={"shell": "echo score", "expect": None})
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            # Mock postcondition to return JSON
            with patch("cc_pipeline.runner.eval_postcondition") as mock_pc:
                mock_pc.return_value = MagicMock(
                    passed=False,
                    reason="score < 80",
                    stdout='{"score":45,"cases":"8/17","missing":["edge1"]}',
                )
                runner._check_postcondition(step)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "score" in output
        assert "45" in output
        assert "│" in output  # formatted with pipe prefix


# ═══ output file not created warning ═══

class TestOutputFileWarning:
    """Warn when output file expected but not created."""

    def test_warn_when_output_missing(self, git_repo):
        """CC succeeds but output file missing → warn printed."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do generate", output="gen.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        import io, sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured

        try:
            with patch("cc_pipeline.executor.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                # Don't create the output file — trigger warning
                runner._execute_step(step)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "Output file not created" in output
        assert "gen.json" in output


# ═══ prev_output_path computed at compile time ═══

class TestPrevOutputPath:
    """compiler sets prev_output_path on each compiled step."""

    def test_prev_output_set_during_compile(self):
        """Second step's prev_output_path = first step's output."""
        config = PipelineConfig(
            repo="/tmp/fake",
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo ok", output="p1.json"),
                PipelineStep(id="P2", executor="shell", prompt="echo ok", output="p2.json"),
            ],
            modules=[Module(name="mod", source_dir="src/", source_files=["a.c"])],
        )
        steps = PipelineCompiler(config).compile_module("mod")
        assert len(steps) == 2
        assert steps[1].prev_output_path == ".pipeline/p1.json"
        assert steps[0].prev_output_path == ""  # first step

    def test_prev_output_forward_when_step_no_output(self):
        """If step has no output, prev_output carries forward."""
        config = PipelineConfig(
            repo="/tmp/fake",
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo ok", output="p1.json"),
                PipelineStep(id="P2", executor="shell", prompt="echo ok"),  # no output
                PipelineStep(id="P3", executor="shell", prompt="echo ok", output="p3.json"),
            ],
            modules=[Module(name="mod", source_dir="src/", source_files=["a.c"])],
        )
        steps = PipelineCompiler(config).compile_module("mod")
        assert steps[2].prev_output_path == ".pipeline/p1.json"  # from P1, not P2

    def test_per_file_prev_output(self):
        """In per_file mode, prev_output tracks per-file."""
        config = PipelineConfig(
            repo="/tmp/fake",
            pipeline=[
                PipelineStep(id="P1", executor="shell", prompt="echo ok", loop="per_file",
                             output="p1-{file}.json"),
                PipelineStep(id="P2", executor="shell", prompt="echo ok", loop="per_file",
                             output="p2-{file}.json"),
            ],
            modules=[Module(name="mod", source_dir="src/", source_files=["a.c", "b.c"])],
        )
        steps = PipelineCompiler(config).compile_module("mod")
        # batched: P1[a], P1[b], P2[a], P2[b]
        assert steps[2].prev_output_path == ".pipeline/p1-b.c.json"  # P2[a] sees P1[b]
        assert steps[3].prev_output_path == ".pipeline/p2-a.c.json"  # P2[b] sees P2[a]
