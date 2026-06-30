"""TDD: Fix StateManager concurrent save overwrite problem.

When multiple modules run in parallel, each module's _run_module
must NOT overwrite other modules' state.
"""
import pytest
import json
import os
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    for i in range(5):
        (src / f"mod_{i}.c").write_text(f"int f_{i}() {{ return {i}; }}\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestConcurrentStateIntegrity:
    """Multiple modules updating state concurrently must not lose data."""

    def test_3_modules_parallel_all_in_final_state(self, real_repo, tmp_path):
        """After 3 modules complete, all 3 should be in orchestrator-state.json."""
        from cc_pipeline.cli import main

        config = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 3\n"
            "max_retries: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: echo ok\n'
            "    postcondition:\n"
            '      shell: echo ok\n'
            "\n"
            "modules:\n"
            "  - {name: mod_0, spec_id: S, source_dir: src/, source_files: [mod_0.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
            "  - {name: mod_1, spec_id: S, source_dir: src/, source_files: [mod_1.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
            "  - {name: mod_2, spec_id: S, source_dir: src/, source_files: [mod_2.c], coverage: {line_threshold: 80, branch_threshold: 70}}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 0

        state_file = tmp_path / "runs" / "orchestrator-state.json"
        state = json.loads(state_file.read_text())
        # ALL 3 modules must be present — not just the last one that saved
        assert len(state["modules"]) == 3
        for mod_name in ["mod_0", "mod_1", "mod_2"]:
            assert mod_name in state["modules"]
            assert state["modules"][mod_name]["status"] == "passed"
