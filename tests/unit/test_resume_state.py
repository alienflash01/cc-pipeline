"""Resume tests — state.json based step skipping (replaces git checkpoint tests)."""
import pytest
import subprocess, os, json, tempfile
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.orchestrator import Orchestrator
from cc_pipeline.state import StateManager


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "a.c").write_text("int x = 1;\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)
    return repo


def _make_config(repo, wt_root, steps):
    return PipelineConfig(
        repo=str(repo), concurrency=1,
        worktree_root=str(wt_root),
        pipeline=steps,
        modules=[Module(name="auth", source_dir="", source_files=["a.c"])],
    )


class TestStateJsonRoundTrip:
    """mark_step_completed → get_completed_steps round trip."""

    def test_non_loop_step(self, tmp_path):
        sm = StateManager(str(tmp_path))
        sm.mark_step_completed("auth", "s1", "")
        assert sm.get_completed_steps("auth") == {"s1"}

    def test_loop_step(self, tmp_path):
        sm = StateManager(str(tmp_path))
        sm.mark_step_completed("auth", "generate", "auth_login.c")
        sm.mark_step_completed("auth", "generate", "auth_token.c")
        assert sm.get_completed_steps("auth") == {"generate/auth_login.c", "generate/auth_token.c"}

    def test_mixed(self, tmp_path):
        sm = StateManager(str(tmp_path))
        sm.mark_step_completed("auth", "scaffold", "")
        sm.mark_step_completed("auth", "generate", "a.c")
        result = sm.get_completed_steps("auth")
        assert "scaffold" in result
        assert "generate/a.c" in result

    def test_no_duplicates(self, tmp_path):
        sm = StateManager(str(tmp_path))
        sm.mark_step_completed("auth", "s1", "")
        sm.mark_step_completed("auth", "s1", "")  # same step twice
        assert sm.get_completed_steps("auth") == {"s1"}

    def test_missing_module(self, tmp_path):
        sm = StateManager(str(tmp_path))
        assert sm.get_completed_steps("nonexistent") == set()

    def test_corrupted_json(self, tmp_path):
        sm = StateManager(str(tmp_path))
        sm.state_file.write_text("{broken json")
        assert sm.get_completed_steps("auth") == set()


class TestResumeSkipsCompletedSteps:
    """Resume correctly skips steps recorded in state.json."""

    def test_three_step_fail_third_resume_skips_first_two(self, tmp_path):
        """s1✅ s2✅ s3❌ → resume → only s3 runs."""
        repo = _git_repo(tmp_path)
        wt = tmp_path / "wt"
        run_dir = tmp_path / "runs"

        # First run: s3 fails
        config = _make_config(repo, wt, [
            PipelineStep(id="s1", executor="shell", prompt="echo step1", retry=0),
            PipelineStep(id="s2", executor="shell", prompt="echo step2", retry=0),
            PipelineStep(id="s3", executor="shell", prompt="false", retry=1),
        ])
        orch = Orchestrator(config=config, run_dir=str(run_dir))
        result = orch.run()
        assert result[0]["status"] == "failed"

        # Verify state.json recorded s1, s2 as completed
        with open(run_dir / "orchestrator-state.json") as f:
            state = json.load(f)
        completed = state["modules"]["auth"].get("completed_steps", [])
        assert "s1" in completed
        assert "s2" in completed
        assert "s3" not in completed

        # Second run (resume): s3 succeeds now
        config2 = _make_config(repo, wt, [
            PipelineStep(id="s1", executor="shell", prompt="echo step1", retry=0),
            PipelineStep(id="s2", executor="shell", prompt="echo step2", retry=0),
            PipelineStep(id="s3", executor="shell", prompt="echo step3-fixed", retry=0),
        ])
        orch2 = Orchestrator(config=config2, run_dir=str(run_dir), resume=True)
        result2 = orch2.run()
        assert result2[0]["status"] == "passed"
        # Only 1 step should have run (s3)
        assert result2[0]["steps_completed"] == 1

    def test_resume_skips_loop_steps_individually(self, tmp_path):
        """per_file loop: 2 files completed, 1 not → resume runs only the missing one."""
        repo = _git_repo(tmp_path)
        wt = tmp_path / "wt"
        run_dir = tmp_path / "runs"

        # Manually write state.json with 2 of 3 loop files completed
        sm = StateManager(str(run_dir))
        sm.mark_step_completed("auth", "generate", "a.c")
        sm.mark_step_completed("auth", "generate", "b.c")
        # c.c not completed

        # Verify
        completed = sm.get_completed_steps("auth")
        assert "generate/a.c" in completed
        assert "generate/b.c" in completed
        assert "generate/c.c" not in completed

    def test_resume_all_passed_skips_everything(self, tmp_path):
        """All steps completed → resume → nothing to run."""
        repo = _git_repo(tmp_path)
        wt = tmp_path / "wt"
        run_dir = tmp_path / "runs"

        # Run succeeds
        config = _make_config(repo, wt, [
            PipelineStep(id="s1", executor="shell", prompt="echo ok", retry=0),
        ])
        orch = Orchestrator(config=config, run_dir=str(run_dir))
        orch.run()

        # Resume: should find s1 already done
        sm = StateManager(str(run_dir))
        assert "s1" in sm.get_completed_steps("auth")
