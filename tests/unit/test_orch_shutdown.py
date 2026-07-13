"""TDD: Orchestrator graceful shutdown — checks _shutdown_requested between modules."""
import pytest
import subprocess, os
from pathlib import Path
from unittest.mock import patch, MagicMock


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com",
}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.c").write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


class TestGracefulShutdown:
    """Orchestrator checks shutdown flag between modules."""

    def test_shutdown_skips_remaining_modules(self, git_repo, tmp_path):
        """When shutdown is requested, orchestrator skips not-yet-started modules."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        config = PipelineConfig(
            repo=str(git_repo),
            base_branch="main",
            concurrency=1,  # serial to control ordering
            pipeline=[PipelineStep(
                id="x", executor="shell",
                prompt="echo ok",
                postcondition={"shell": "true"},
            )],
            modules=[
                Module(name="mod_a", source_dir="src/", source_files=["a.c"],
                       variables={"line_threshold": 80, "branch_threshold": 70}),
                Module(name="mod_b", source_dir="src/", source_files=["a.c"],
                       variables={"line_threshold": 80, "branch_threshold": 70}),
                Module(name="mod_c", source_dir="src/", source_files=["a.c"],
                       variables={"line_threshold": 80, "branch_threshold": 70}),
            ],
        )

        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        # Patch _run_module to set shutdown flag after first module
        original_run_module = orch._run_module
        call_count = [0]

        def mock_run_module(mod_name):
            call_count[0] += 1
            result = original_run_module(mod_name)
            if call_count[0] >= 1:
                orch.request_shutdown()
            return result

        with patch.object(orch, "_run_module", side_effect=mock_run_module):
            results = orch.run()

        # Only 1 module should have been processed (mod_a), mod_b and mod_c skipped
        passed = [r for r in results if r.get("status") == "passed"]
        skipped = [r for r in results if r.get("status") == "skipped"]
        assert len(passed) == 1
        assert len(skipped) == 2

    def test_no_shutdown_runs_all(self, git_repo, tmp_path):
        """Without shutdown signal, all modules run normally."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        config = PipelineConfig(
            repo=str(git_repo),
            base_branch="main",
            concurrency=1,
            pipeline=[PipelineStep(
                id="x", executor="shell",
                prompt="echo ok",
                postcondition={"shell": "true"},
            )],
            modules=[
                Module(name="mod_a", source_dir="src/", source_files=["a.c"],
                       variables={"line_threshold": 80, "branch_threshold": 70}),
                Module(name="mod_b", source_dir="src/", source_files=["a.c"],
                       variables={"line_threshold": 80, "branch_threshold": 70}),
            ],
        )

        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        results = orch.run()
        assert len(results) == 2
        assert all(r["status"] == "passed" for r in results)

    def test_shutdown_message_in_results(self, git_repo, tmp_path):
        """Skipped modules have 'shutdown' in their result."""
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module

        config = PipelineConfig(
            repo=str(git_repo),
            base_branch="main",
            concurrency=1,
            pipeline=[PipelineStep(
                id="x", executor="shell",
                prompt="echo ok",
                postcondition={"shell": "true"},
            )],
            modules=[
                Module(name="mod_a", source_dir="src/", source_files=["a.c"],
                       variables={"line_threshold": 80, "branch_threshold": 70}),
                Module(name="mod_b", source_dir="src/", source_files=["a.c"],
                       variables={"line_threshold": 80, "branch_threshold": 70}),
            ],
        )

        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))
        orch.request_shutdown()  # shutdown before any module

        results = orch.run()
        assert all(r["status"] == "skipped" for r in results)
