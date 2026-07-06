"""TDD: postcondition failure diagnostics (P0-1).

When a step's postcondition fails in verbose mode, the runner prints extra
context so the user can see WHAT was checked and WHAT CC actually produced:
  1. the postcondition shell command itself
  2. files CC created/modified (via `git status --porcelain`)
  3. a hint when the file suffix mismatches (CC made a .py but we checked .c)

Covers SPEC.md P0-1. See also [[test_preflight_verbose]] for verbose events.
"""
import os
import subprocess
from unittest.mock import patch

import pytest


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
}


@pytest.fixture
def git_repo(tmp_path):
    """Minimal git repo with an initial commit, for git-status diagnostics."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


def _step(**kw):
    """Build a CompiledStep with sensible defaults."""
    from cc_pipeline.compiler import CompiledStep
    defaults = dict(
        step_id="verify", executor="claude-code",
        rendered_prompt="x", postcondition=None, retry=0,
    )
    defaults.update(kw)
    return CompiledStep(**defaults)


class _FakeSuccessCC:
    """CC executor that always succeeds — so we reach the postcondition."""
    def run(self, prompt, cwd, allowed_tools=None, **kw):
        from cc_pipeline.executor import CCResult
        return CCResult(returncode=0, stdout="done", stderr="")


# --- 1 & 2: postcondition.py ------------------------------------------------

class TestPostconditionResultField:
    """PostconditionResult carries the shell_command that produced it."""

    def test_result_has_shell_command_field(self):
        from cc_pipeline.postcondition import PostconditionResult
        r = PostconditionResult(passed=True, shell_command="test -f foo.c")
        assert r.shell_command == "test -f foo.c"
        # default is empty string
        assert PostconditionResult(passed=True).shell_command == ""


class TestEvaluateStoresShellCommand:
    """evaluate() records the shell it ran, on every outcome."""

    def test_evaluate_pass_records_shell(self, tmp_path):
        from cc_pipeline.postcondition import evaluate
        r = evaluate(shell="echo ok", expect=None, cwd=str(tmp_path))
        assert r.passed is True
        assert r.shell_command == "echo ok"

    def test_evaluate_fail_records_shell(self, tmp_path):
        from cc_pipeline.postcondition import evaluate
        r = evaluate(shell="false", expect=None, cwd=str(tmp_path))
        assert r.passed is False
        assert r.shell_command == "false"

    def test_evaluate_expect_fail_records_shell(self, tmp_path):
        from cc_pipeline.postcondition import evaluate
        r = evaluate(shell='echo \'{"line": 50}\'', expect="$.line >= 80",
                     cwd=str(tmp_path))
        assert r.passed is False
        assert r.shell_command == 'echo \'{"line": 50}\''


# --- 3, 4, 5: runner diagnostics -------------------------------------------

class TestRunnerPostconditionDiag:
    """verbose mode prints postcondition shell + git-status files + suffix hint."""

    def test_verbose_fail_prints_postcondition_shell(self, git_repo, capsys):
        """On final postcondition FAIL, verbose prints the shell command checked."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.postcondition import PostconditionResult

        runner = ModuleRunner(
            steps=[_step(retry=0)], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=_FakeSuccessCC(),
            verbose=1,
        )
        runner._check_postcondition = lambda step: PostconditionResult(
            passed=False, reason="Shell command exited with code 1",
            shell_command="test -f tests/test_auth.c",
        )
        runner.run()
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "postcondition" in out
        assert "test -f tests/test_auth.c" in out

    def test_quiet_fail_prints_no_diag(self, git_repo, capsys):
        """verbose=0 stays quiet — no postcondition shell / git-status dump."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.postcondition import PostconditionResult

        runner = ModuleRunner(
            steps=[_step(retry=0)], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=_FakeSuccessCC(),
            verbose=0,
        )
        runner._check_postcondition = lambda step: PostconditionResult(
            passed=False, reason="Shell command exited with code 1",
            shell_command="test -f tests/test_auth.c",
        )
        runner.run()
        out = capsys.readouterr().out
        assert "postcondition" not in out
        assert "CC changed files" not in out

    def test_verbose_fail_prints_git_status_files(self, git_repo, capsys):
        """verbose FAIL lists files CC created/modified via git status."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.postcondition import PostconditionResult

        # CC "generated" an untracked file
        (git_repo / "tests").mkdir(exist_ok=True)
        (git_repo / "tests" / "test_auth.py").write_text("# generated")

        runner = ModuleRunner(
            steps=[_step(retry=0)], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(git_repo / "runs"),
            cc_executor=_FakeSuccessCC(),
            verbose=1,
        )
        runner._check_postcondition = lambda step: PostconditionResult(
            passed=False, reason="fail",
            shell_command="test -f tests/test_auth.py",
        )
        runner.run()
        out = capsys.readouterr().out
        assert "CC changed files" in out
        assert "test_auth.py" in out  # the untracked file shows up

    def test_verbose_fail_suffix_c_vs_py_hint(self, git_repo, tmp_path, capsys):
        """Checking a .c file but CC generated a .py → hint printed."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.postcondition import PostconditionResult

        (git_repo / "tests").mkdir(exist_ok=True)
        (git_repo / "tests" / "test_auth.py").write_text("# generated")

        runner = ModuleRunner(
            steps=[_step(retry=0)], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(tmp_path / "runs"),
            cc_executor=_FakeSuccessCC(),
            verbose=1,
        )
        runner._check_postcondition = lambda step: PostconditionResult(
            passed=False, reason="Shell command exited with code 1",
            shell_command="test -f tests/test_auth.c",
        )
        runner.run()
        out = capsys.readouterr().out
        assert "Hint" in out
        assert ".c" in out
        assert ".py" in out

    def test_detect_file_changes_method_exists(self, git_repo, tmp_path):
        """Runner exposes a _detect_file_changes helper backed by git status."""
        from cc_pipeline.runner import ModuleRunner

        # Use a separate run_dir outside the worktree so it doesn't pollute git status
        runner = ModuleRunner(
            steps=[_step()], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(tmp_path / "runs"),
        )
        # Nothing untracked → empty list
        assert runner._detect_file_changes() == []

        # Add an untracked file → detected
        (git_repo / "new.txt").write_text("hi")
        changes = runner._detect_file_changes()
        assert any("new.txt" in c for c in changes)
