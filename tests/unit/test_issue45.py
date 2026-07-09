"""TDD tests for GitHub Issue #4 (verbose timestamp) and Issue #5 (log_cc_result).

Issue #4: verbose mode prints a [HH:MM:SS] timestamp.
Issue #5: Logger records CC runtime result (returncode/stdout/stderr) and the
         transcript command renders those cc_result events.
"""
import json
import os
import re
import subprocess

import pytest
from pathlib import Path


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
}


@pytest.fixture
def git_repo(tmp_path):
    """Minimal git repo so state.json works during a passing run."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class _FakeCC:
    """Fake CC executor that always succeeds with recognizable stdout."""

    def __init__(self, **kw):
        pass

    def run(self, prompt, cwd, allowed_tools=None, **kw):
        from cc_pipeline.executor import CCResult
        return CCResult(returncode=0, stdout="all tests passed\n3 files created", stderr="")


# --- Issue #4: verbose timestamp --------------------------------------------

class TestVerboseTimestamp:
    """verbose START/PASS lines carry a [HH:MM:SS] timestamp."""

    def test_start_line_has_timestamp(self, git_repo, capsys):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="scaffold", executor="claude-code",
            rendered_prompt="scaffold", postcondition=None, retry=0,
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=_FakeCC(),
            verbose=True,
        )
        runner.run()
        out = capsys.readouterr().out

        # Expect: "  [HH:MM:SS] [auth] scaffold      START"
        assert re.search(r"\[\d{2}:\d{2}:\d{2}\]\s+\[auth\]\s+scaffold\s+START", out), (
            f"START line missing [HH:MM:SS] timestamp:\n{out}"
        )

    def test_pass_line_has_timestamp(self, git_repo, capsys):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="scaffold", executor="claude-code",
            rendered_prompt="scaffold", postcondition=None, retry=0,
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=_FakeCC(),
            verbose=True,
        )
        runner.run()
        out = capsys.readouterr().out

        assert re.search(r"\[\d{2}:\d{2}:\d{2}\]\s+\[auth\]\s+scaffold\s+PASS", out), (
            f"PASS line missing [HH:MM:SS] timestamp:\n{out}"
        )


# --- Issue #5: Logger.log_cc_result -----------------------------------------

class TestLogCcResult:
    """Logger exposes log_cc_result and records returncode/stdout/stderr."""

    def test_method_exists(self):
        from cc_pipeline.logger import Logger
        assert hasattr(Logger, "log_cc_result")

    def test_writes_cc_result_event(self, tmp_path):
        from cc_pipeline.logger import Logger
        from cc_pipeline.executor import CCResult

        log = Logger(run_dir=str(tmp_path), module_name="auth")
        cc = CCResult(returncode=0, stdout="build ok", stderr="")
        log.log_cc_result(step="generate", cc_result=cc)

        entry = json.loads(
            (tmp_path / "auth" / "transcript.jsonl").read_text().strip().split("\n")[-1]
        )
        assert entry["event"] == "cc_result"
        assert entry["step"] == "generate"
        assert entry["returncode"] == 0
        assert entry["stdout"] == "build ok"
        assert "stderr" in entry

    def test_records_failure_result(self, tmp_path):
        from cc_pipeline.logger import Logger
        from cc_pipeline.executor import CCResult

        log = Logger(run_dir=str(tmp_path), module_name="auth")
        cc = CCResult(returncode=1, stdout="", stderr="fatal: API error")
        log.log_cc_result(step="generate", cc_result=cc)

        entry = json.loads(
            (tmp_path / "auth" / "transcript.jsonl").read_text().strip().split("\n")[-1]
        )
        assert entry["returncode"] == 1
        assert "API error" in entry["stderr"]


# --- Issue #5: runner logs cc_result ----------------------------------------

class TestRunnerLogsCcResult:
    """ModuleRunner._execute_step logs cc_result on success and CC failure."""

    def test_success_logs_cc_result(self, git_repo):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        step = CompiledStep(
            step_id="generate", executor="claude-code",
            rendered_prompt="generate", postcondition=None, retry=0,
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=_FakeCC(),
        )
        runner._execute_step(step)

        transcript = git_repo / "runs" / "auth" / "transcript.jsonl"
        events = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
        cc_results = [e for e in events if e.get("event") == "cc_result"]
        assert cc_results, "expected a cc_result event in the transcript"
        assert "all tests passed" in cc_results[-1].get("stdout", "")

    def test_failure_logs_cc_result(self, git_repo):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        class CrashCC:
            def __init__(self, **kw):
                pass

            def run(self, prompt, cwd, allowed_tools=None, **kw):
                return CCResult(returncode=1, stdout="", stderr="boom: failed")

        step = CompiledStep(
            step_id="generate", executor="claude-code",
            rendered_prompt="generate", postcondition=None, retry=0,
        )
        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=CrashCC(),
        )
        runner._execute_step(step)

        transcript = git_repo / "runs" / "auth" / "transcript.jsonl"
        events = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
        cc_results = [e for e in events if e.get("event") == "cc_result"]
        assert cc_results, "expected a cc_result event on CC failure"
        assert cc_results[-1].get("returncode") == 1
        assert "boom: failed" in cc_results[-1].get("stderr", "")


# --- Issue #5: transcript command shows cc_result ----------------------------

def _make_transcript_with_cc_result(run_dir, module_name):
    """Create a transcript.jsonl that includes a cc_result event."""
    mod_dir = Path(run_dir) / module_name
    mod_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"ts": "2026-07-04T12:00:01", "event": "step_start", "step": "generate", "attempt": 1},
        {"ts": "2026-07-04T12:00:01", "event": "cc_prompt", "step": "generate", "prompt": "Generate tests."},
        {"ts": "2026-07-04T12:00:30", "event": "cc_result", "step": "generate",
         "returncode": 0, "stdout": "all tests passed\n3 files created", "stderr": ""},
        {"ts": "2026-07-04T12:00:31", "event": "pass", "step": "generate", "attempt": 1,
         "info": {"reason": "ok"}},
    ]
    with open(mod_dir / "transcript.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


class TestTranscriptShowsCcResult:
    """transcript command renders cc_result stdout/stderr."""

    def test_shows_stdout(self, tmp_path, capsys):
        from cc_pipeline.cli import main

        _make_transcript_with_cc_result(str(tmp_path), "auth")
        ret = main(["transcript", "--run-dir", str(tmp_path), "--module", "auth"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "all tests passed" in out
        assert "3 files created" in out

    def test_shows_returncode(self, tmp_path, capsys):
        from cc_pipeline.cli import main

        _make_transcript_with_cc_result(str(tmp_path), "auth")
        ret = main(["transcript", "--run-dir", str(tmp_path), "--module", "auth"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "returncode=0" in out or "returncode" in out

    def test_shows_stderr_on_failure(self, tmp_path, capsys):
        from cc_pipeline.cli import main

        mod_dir = tmp_path / "auth"
        mod_dir.mkdir(parents=True, exist_ok=True)
        events = [
            {"ts": "2026-07-04T12:00:01", "event": "step_start", "step": "gen", "attempt": 1},
            {"ts": "2026-07-04T12:00:30", "event": "cc_result", "step": "gen",
             "returncode": 1, "stdout": "", "stderr": "fatal: rate limited"},
        ]
        with open(mod_dir / "transcript.jsonl", "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")

        ret = main(["transcript", "--run-dir", str(tmp_path), "--module", "auth"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "rate limited" in out
