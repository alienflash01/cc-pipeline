"""TDD: tests for logging the full CC prompt to the transcript.

The runner previously only logged step_start/pass/fail/retry. We now also
record the exact prompt handed to Claude Code, so failed runs can be reproduced
and audited. See feature 2 of the implementation brief.
"""
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


def _last_entry(transcript: Path) -> dict:
    return json.loads(transcript.read_text().strip().split("\n")[-1])


class TestLoggerLogPrompt:
    """Logger gains a log_prompt method that records the CC prompt."""

    def test_log_prompt_writes_cc_prompt_event(self, tmp_path):
        from cc_pipeline.logger import Logger

        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.log_prompt(step="generate", prompt="write tests for auth")

        entry = _last_entry(tmp_path / "auth" / "transcript.jsonl")
        assert entry["event"] == "cc_prompt"
        assert entry["step"] == "generate"
        assert entry["prompt"] == "write tests for auth"

    def test_log_prompt_has_timestamp_and_module(self, tmp_path):
        from cc_pipeline.logger import Logger

        log = Logger(run_dir=str(tmp_path), module_name="payment")
        log.log_prompt(step="scaffold", prompt="hi")

        entry = _last_entry(tmp_path / "payment" / "transcript.jsonl")
        assert "ts" in entry
        assert entry["module"] == "payment"

    def test_log_prompt_truncates_to_2000_chars(self, tmp_path):
        from cc_pipeline.logger import Logger

        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.log_prompt(step="generate", prompt="x" * 5000)

        entry = _last_entry(tmp_path / "auth" / "transcript.jsonl")
        assert len(entry["prompt"]) == 2000

    def test_log_prompt_short_prompt_not_truncated(self, tmp_path):
        from cc_pipeline.logger import Logger

        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.log_prompt(step="generate", prompt="short")

        entry = _last_entry(tmp_path / "auth" / "transcript.jsonl")
        assert entry["prompt"] == "short"


class TestRunnerLogsPrompt:
    """Runner._execute_step logs the CC prompt before executing CC."""

    def test_execute_step_logs_cc_prompt_event(self, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep

        repo = _git_repo(tmp_path)
        step = CompiledStep(
            step_id="scaffold",
            executor="claude-code",
            rendered_prompt="build the scaffold for auth module",
        )
        runner = ModuleRunner(
            steps=[step],
            module_name="auth",
            worktree_path=str(repo),
            run_dir=str(tmp_path / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        transcript = tmp_path / "runs" / "auth" / "transcript.jsonl"
        entries = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
        prompt_events = [e for e in entries if e.get("event") == "cc_prompt"]

        assert len(prompt_events) == 1
        assert prompt_events[0]["step"] == "scaffold"
        # the rendered prompt content is preserved (context injection may append)
        assert "scaffold for auth module" in prompt_events[0]["prompt"]

    def test_execute_step_logs_prompt_before_cc_runs(self, tmp_path):
        """The cc_prompt event is written even if CC itself raises."""
        from cc_pipeline.runner import ModuleRunner, ExecOutcome
        from cc_pipeline.compiler import CompiledStep

        repo = _git_repo(tmp_path)
        step = CompiledStep(
            step_id="scaffold",
            executor="claude-code",
            rendered_prompt="do work",
        )
        runner = ModuleRunner(
            steps=[step],
            module_name="auth",
            worktree_path=str(repo),
            run_dir=str(tmp_path / "runs"),
        )

        with patch("cc_pipeline.executor.subprocess.run", side_effect=RuntimeError("boom")):
            result = runner._execute_step(step)

        # CC blew up → classified as unknown error, but prompt still logged
        assert result.outcome == ExecOutcome.UNKNOWN_ERROR
        transcript = tmp_path / "runs" / "auth" / "transcript.jsonl"
        entries = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
        assert any(e.get("event") == "cc_prompt" and "do work" in e["prompt"] for e in entries)
