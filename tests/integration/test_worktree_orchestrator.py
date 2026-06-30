"""TDD: Worktree Manager + Orchestrator tests."""
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
def bare_repo(tmp_path):
    """Real git repo for worktree tests."""
    repo = tmp_path / "main-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.c").write_text("int f() { return 0; }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True)
    return repo


class TestWorktreeManager:
    """Test git worktree lifecycle management."""

    def test_importable(self):
        from cc_pipeline.worktree import WorktreeManager
        assert WorktreeManager is not None

    def test_create_worktree(self, bare_repo, tmp_path):
        """Creates a worktree with a new branch."""
        from cc_pipeline.worktree import WorktreeManager
        wt_mgr = WorktreeManager(
            repo_path=str(bare_repo), base_branch="main",
            worktree_root=str(tmp_path / "wt"),
        )
        wt_path = wt_mgr.create(module_name="auth")
        assert Path(wt_path).exists()
        assert (Path(wt_path) / "src" / "a.c").exists()  # files present

    def test_worktree_has_branch(self, bare_repo, tmp_path):
        """Worktree is on its own branch."""
        from cc_pipeline.worktree import WorktreeManager
        wt_mgr = WorktreeManager(
            repo_path=str(bare_repo), base_branch="main",
            worktree_root=str(tmp_path / "wt"),
        )
        wt_path = wt_mgr.create(module_name="auth")
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt_path, capture_output=True, text=True,
        )
        assert "auth" in result.stdout

    def test_worktree_isolated(self, bare_repo, tmp_path):
        """Changes in worktree don't affect main repo."""
        from cc_pipeline.worktree import WorktreeManager
        wt_mgr = WorktreeManager(
            repo_path=str(bare_repo), base_branch="main",
            worktree_root=str(tmp_path / "wt"),
        )
        wt_path = wt_mgr.create(module_name="auth")
        # Write a file in worktree
        (Path(wt_path) / "new.txt").write_text("test")
        # Main repo should NOT have it
        assert not (bare_repo / "new.txt").exists()

    def test_cleanup_removes_worktree(self, bare_repo, tmp_path):
        """Cleanup removes the worktree."""
        from cc_pipeline.worktree import WorktreeManager
        wt_mgr = WorktreeManager(
            repo_path=str(bare_repo), base_branch="main",
            worktree_root=str(tmp_path / "worktrees"),
        )
        wt_path = wt_mgr.create(module_name="auth")
        assert Path(wt_path).exists()
        wt_mgr.cleanup("auth")
        assert not Path(wt_path).exists()

    def test_preserve_on_failure(self, bare_repo, tmp_path):
        """preserve() keeps the worktree for analysis."""
        from cc_pipeline.worktree import WorktreeManager
        wt_mgr = WorktreeManager(
            repo_path=str(bare_repo), base_branch="main",
            worktree_root=str(tmp_path / "worktrees"),
        )
        wt_path = wt_mgr.create(module_name="auth")
        wt_mgr.preserve("auth")
        assert Path(wt_path).exists()


class TestOrchestrator:
    """Test the top-level parallel orchestrator."""

    MULTI_YAML = """
repo: PLACEHOLDER
base_branch: main
concurrency: 2
max_retries: 1

pipeline:
  - id: step1
    executor: shell
    prompt: "echo hello"
    postcondition:
      shell: "echo ok"

modules:
  - name: mod_a
    spec_id: S1
    source_dir: src/
    source_files: [a.c]
    coverage: {line_threshold: 80, branch_threshold: 70}

  - name: mod_b
    spec_id: S2
    source_dir: src/
    source_files: [b.c]
    coverage: {line_threshold: 80, branch_threshold: 70}
"""

    def test_importable(self):
        from cc_pipeline.orchestrator import Orchestrator
        assert Orchestrator is not None

    def test_run_all_modules(self, bare_repo, tmp_path):
        """Orchestrator runs all modules."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config

        yaml = self.MULTI_YAML.replace("PLACEHOLDER", str(bare_repo))
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
            worktree_root=str(tmp_path / "worktrees"),
        )
        results = orch.run()
        assert len(results) == 2
        assert all(r["status"] == "passed" for r in results)

    def test_one_failure_others_continue(self, bare_repo, tmp_path):
        """One module failing doesn't block others."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config

        yaml = self.MULTI_YAML.replace("PLACEHOLDER", str(bare_repo))
        # Make mod_a always fail
        yaml = yaml.replace(
            'shell: "echo ok"',
            'shell: "false"',
            1,  # only first occurrence (step for mod_a)
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
            worktree_root=str(tmp_path / "worktrees"),
        )
        results = orch.run()
        # Both modules attempted
        assert len(results) == 2
        statuses = {r["status"] for r in results}
        assert "failed" in statuses  # at least one failed

    def test_concurrency_respected(self, bare_repo, tmp_path):
        """Orchestrator uses ThreadPoolExecutor with correct concurrency."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config

        yaml = self.MULTI_YAML.replace("PLACEHOLDER", str(bare_repo))
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml)
        config = load_config(str(config_path))

        orch = Orchestrator(
            config=config,
            run_dir=str(tmp_path / "runs"),
            worktree_root=str(tmp_path / "worktrees"),
        )
        assert orch.concurrency == 2
