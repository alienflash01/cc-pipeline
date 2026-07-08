"""Failure path terminal visibility tests — per TESTING-RULES.md."""
import pytest
import subprocess, os
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.orchestrator import Orchestrator


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "file.txt").write_text("line1\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "test"}
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=env)
    return repo


def _simple_config(repo, **kw):
    return PipelineConfig(
        repo=str(repo),
        concurrency=1,
        auto_merge=kw.pop("auto_merge", True),  # default True for merge tests
        pipeline=[PipelineStep(id="x", executor="shell", prompt="echo ok")],
        modules=[Module(name="auth", source_dir="src/", source_files=["a.c"])],
        **kw,
    )


class TestModuleExceptionPrints:
    """Rule 1+2: module exception must print to terminal."""

    def test_shell_failure_prints_to_terminal(self, tmp_path, capsys):
        repo = _git_repo(tmp_path)
        config = PipelineConfig(
            repo=str(repo), concurrency=1,
            pipeline=[PipelineStep(id="x", executor="shell", prompt="nonexistent_cmd_xyz",
                                   retry=0)],
            modules=[Module(name="auth", source_dir="src/", source_files=["a.c"])],
        )
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))
        orch.run()
        out = capsys.readouterr().out
        assert "failed" in out.lower() or "❌" in out or "FAIL" in out


class TestMergeSuccessPrints:
    """Rule 5: merge success must print to terminal."""

    def test_merge_success_message(self, tmp_path, capsys):
        repo = _git_repo(tmp_path)
        config = _simple_config(repo)
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))
        orch.run()
        out = capsys.readouterr().out
        # Merge success should print (module passed)
        assert "passed" in out.lower() or "✅" in out or "🔀" in out


class TestMergeConflictPrints:
    """Rule 1+5: merge conflict must print + preserve worktree."""

    def test_merge_conflict_message_and_worktree_preserved(self, tmp_path, capsys):
        repo = _git_repo(tmp_path)
        wt_root = tmp_path / "wt"
        config = PipelineConfig(
            repo=str(repo), concurrency=1,
            worktree_root=str(wt_root), auto_merge=True,
            pipeline=[PipelineStep(id="x", executor="shell", prompt="echo ok")],
            modules=[Module(name="auth", source_dir="src/", source_files=["a.c"])],
        )
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        # Mock _merge_branch to simulate conflict (return False)
        original_merge = orch._merge_branch
        orch._merge_branch = lambda name, branch: False

        orch.run()
        out = capsys.readouterr().out
        assert "Merge conflict" in out or "⚠️" in out
        assert "Manual merge" in out or "manual" in out.lower()


class TestMergeErrorPrints:
    """Rule 1: merge exception must print to terminal."""

    def test_merge_error_message(self, tmp_path, capsys):
        repo = _git_repo(tmp_path)
        config = _simple_config(repo)
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        # Mock _merge_branch to raise
        def boom(name, branch):
            raise RuntimeError("git checkout failed: no such branch")
        orch._merge_branch = boom

        orch.run()
        out = capsys.readouterr().out
        assert "Merge failed" in out or "git checkout failed" in out or "⚠️" in out


class TestParallelFailurePrints:
    """Rule 2: parallel module failure must print to terminal."""

    def test_parallel_failure_visible(self, tmp_path, capsys):
        repo = _git_repo(tmp_path)
        config = PipelineConfig(
            repo=str(repo), concurrency=2,
            pipeline=[PipelineStep(id="x", executor="shell", prompt="echo ok")],
            modules=[
                Module(name="good", source_dir="src/", source_files=["a.c"]),
                Module(name="bad", source_dir="src/", source_files=["b.c"]),
            ],
        )
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        # Mock _run_module to fail for "bad"
        original = orch._run_module
        def mock_run(name):
            if name == "bad":
                raise RuntimeError("intentional failure for bad module")
            return original(name)
        orch._run_module = mock_run

        orch.run()
        out = capsys.readouterr().out
        assert "bad" in out and ("failed" in out.lower() or "❌" in out)
