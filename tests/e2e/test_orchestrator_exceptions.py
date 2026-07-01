"""TDD: Orchestrator exception handling — no silent swallowing."""
import pytest
import subprocess
import os
from pathlib import Path
from unittest.mock import patch, MagicMock


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


@pytest.fixture
def real_repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True)
    src = repo / "src"
    src.mkdir()
    (src / "a.c").write_text("int f() { return 0; }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestOrchestratorExceptionLogging:
    """Exceptions in _run_module must be logged, not silently swallowed."""

    def test_exception_logged_to_transcript(self, real_repo, tmp_path):
        """When _run_module throws, the exception is in transcript.jsonl."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config

        config_yaml = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: echo ok\n'
            "    postcondition:\n"
            '      shell: echo ok\n'
            "\n"
            "modules:\n"
            "  - {name: mod_a, spec_id: S, source_dir: src/, source_files: [a.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
        )

        # Make compiler throw
        with patch.object(orch, "compiler") as mock_compiler:
            mock_compiler.compile_module.side_effect = RuntimeError("compiler exploded")
            results = orch.run()

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert "compiler exploded" in results[0]["error"]

        # Exception logged to transcript
        transcript = (tmp_path / "runs" / "mod_a" / "transcript.jsonl")
        assert transcript.exists()
        content = transcript.read_text()
        assert "module_exception" in content
        assert "compiler exploded" in content
        assert "traceback" in content.lower()

    def test_exception_traceback_in_transcript(self, real_repo, tmp_path):
        """Full traceback is in transcript for debugging."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config

        config_yaml = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: echo ok\n'
            "    postcondition:\n"
            '      shell: echo ok\n'
            "\n"
            "modules:\n"
            "  - {name: mod_a, spec_id: S, source_dir: src/, source_files: [a.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
        )

        with patch.object(orch, "compiler") as mock_compiler:
            mock_compiler.compile_module.side_effect = FileNotFoundError("git not found")
            results = orch.run()

        transcript = (tmp_path / "runs" / "mod_a" / "transcript.jsonl").read_text()
        assert "FileNotFoundError" in transcript
        assert "git not found" in transcript


class TestWorktreeCleanupOnException:
    """Worktree cleanup is guaranteed even on exceptions."""

    def test_worktree_preserved_on_mid_pipeline_exception(self, real_repo, tmp_path):
        """If pipeline throws mid-run, worktree is preserved (not cleaned)."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config

        config_yaml = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: echo ok\n'
            "    postcondition:\n"
            '      shell: echo ok\n'
            "\n"
            "modules:\n"
            "  - {name: mod_a, spec_id: S, source_dir: src/, source_files: [a.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
        )

        # Create worktree succeeds, but ModuleRunner.run() throws
        with patch("cc_pipeline.orchestrator.ModuleRunner") as mock_runner_cls:
            mock_runner_cls.return_value.run.side_effect = RuntimeError("OOM")
            results = orch.run()

        assert results[0]["status"] == "failed"
        assert "OOM" in results[0]["error"]

        # Worktree should be preserved (not cleaned)
        assert orch.worktree_mgr.get_path("mod_a") is not None

    def test_state_marked_error_on_exception(self, real_repo, tmp_path):
        """State file records 'error' status on exception."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config
        import json

        config_yaml = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: echo ok\n'
            "    postcondition:\n"
            '      shell: echo ok\n'
            "\n"
            "modules:\n"
            "  - {name: mod_a, spec_id: S, source_dir: src/, source_files: [a.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
        )

        with patch("cc_pipeline.orchestrator.ModuleRunner") as mock_runner_cls:
            mock_runner_cls.return_value.run.side_effect = RuntimeError("disk full")
            results = orch.run()

        state = json.loads((tmp_path / "runs" / "orchestrator-state.json").read_text())
        assert state["modules"]["mod_a"]["status"] == "error"
        assert "disk full" in state["modules"]["mod_a"]["error"]


class TestWorktreeCreationFailure:
    """Worktree creation itself fails — no garbage left behind."""

    def test_worktree_creation_failure_logged(self, real_repo, tmp_path):
        """If worktree creation fails, exception is logged, no preserve."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config

        config_yaml = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: echo ok\n'
            "    postcondition:\n"
            '      shell: echo ok\n'
            "\n"
            "modules:\n"
            "  - {name: mod_a, spec_id: S, source_dir: src/, source_files: [a.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
        )

        with patch.object(orch.worktree_mgr, "create") as mock_create:
            mock_create.side_effect = subprocess.CalledProcessError(128, "git worktree add")
            results = orch.run()

        assert results[0]["status"] == "failed"
        assert "worktree" in results[0]["error"].lower() or "CalledProcessError" in results[0]["error"]

        # No worktree path recorded (creation failed)
        assert orch.worktree_mgr.get_path("mod_a") is None

        # Still logged to transcript
        transcript = (tmp_path / "runs" / "mod_a" / "transcript.jsonl").read_text()
        assert "module_exception" in transcript
