"""TDD: Module Runner — executes a compiled pipeline for one module."""
import pytest
import json
import subprocess
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, call


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


@pytest.fixture
def real_repo(tmp_path):
    """Real git repo with source files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    src = repo / "src"
    src.mkdir()
    (src / "math.c").write_text("int add(int a, int b) { return a + b; }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


RUNNER_YAML = '''
repo: PLACEHOLDER
base_branch: master
concurrency: 1
max_retries: 2

pipeline:
  - id: scaffold
    executor: claude-code
    prompt: "Create test dir for {module}"
    postcondition:
      shell: "echo passed"
    output: scaffold.json

  - id: generate
    executor: claude-code
    loop: per_file
    prompt: "Generate test for {file}"
    postcondition:
      shell: "echo passed"
    retry: 2
    depends_on: scaffold

modules:
  - name: math
    spec_id: SPEC-1
    source_dir: src/
    source_files:
      - math.c
    coverage:
      line_threshold: 80
'''


class TestModuleRunner:
    """Test the module pipeline runner — serial step execution."""

    def test_importable(self):
        from cc_pipeline.runner import ModuleRunner
        assert ModuleRunner is not None

    def test_run_executes_steps_in_order(self, real_repo, tmp_path):
        """Runner executes compiled steps sequentially."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        yaml = RUNNER_YAML.replace("PLACEHOLDER", str(real_repo))
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        run_dir = tmp_path / "runs"
        runner = ModuleRunner(
            steps=steps,
            module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(run_dir),
            cc_executor=None,  # no real CC
        )

        # With mock executor
        with patch.object(runner, "_execute_step") as mock_exec:
            mock_exec.return_value = MagicMock(returncode=0, stdout='{"passed":true}', stderr="")
            result = runner.run()

        assert mock_exec.call_count >= 2  # scaffold + at least 1 generate

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_passes_on_success(self, mock_subproc, real_repo, tmp_path):
        """Runner returns success when all steps pass."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        yaml = RUNNER_YAML.replace("PLACEHOLDER", str(real_repo))
        config_path = tmp_path / "c.yaml"
        config_path.write_text(yaml)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        mock_subproc.return_value = MagicMock(returncode=0, stdout="done", stderr="")
        runner = ModuleRunner(
            steps=steps,
            module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )
        result = runner.run()
        assert result["status"] == "passed"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_retries_on_failure(self, mock_subproc, real_repo, tmp_path):
        """Runner retries when postcondition fails, then succeeds."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.postcondition import PostconditionResult

        yaml = RUNNER_YAML.replace("PLACEHOLDER", str(real_repo))
        config_path = tmp_path / "c.yaml"
        config_path.write_text(yaml)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        mock_subproc.return_value = MagicMock(returncode=0, stdout="done", stderr="")
        runner = ModuleRunner(
            steps=steps,
            module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )

        # Mock postcondition: first step fails once then passes
        call_count = [0]
        original_check = runner._check_postcondition
        def mock_check(step):
            call_count[0] += 1
            if call_count[0] == 1:
                return PostconditionResult(passed=False, reason="simulated fail")
            return PostconditionResult(passed=True, reason="simulated pass")
        runner._check_postcondition = mock_check

        result = runner.run()
        assert result["status"] == "passed"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_run_fails_after_max_retries(self, mock_subproc, real_repo, tmp_path):
        """Runner marks module as failed after exhausting retries."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.postcondition import PostconditionResult

        yaml = RUNNER_YAML.replace("PLACEHOLDER", str(real_repo))
        config_path = tmp_path / "c.yaml"
        config_path.write_text(yaml)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        mock_subproc.return_value = MagicMock(returncode=0, stdout="done", stderr="")
        runner = ModuleRunner(
            steps=steps,
            module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )
        runner._check_postcondition = lambda step: PostconditionResult(passed=False, reason="always fail")

        result = runner.run()
        assert result["status"] == "failed"

    @patch("cc_pipeline.executor.subprocess.run")
    def test_logger_writes_events(self, mock_subproc, real_repo, tmp_path):
        """Runner logs events to transcript.jsonl."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        yaml = RUNNER_YAML.replace("PLACEHOLDER", str(real_repo))
        (tmp_path / "c.yaml").write_text(yaml)
        config = load_config(str(tmp_path / "c.yaml"))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        mock_subproc.return_value = MagicMock(returncode=0, stdout='{"passed":true}', stderr="")
        run_dir = tmp_path / "runs"
        runner = ModuleRunner(
            steps=steps,
            module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(run_dir),
        )
        runner.run()

        transcript = run_dir / "math" / "transcript.jsonl"
        assert transcript.exists()
        entries = [json.loads(l) for l in transcript.read_text().strip().split("\n") if l]
        events = [e["event"] for e in entries]
        assert "step_start" in events
        assert "pass" in events
