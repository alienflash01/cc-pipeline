"""TDD: worktree_root config field — user can specify where worktrees go."""
import pytest
import subprocess, os
from pathlib import Path
from cc_pipeline.config import load_config, PipelineConfig


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
}


class TestWorktreeRootConfig:
    """worktree_root can be set in YAML config."""

    def test_worktree_root_in_yaml(self, tmp_path):
        """worktree_root field in YAML is parsed into PipelineConfig."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        wt_root = tmp_path / "worktrees"

        config_file = tmp_path / "c.yaml"
        config_file.write_text(f"""
repo: {repo}
base_branch: main
worktree_root: {wt_root}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    variables:
      line_threshold: 80
""")
        config = load_config(str(config_file))
        assert config.worktree_root == str(wt_root)

    def test_worktree_root_defaults_none(self, tmp_path):
        """Without worktree_root, config.worktree_root is empty (framework decides)."""
        config_file = tmp_path / "c.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
""")
        config = load_config(str(config_file))
        assert config.worktree_root == ""

    def test_worktree_root_relative_to_repo(self, tmp_path):
        """worktree_root: ../wt means sibling of repo, resolved against repo path."""
        repo = tmp_path / "myproject"
        repo.mkdir()
        config_file = tmp_path / "c.yaml"
        config_file.write_text(f"""
repo: {repo}
worktree_root: ../wt
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
""")
        config = load_config(str(config_file))
        # worktree_root should be resolved relative to repo
        assert config.worktree_root == str(tmp_path / "wt")

    def test_orchestrator_uses_config_worktree_root(self, tmp_path):
        """Orchestrator creates WorktreeManager with config.worktree_root."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)

        wt_root = tmp_path / "wt"
        config = PipelineConfig(
            repo=str(repo),
            worktree_root=str(wt_root),
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok")],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"])],
        )
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))
        assert str(orch.worktree_mgr.worktree_root) == str(wt_root)
