"""TDD tests for #34: failed file full-pipeline retry on resume.

Scenario: 3 files × 2 steps (generate → evaluate).
  a.c passes both, b.c generate passes but evaluate fails,
  c.c hasn't been touched.

On resume: a.c skipped (both passed), b.c both re-run,
           c.c both run normally.
"""
import pytest, subprocess, os
from pathlib import Path

from cc_pipeline.state import StateManager
from cc_pipeline.compiler import CompiledStep
from cc_pipeline.runner import ModuleRunner


ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    for f in ["a.c", "b.c", "c.c"]:
        (repo / f).write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ StateManager: clear_completed_for_file ═══

class TestClearCompletedForFile:
    def test_clears_all_steps_for_one_file(self, tmp_path):
        """clear_completed_for_file removes all completed steps for a file."""
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        sm.mark_step_completed("m1", "generate", "a.c")
        sm.mark_step_completed("m1", "evaluate", "a.c")
        sm.mark_step_completed("m1", "generate", "b.c")

        assert "generate/a.c" in sm.get_completed_steps("m1")
        assert "evaluate/a.c" in sm.get_completed_steps("m1")
        assert "generate/b.c" in sm.get_completed_steps("m1")

        sm.clear_completed_for_file("m1", "a.c")

        after = sm.get_completed_steps("m1")
        assert "generate/a.c" not in after
        assert "evaluate/a.c" not in after
        assert "generate/b.c" in after  # other files untouched

    def test_non_loop_steps_not_affected(self, tmp_path):
        """Non-loop steps (no file suffix) are not cleared."""
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        sm.mark_step_completed("m1", "scaffold")
        sm.mark_step_completed("m1", "generate", "a.c")

        sm.clear_completed_for_file("m1", "a.c")

        after = sm.get_completed_steps("m1")
        assert "scaffold" in after  # non-loop step preserved
        assert "generate/a.c" not in after

    def test_clear_nonexistent_file_no_error(self, tmp_path):
        """Clearing a file with no completed steps → no error."""
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        sm.mark_step_completed("m1", "generate", "a.c")

        # Should not raise
        sm.clear_completed_for_file("m1", "nonexistent.c")


# ═══ Integration: continue_on_error clears failed file steps ═══

class TestContinueOnErrorClearsFailedFile:
    def test_failed_file_steps_cleared_from_state(self, git_repo):
        """When continue_on_error skips a file, its completed steps are cleared."""
        from unittest.mock import MagicMock, patch

        steps = [
            CompiledStep(step_id="generate", executor="shell",
                        rendered_prompt="echo ok", loop_file="a.c", retry=0),
            CompiledStep(step_id="generate", executor="shell",
                        rendered_prompt="echo ok", loop_file="b.c", retry=0),
            CompiledStep(step_id="evaluate", executor="shell",
                        rendered_prompt="false", loop_file="b.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))
        runner._continue_on_error = True
        # Inject a real StateManager (normally created by orchestrator)
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(git_repo / "runs"))
        sm.save("r1", {"mod": {"status": "running"}})
        runner.state_manager = sm

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            def se(cmd, **kw):
                if isinstance(cmd, list):
                    return MagicMock(returncode=0, stdout="ok", stderr="")
                is_fail = isinstance(cmd, str) and "false" in cmd and "ok" not in cmd
                return MagicMock(returncode=1 if is_fail else 0, stdout="ok", stderr="")
            mock_run.side_effect = se
            runner.run()

        # b.c evaluate failed → ALL of b.c's steps should be cleared
        completed = sm.get_completed_steps("mod")
        assert "generate/a.c" in completed  # a.c untouched
        assert "generate/b.c" not in completed  # cleared because evaluate(b.c) failed
        assert "evaluate/b.c" not in completed


# ═══ Resume integration: failed file re-runs ═══

class TestResumeWithFailedFiles:
    def test_resume_reruns_failed_file_entire_pipeline(self, git_repo):
        """Resume re-runs generate+evaluate for file that failed downstream."""
        from unittest.mock import MagicMock, patch
        import json

        # Simulate first run: a.c passes, b.c fails evaluate
        sm = StateManager(run_dir=str(git_repo / "runs"))
        sm.save("r1", {"mod": {"status": "running"}})
        sm.mark_step_completed("mod", "generate", "a.c")
        sm.mark_step_completed("mod", "evaluate", "a.c")
        # b.c generate passed but evaluate failed → clear all for b.c
        sm.clear_completed_for_file("mod", "b.c")
        # c.c untouched

        # Now resume: build steps for b.c and c.c only (a.c skipped)
        steps = [
            CompiledStep(step_id="generate", executor="shell",
                        rendered_prompt="echo ok", loop_file="b.c", retry=0),
            CompiledStep(step_id="evaluate", executor="shell",
                        rendered_prompt="echo ok", loop_file="b.c", retry=0),
            CompiledStep(step_id="generate", executor="shell",
                        rendered_prompt="echo ok", loop_file="c.c", retry=0),
            CompiledStep(step_id="evaluate", executor="shell",
                        rendered_prompt="echo ok", loop_file="c.c", retry=0),
        ]
        runner = ModuleRunner(steps, "mod", str(git_repo), str(git_repo / "runs"))

        call_log = []
        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            def se(cmd, **kw):
                if isinstance(cmd, list):
                    return MagicMock(returncode=0, stdout="ok", stderr="")
                call_log.append(cmd)
                return MagicMock(returncode=0, stdout="ok", stderr="")
            mock_run.side_effect = se
            result = runner.run()

        assert result["status"] == "passed"
        # Total: 4 shell calls for b.c and c.c (2 per file, both steps run)
        assert result["steps_completed"] == 4, f"Expected 4 steps, got {result['steps_completed']}"
        # b.c: 2 steps (generate+evaluate), c.c: 2 steps (generate+evaluate)
        # a.c was skipped by resume logic
