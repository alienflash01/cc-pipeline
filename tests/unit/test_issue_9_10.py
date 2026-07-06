"""TDD: Issue #9 (Ctrl+C leaves CC subprocesses running) + Issue #10 (resume --verbose).

Issue #9: the signal handler must not only set _shutdown_requested — it must
also kill the Claude Code child processes. CC is launched with
start_new_session=True, so it escapes cc-pipeline's process group and survives
Ctrl+C unless we explicitly kill it.

Issue #10: `cc-pipeline resume` did not accept --verbose, leaving a resume run
a total black box. resume_parser must gain --verbose (and --dry-run for
consistency), and _cmd_resume must forward verbose to the Orchestrator.
"""
import json
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
}


def _head_branch(repo) -> str:
    """Return the current branch name of a repo (master/main agnostic)."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return r.stdout.strip() or "master"


@pytest.fixture
def git_repo(tmp_path):
    """Minimal git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


# ============================================================================
# Issue #9 — Ctrl+C must kill CC subprocesses
# ============================================================================

class TestSignalHandlerShutdown:
    """The signal handler sets _shutdown_requested AND kills CC children."""

    def test_handler_sets_shutdown_flag(self):
        """SIGTERM sets _shutdown_requested = True (no exception)."""
        from cc_pipeline import cli
        cli._shutdown_requested = False
        with patch("cc_pipeline.cli._kill_cc_subprocesses"):
            cli._signal_handler(signal.SIGTERM, None)
        assert cli._shutdown_requested is True
        cli._shutdown_requested = False  # reset global for other tests

    def test_handler_invokes_cc_kill(self):
        """The signal handler calls _kill_cc_subprocesses()."""
        from cc_pipeline import cli
        cli._shutdown_requested = False
        with patch("cc_pipeline.cli._kill_cc_subprocesses") as mock_kill:
            cli._signal_handler(signal.SIGTERM, None)
        mock_kill.assert_called_once()
        assert cli._shutdown_requested is True
        cli._shutdown_requested = False


class TestKillCCSubprocesses:
    """_kill_cc_subprocesses() shells out to pkill targeting the CC headless cmd."""

    def test_helper_exists(self):
        """A _kill_cc_subprocesses helper exists on the cli module."""
        from cc_pipeline import cli
        assert hasattr(cli, "_kill_cc_subprocesses")
        assert callable(cli._kill_cc_subprocesses)

    def test_calls_pkill_on_claude_headless(self):
        """It invokes pkill -f with a pattern matching `claude ... -p`."""
        from cc_pipeline import cli
        with patch("cc_pipeline.cli.subprocess.run") as mock_run:
            cli._kill_cc_subprocesses()
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == "pkill"
        # pattern must reference both 'claude' and the headless '-p' flag
        pattern = " ".join(cmd[1:])
        assert "claude" in pattern
        assert "-p" in pattern

    def test_best_effort_does_not_raise(self):
        """pkill failing (FileNotFoundError / non-zero) must not raise."""
        from cc_pipeline import cli
        with patch("cc_pipeline.cli.subprocess.run", side_effect=FileNotFoundError("no pkill")):
            cli._kill_cc_subprocesses()  # should swallow the error


# ============================================================================
# Issue #10 — resume accepts --verbose
# ============================================================================

class TestResumeVerboseFlag:
    """resume_parser exposes --verbose, and _cmd_resume forwards it."""

    def test_resume_parser_has_verbose(self):
        """`resume --verbose` parses args.verbose == True."""
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(
            ["resume", "config.yaml", "--run-dir", "x", "--verbose"]
        )
        assert args.verbose >= 1

    def test_resume_parser_verbose_defaults_false(self):
        """Without --verbose, args.verbose == False."""
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["resume", "config.yaml", "--run-dir", "x"])
        assert args.verbose == 0

    def test_resume_parser_has_dry_run(self):
        """resume_parser also gains --dry-run for consistency with run."""
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(
            ["resume", "config.yaml", "--run-dir", "x", "--dry-run"]
        )
        assert args.dry_run is True

    def test_resume_verbose_forwarded_to_orchestrator(self, git_repo, tmp_path):
        """`cc-pipeline resume ... --verbose` constructs Orchestrator(verbose=True)."""
        from cc_pipeline.cli import main

        cfg = git_repo.parent / "config.yaml"
        cfg.write_text(f"""
repo: {git_repo}
base_branch: {_head_branch(git_repo)}
pipeline:
  - id: gen
    executor: shell
    command: "echo ok"
modules:
  - name: auth
    source_dir: src/
    source_files: [a.c]
""")

        runs = git_repo.parent / "runs"
        runs.mkdir()

        # State file: auth did NOT pass → resume will re-run it (Orchestrator built).
        (runs / "orchestrator-state.json").write_text(json.dumps({
            "run_id": "prev-run",
            "modules": {"auth": {"status": "failed"}},
        }))

        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"), \
             patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = []
            ret = main([
                "resume", str(cfg),
                "--run-dir", str(runs),
                "--verbose",
            ])

        assert ret == 0
        MockOrch.assert_called_once()
        assert MockOrch.call_args.kwargs.get("verbose") >= 1

    def test_resume_without_verbose_is_quiet(self, git_repo, tmp_path):
        """`cc-pipeline resume ...` (no flag) constructs Orchestrator(verbose=0)."""
        from cc_pipeline.cli import main

        cfg = git_repo.parent / "config.yaml"
        cfg.write_text(f"""
repo: {git_repo}
base_branch: {_head_branch(git_repo)}
pipeline:
  - id: gen
    executor: shell
    command: "echo ok"
modules:
  - name: auth
    source_dir: src/
    source_files: [a.c]
""")

        runs = git_repo.parent / "runs"
        runs.mkdir()
        (runs / "orchestrator-state.json").write_text(json.dumps({
            "run_id": "prev-run",
            "modules": {"auth": {"status": "failed"}},
        }))

        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"), \
             patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            MockOrch.return_value.run.return_value = []
            main(["resume", str(cfg), "--run-dir", str(runs)])

        MockOrch.assert_called_once()
        assert MockOrch.call_args.kwargs.get("verbose") == 0
