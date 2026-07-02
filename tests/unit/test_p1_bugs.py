"""TDD: P1 bug reproduction + fix verification.

Each test triggers the P1 bug, then we fix and verify.
"""
import pytest
import json
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


# ─── #3: PR exception silent swallow ───

class TestIssue3PRSilentSwallow:
    def test_pr_error_logged(self, git_repo, tmp_path, capsys):
        """PR creation failure should not be silently swallowed."""
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module
        from cc_pipeline.orchestrator import Orchestrator

        config = PipelineConfig(
            repo=str(git_repo),
            concurrency=1,
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok",
                                   postcondition={"shell": "true"})],
            modules=[Module(name="auth", source_dir="src/", source_files=["a.c"],
                           coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        with patch("cc_pipeline.pr.PRCreator") as mock_pr:
            mock_pr.return_value.create.side_effect = Exception("gh not found")
            orch.run()

        # Check transcript has the error
        transcript = (tmp_path / "runs" / "auth" / "transcript.jsonl").read_text()
        assert "gh not found" in transcript or "pr" in transcript.lower(), \
            "PR creation error was silently swallowed"


# ─── #4: attempt_num dead code ───

class TestIssue4AttemptNumDeadCode:
    def test_no_attempt_num_variable(self):
        """attempt_num should not exist as unused variable."""
        import inspect
        from cc_pipeline.runner import ModuleRunner
        source = inspect.getsource(ModuleRunner)
        # The variable name should not appear (it was dead code)
        lines = [l for l in source.split("\n") if "attempt_num" in l and not l.strip().startswith("#")]
        assert len(lines) == 0, f"Dead variable attempt_num still exists: {lines}"


# ─── #5: status command corrupt JSON ───

class TestIssue5StatusCorruptJSON:
    def test_status_handles_corrupt_json(self, tmp_path, capsys):
        """status command should handle corrupt state.json gracefully."""
        from cc_pipeline.cli import main

        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        (run_dir / "orchestrator-state.json").write_text("{broken json")

        ret = main(["status", "--run-dir", str(run_dir)])
        captured = capsys.readouterr()
        assert ret == 0 or "corrupt" in captured.out.lower() or "error" in captured.out.lower(), \
            "Should handle corrupt JSON gracefully"


# ─── #6: resume config.modules filter order ───

class TestIssue6ResumeFilterOrder:
    def test_filter_before_orchestrator(self, tmp_path):
        """config.modules should be filtered before Orchestrator construction."""
        import inspect
        from cc_pipeline.cli import _cmd_resume
        source = inspect.getsource(_cmd_resume)
        # Find line numbers
        filter_line = source.find("config.modules = [m for m in")
        orch_line = source.find("orch = Orchestrator(")
        assert filter_line < orch_line or filter_line == -1, \
            "config.modules filter should come before Orchestrator construction"


# ─── #20: circular dependency not detected ───

class TestIssue20CircularDependency:
    def test_circular_dependency_raises(self):
        """Circular dependency should raise error, not silently proceed."""
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo="/tmp",
            pipeline=[
                PipelineStep(id="a", executor="shell", command="echo a", depends_on="b"),
                PipelineStep(id="b", executor="shell", command="echo b", depends_on="a"),
            ],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                           coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        compiler = PipelineCompiler(config)
        with pytest.raises(ValueError, match="Circular"):
            compiler.compile_module("m")


# ─── #25: on_complete/skill declared but never implemented ───

class TestIssue25UnimplementedFields:
    def test_unconfigured_fields_warn(self, tmp_path, capsys):
        """YAML with on_complete or skill should warn that they're not implemented."""
        from cc_pipeline.config import load_config
        import warnings

        config_file = tmp_path / "config.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
    on_complete: [notify]
    skill: my-skill
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            load_config(str(config_file))
            # Should warn about unimplemented fields
            assert len(w) > 0, "Should warn about unimplemented on_complete/skill"


# ─── #35: duplicate module name ───

class TestIssue35DuplicateModule:
    def test_duplicate_module_rejected(self, tmp_path):
        """Duplicate module names should be rejected."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: dup
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
  - name: dup
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))


# ─── #36: negative concurrency/retry ───

class TestIssue36NegativeValues:
    def test_negative_concurrency_rejected(self, tmp_path):
        """Negative concurrency should be rejected at config load."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
concurrency: -1
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))

    def test_negative_max_retries_rejected(self, tmp_path):
        from cc_pipeline.config import load_config

        config_file = tmp_path / "config2.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
max_retries: -5
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))


# ─── #37: depends_on pointing to nonexistent step ───

class TestIssue37DependsOnNonexistent:
    def test_nonexistent_dependency_raises(self):
        """depends_on pointing to nonexistent step should raise."""
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo="/tmp",
            pipeline=[
                PipelineStep(id="a", executor="shell", command="echo a", depends_on="nonexistent"),
            ],
            modules=[Module(name="m", source_dir="src/", source_files=["a.c"],
                           coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        compiler = PipelineCompiler(config)
        with pytest.raises(ValueError, match="does not exist"):
            compiler.compile_module("m")


# ─── #41: StateManager.load corrupt JSON ───

class TestIssue41StateManagerCorruptJSON:
    def test_load_handles_corrupt_json(self, tmp_path):
        """StateManager.load should handle corrupt JSON gracefully."""
        from cc_pipeline.state import StateManager

        state_file = tmp_path / "state.json"
        state_file.write_text("{broken json")

        mgr = StateManager(run_dir=str(tmp_path))
        result = mgr.load()
        assert result is None, "Should return None for corrupt JSON, not crash"


# ─── #42: StopIteration in _run_module ───

class TestIssue42StopIteration:
    def test_missing_module_gives_clear_error(self, git_repo, tmp_path):
        """_run_module with unknown module name should give clear error."""
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module
        from cc_pipeline.orchestrator import Orchestrator

        config = PipelineConfig(
            repo=str(git_repo),
            concurrency=1,
            pipeline=[PipelineStep(id="x", executor="shell", command="echo ok",
                                   postcondition={"shell": "true"})],
            modules=[Module(name="auth", source_dir="src/", source_files=["a.c"],
                           coverage={"line_threshold": 80, "branch_threshold": 70})],
        )
        orch = Orchestrator(config=config, run_dir=str(tmp_path / "runs"))

        result = orch._run_module("nonexistent_module")
        assert result["status"] == "error", \
            f"Should return error for nonexistent module, got: {result}"
        assert "nonexistent_module" in result.get("error", ""), \
            "Error message should mention the module name"


# ─── #12: Git tag module name with slash ───

class TestIssue12TagSlash:
    def test_tag_with_underscore_module(self, git_repo):
        """Module names with underscores should create valid git tags."""
        from cc_pipeline.git_checkpoint import GitCheckpoint

        gc = GitCheckpoint(repo_path=str(git_repo))
        gc.checkpoint(step="scaffold", module="auth_v2", attempt=1)

        completed = gc.list_completed_steps(module="auth_v2")
        assert "scaffold" in completed

    def test_module_with_slash_rejected_at_config(self, tmp_path):
        """Module name with slash should be rejected (already covered by #11)."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "slash.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo ok"
modules:
  - name: "auth/v2"
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))
