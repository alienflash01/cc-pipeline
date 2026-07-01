"""TDD: Resume command — restart from checkpoint after crash.

Resume logic:
1. Read orchestrator-state.json from previous run
2. Identify which modules already passed → skip them
3. Identify failed/error modules → re-run from their last checkpoint
4. Modules with no state → run fresh
"""
import pytest
import json
import subprocess
import os
from pathlib import Path
from unittest.mock import patch, MagicMock


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
}


CONFIG_YAML = """
repo: {repo}
base_branch: main
concurrency: 1
max_retries: 2

pipeline:
  - id: check
    executor: shell
    prompt: "echo ok"
    postcondition:
      shell: "echo ok"

modules:
  - {{name: mod_a, spec_id: S, source_dir: src/, source_files: [a.c], coverage: {{line_threshold: 80, branch_threshold: 70}}}}
  - {{name: mod_b, spec_id: S, source_dir: src/, source_files: [b.c], coverage: {{line_threshold: 80, branch_threshold: 70}}}}
  - {{name: mod_c, spec_id: S, source_dir: src/, source_files: [c.c], coverage: {{line_threshold: 80, branch_threshold: 70}}}}
"""


@pytest.fixture
def real_repo(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True)
    src = repo / "src"
    src.mkdir()
    for i in range(3):
        (src / f"mod_{'abc'[i]}.c").write_text(f"int f() {{ return {i}; }}\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestResumeReadsState:
    """Resume reads orchestrator-state.json to determine what to skip."""

    def test_resume_skips_passed_modules(self, real_repo, tmp_path):
        """Modules with status=passed in state.json are skipped."""
        from cc_pipeline.cli import main

        # Simulate a previous run where mod_a passed
        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        state = {
            "run_id": "2026-07-01T10-00-00",
            "modules": {
                "mod_a": {"status": "passed", "steps_completed": 1, "steps_total": 1},
                "mod_b": {"status": "failed", "steps_completed": 0, "steps_total": 1},
                "mod_c": {"status": "running", "steps_completed": 0, "steps_total": 1},
            },
        }
        (run_dir / "orchestrator-state.json").write_text(json.dumps(state))

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CONFIG_YAML.format(repo=real_repo))

        # Resume should skip mod_a, only run mod_b and mod_c
        ret = main(["resume", str(config_path), "--run-dir", str(run_dir)])
        assert ret == 0

        # Verify state: mod_a still passed, mod_b and mod_c should have new status
        new_state = json.loads((run_dir / "orchestrator-state.json").read_text())
        assert new_state["modules"]["mod_a"]["status"] == "passed"
        assert new_state["modules"]["mod_b"]["status"] == "passed"
        assert new_state["modules"]["mod_c"]["status"] == "passed"

    def test_resume_with_no_state_runs_all(self, real_repo, tmp_path):
        """Resume with no state.json → runs all modules fresh."""
        from cc_pipeline.cli import main

        run_dir = tmp_path / "runs"
        run_dir.mkdir()

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CONFIG_YAML.format(repo=real_repo))

        ret = main(["resume", str(config_path), "--run-dir", str(run_dir)])
        assert ret == 0

        state = json.loads((run_dir / "orchestrator-state.json").read_text())
        assert len(state["modules"]) == 3
        for m in state["modules"].values():
            assert m["status"] == "passed"

    def test_resume_with_all_passed_exits_early(self, real_repo, tmp_path):
        """All modules already passed → exit 0, no work."""
        from cc_pipeline.cli import main

        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        state = {
            "run_id": "2026-07-01T10-00-00",
            "modules": {
                "mod_a": {"status": "passed"},
                "mod_b": {"status": "passed"},
                "mod_c": {"status": "passed"},
            },
        }
        (run_dir / "orchestrator-state.json").write_text(json.dumps(state))

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CONFIG_YAML.format(repo=real_repo))

        ret = main(["resume", str(config_path), "--run-dir", str(run_dir)])
        assert ret == 0


class TestResumeWorktreeReuse:
    """Resume reuses existing worktrees for failed modules."""

    def test_resume_cleans_old_worktree_before_rerun(self, real_repo, tmp_path):
        """Resume cleans up old worktree for failed module before re-running."""
        from cc_pipeline.cli import main

        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        state = {
            "run_id": "2026-07-01T10-00-00",
            "modules": {
                "mod_a": {"status": "passed"},
                "mod_b": {"status": "failed", "worktree": str(run_dir / "worktrees" / "mod_b")},
            },
        }
        (run_dir / "orchestrator-state.json").write_text(json.dumps(state))

        config_path = tmp_path / "config.yaml"
        config_path.write_text(CONFIG_YAML.format(repo=real_repo))

        ret = main(["resume", str(config_path), "--run-dir", str(run_dir)])
        assert ret == 0
