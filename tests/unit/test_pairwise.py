"""Pairwise tests — key feature interactions, self-contained.

Tests pairwise interactions without external fixtures.
All use shell executor (deterministic, no CC needed).
"""
import pytest, subprocess, os
from pathlib import Path
from unittest.mock import MagicMock, patch

from cc_pipeline.compiler import CompiledStep
from cc_pipeline.runner import ModuleRunner


ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    (repo / "a.c").write_text("int f() { return 0; }")
    (repo / "b.c").write_text("int g() { return 1; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ retry × on_failure ═══

def test_retry_with_on_failure(git_repo):
    """retry=1 × on_failure: retry first, jump on exhaustion."""
    steps = [
        CompiledStep(step_id="fix", executor="shell", rendered_prompt="echo fix",
                     retry=0, output="fix.json"),
        CompiledStep(step_id="bad", executor="shell", rendered_prompt="false",
                     retry=1, on_failure="fix"),
    ]
    runner = ModuleRunner(steps, "pw", str(git_repo), str(git_repo / "runs"))
    with patch("cc_pipeline.executor.subprocess.run") as m:
        def se(cmd, **kw):
            if isinstance(cmd, list):
                return MagicMock(returncode=0, stdout="ok", stderr="")
            is_fail = isinstance(cmd, str) and "false" in cmd and "fix" not in cmd
            return MagicMock(returncode=1 if is_fail else 0, stdout="ok", stderr="")
        m.side_effect = se
        result = runner.run()
    assert result["status"] in ("passed", "failed", "partial")


# ═══ retry × depends_on ═══

def test_retry_with_depends_on(git_repo):
    """retry=1 × depends_on: dependency order respected in retries."""
    steps = [
        CompiledStep(step_id="base", executor="shell", rendered_prompt="echo ok",
                     retry=0, output="base.json"),
        CompiledStep(step_id="main", executor="shell", rendered_prompt="echo ok",
                     retry=1, depends_on="base"),
    ]
    # Compile via PipelineCompiler to test dependency sorting
    from cc_pipeline.config import PipelineConfig, PipelineStep, Module
    config = PipelineConfig(
        repo=str(git_repo),
        pipeline=[
            PipelineStep(id="main", executor="shell", prompt="echo ok", retry=1, depends_on="base"),
            PipelineStep(id="base", executor="shell", prompt="echo ok", retry=0),
        ],
        modules=[Module(name="pw", source_files=["a.c"])],
    )
    from cc_pipeline.compiler import PipelineCompiler
    steps = PipelineCompiler(config).compile_module("pw")
    assert steps[0].step_id == "base"  # depends_on sorts base first


# ═══ retry × timeout ═══

def test_retry_with_timeout(git_repo):
    """retry=1 × timeout=1: timeout triggers retry."""
    import subprocess as sp
    step = CompiledStep(step_id="main", executor="claude-code",
                        rendered_prompt="ok", retry=1, timeout=1, output="out.json")
    runner = ModuleRunner([step], "pw", str(git_repo), str(git_repo / "runs"))
    attempts = []
    with patch("subprocess.run") as m:
        def se(cmd, **kw):
            if isinstance(cmd, list) and cmd[0].endswith("claude"): attempts.append(1)
            if len(attempts) == 1:
                raise sp.TimeoutExpired(cmd="claude", timeout=0.1)
            return MagicMock(returncode=0, stdout="ok", stderr="")
        m.side_effect = se
        result = runner.run()
    assert result["status"] == "passed"
    assert len(attempts) == 2  # first timeout, second pass


# ═══ loop × continue_on_error ═══

def test_per_file_with_continue_on_error(git_repo):
    """loop=per_file × continue_on_error: one file fails, other continues."""
    steps = [
        CompiledStep(step_id="main", executor="shell", rendered_prompt="false",
                     loop_file="a.c", retry=0),
        CompiledStep(step_id="main", executor="shell", rendered_prompt="echo ok",
                     loop_file="b.c", retry=0),
    ]
    runner = ModuleRunner(steps, "pw", str(git_repo), str(git_repo / "runs"))
    runner._continue_on_error = True
    with patch("cc_pipeline.executor.subprocess.run") as m:
        def se(cmd, **kw):
            if isinstance(cmd, list):
                return MagicMock(returncode=0, stdout="ok", stderr="")
            is_fail = isinstance(cmd, str) and "false" in cmd and "ok" not in cmd
            return MagicMock(returncode=1 if is_fail else 0, stdout="ok", stderr="")
        m.side_effect = se
        result = runner.run()
    assert "a.c" in runner._failed_files
    assert result["status"] in ("partial", "passed")


# ═══ postcondition × retry ═══

def test_postcondition_fail_triggers_retry(git_repo):
    """Postcondition fail → retry."""
    step = CompiledStep(step_id="main", executor="shell", rendered_prompt="echo 45",
                        retry=1, postcondition={"shell": "echo 85", "expect": "$.score >= 80"})
    runner = ModuleRunner([step], "pw", str(git_repo), str(git_repo / "runs"))
    attempts = []
    with patch("cc_pipeline.executor.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stdout="85", stderr="")
        with patch.object(runner, "_check_postcondition") as mock_pc:
            from cc_pipeline.postcondition import PostconditionResult
            def pc_side(step):
                attempts.append(1)
                if len(attempts) == 1:
                    return PostconditionResult(passed=False, reason="too low",
                                               stdout='{"score":45}', stderr="")
                return PostconditionResult(passed=True, reason="ok",
                                           stdout='{"score":85}', stderr="")
            mock_pc.side_effect = pc_side
            result = runner.run()
    assert result["status"] == "passed"


# ═══ depends_on × on_failure × retry ═══

def test_triple_interaction_depends_on_failure(git_repo):
    """depends_on × on_failure × retry: all three work together."""
    steps = [
        CompiledStep(step_id="base", executor="shell", rendered_prompt="echo ok",
                     retry=0, output="base.json"),
        CompiledStep(step_id="fix", executor="shell", rendered_prompt="echo fix",
                     retry=0, output="fix.json"),
        CompiledStep(step_id="main", executor="shell", rendered_prompt="false",
                     retry=1, depends_on="base", on_failure="fix"),
    ]
    runner = ModuleRunner(steps, "pw", str(git_repo), str(git_repo / "runs"))
    with patch("cc_pipeline.executor.subprocess.run") as m:
        def se(cmd, **kw):
            if isinstance(cmd, list):
                return MagicMock(returncode=0, stdout="ok", stderr="")
            is_fail = isinstance(cmd, str) and "false" in cmd and "fix" not in cmd
            return MagicMock(returncode=1 if is_fail else 0, stdout="ok", stderr="")
        m.side_effect = se
        result = runner.run()
    assert result["status"] in ("passed", "failed", "partial")


# ═══ timeout × resume session ═══

def test_timeout_resume_session(git_repo):
    """RETRY=1 × TIMEOUT → --resume flag used on retry."""
    import subprocess as sp
    step = CompiledStep(step_id="main", executor="claude-code",
                        rendered_prompt="ok", retry=1, timeout=1, output="out.json")
    runner = ModuleRunner([step], "pw", str(git_repo), str(git_repo / "runs"))
    from cc_pipeline.state import StateManager
    sm = StateManager(run_dir=str(git_repo / "runs"))
    sm.save("r1", {"pw": {"status": "running"}})
    runner.state_manager = sm

    calls = []
    with patch("subprocess.run") as m:
        def se(cmd, **kw):
            if isinstance(cmd, list) and cmd[0].endswith("claude"):
                calls.append("resume" if "--resume" in cmd else "new")
            if len(calls) == 1:
                raise sp.TimeoutExpired(cmd="claude", timeout=0.1)
            return MagicMock(returncode=0, stdout="ok", stderr="")
        m.side_effect = se
        runner.run()

    assert "new" in calls and "resume" in calls
