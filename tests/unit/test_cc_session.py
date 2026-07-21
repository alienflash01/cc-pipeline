"""TDD tests for CC session resume feature."""
import pytest, subprocess, os, uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    for f in ["a.c", "b.c"]:
        (repo / f).write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ Layer 1: StateManager cc_sessions CRUD ═══

class TestCCSessionStateManager:
    def test_set_and_get_session(self, tmp_path):
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        uid = str(uuid.uuid4())
        sm.set_cc_session("m1", "generate", "a.c", uid)
        assert sm.get_cc_session("m1", "generate", "a.c") == uid

    def test_get_nonexistent_returns_none(self, tmp_path):
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        assert sm.get_cc_session("m1", "generate", "a.c") is None

    def test_clear_session(self, tmp_path):
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        uid = str(uuid.uuid4())
        sm.set_cc_session("m1", "generate", "a.c", uid)
        sm.clear_cc_session("m1", "generate", "a.c")
        assert sm.get_cc_session("m1", "generate", "a.c") is None

    def test_different_files_independent(self, tmp_path):
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        sm.set_cc_session("m1", "generate", "a.c", u1)
        sm.set_cc_session("m1", "generate", "b.c", u2)
        assert sm.get_cc_session("m1", "generate", "a.c") == u1
        assert sm.get_cc_session("m1", "generate", "b.c") == u2

    def test_different_modules_independent(self, tmp_path):
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}, "m2": {"status": "running"}})
        u1 = str(uuid.uuid4())
        u2 = str(uuid.uuid4())
        sm.set_cc_session("m1", "gen", "a.c", u1)
        sm.set_cc_session("m2", "gen", "a.c", u2)
        assert sm.get_cc_session("m1", "gen", "a.c") == u1
        assert sm.get_cc_session("m2", "gen", "a.c") == u2

    def test_clear_step_also_clears_session(self, tmp_path):
        """clear_step_completed should also clear cc_session."""
        sm = StateManager(run_dir=str(tmp_path / "runs"))
        sm.save("r1", {"m1": {"status": "running"}})
        uid = str(uuid.uuid4())
        sm.set_cc_session("m1", "generate", "a.c", uid)
        sm.mark_step_completed("m1", "generate", "a.c")
        sm.clear_step_completed("m1", "generate", "a.c")
        assert "generate/a.c" not in sm.get_completed_steps("m1")
        assert sm.get_cc_session("m1", "generate", "a.c") is None


# ═══ Layer 2: CCExecutor cmd construction ═══

class TestCCExecutorSessionCmd:
    def test_first_run_has_session_id(self):
        from cc_pipeline.executor import CCExecutor
        executor = CCExecutor()
        uid = str(uuid.uuid4())
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            executor.run("do something", "/tmp", session_id=uid, resume_session=False)
        cmd = mock_run.call_args[0][0]
        assert "--session-id" in cmd
        assert uid in cmd
        assert "--resume" not in cmd

    def test_resume_run_has_resume_flag(self):
        from cc_pipeline.executor import CCExecutor
        executor = CCExecutor()
        uid = str(uuid.uuid4())
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            executor.run("do something", "/tmp", session_id=uid, resume_session=True)
        cmd = mock_run.call_args[0][0]
        assert "--resume" in cmd
        assert uid in cmd
        assert "-p" in cmd

    def test_no_session_id_no_flags(self):
        from cc_pipeline.executor import CCExecutor
        executor = CCExecutor()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            executor.run("do something", "/tmp")
        cmd = mock_run.call_args[0][0]
        assert "--session-id" not in cmd
        assert "--resume" not in cmd


# ═══ Layer 3: runner retry loop session management ═══

class TestRunnerSessionRetry:
    def test_first_attempt_creates_session(self, git_repo):
        """First attempt generates UUID and passes session_id to executor."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                           rendered_prompt="ok", retry=1, output="out.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(git_repo / "runs"))
        sm.save("r1", {"mod": {"status": "running"}})
        runner.state_manager = sm

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner.run()

        # After PASS, session should be cleared
        assert sm.get_cc_session("mod", "gen", "") is None

    def test_timeout_retry_uses_resume(self, git_repo):
        """TIMEOUT retry calls CCExecutor with resume_session=True."""
        import subprocess as sp
        step = CompiledStep(step_id="gen", executor="claude-code",
                           rendered_prompt="ok", retry=2, output="out.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(git_repo / "runs"))
        sm.save("r1", {"mod": {"status": "running"}})
        runner.state_manager = sm

        call_modes = []
        with patch("subprocess.run") as mock_run:
            def se(cmd, **kw):
                if isinstance(cmd, list) and cmd[0].endswith("claude"):
                    if "--resume" in cmd:
                        call_modes.append("resume")
                    elif "--session-id" in cmd:
                        call_modes.append("new")
                    else:
                        call_modes.append("no_session")
                call_modes.append(0)
                # Timeout on first, pass on second
                if len(call_modes) <= 2:
                    raise sp.TimeoutExpired(cmd="claude", timeout=0.1)
                return MagicMock(returncode=0, stdout="ok", stderr="")
            mock_run.side_effect = se
            runner.run()

        assert "new" in call_modes  # first attempt
        assert "resume" in call_modes  # retry after timeout

    def test_cc_failed_retry_uses_new_session(self, git_repo):
        """CC_FAILED retry generates new UUID (not resume)."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                           rendered_prompt="ok", retry=1, output="out.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        from cc_pipeline.state import StateManager
        sm = StateManager(run_dir=str(git_repo / "runs"))
        sm.save("r1", {"mod": {"status": "running"}})
        runner.state_manager = sm

        uuids_seen = []
        with patch("subprocess.run") as mock_run:
            def se(cmd, **kw):
                if isinstance(cmd, list) and cmd[0].endswith("claude"):
                    # Extract session-id UUID
                    for i, a in enumerate(cmd):
                        if a == "--session-id" and i+1 < len(cmd):
                            uuids_seen.append(cmd[i+1])
                return MagicMock(returncode=1, stdout="", stderr="error")
            mock_run.side_effect = se
            runner.run()

        # Two different UUIDs — new session each time
        assert len(uuids_seen) >= 2
        assert uuids_seen[0] != uuids_seen[1]
