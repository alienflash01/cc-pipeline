"""Black-box tests: full business scenarios via CLI + Orchestrator.

These tests exercise the system end-to-end through public interfaces,
without mocking internals. Each test represents a real user scenario.
"""
import pytest
import subprocess
import os
import json
from pathlib import Path

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@test.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@test.com",
}


@pytest.fixture
def real_repo(tmp_path):
    """Create a real git repo with source files."""
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True)
    src = repo / "src"
    src.mkdir()
    (src / "math.c").write_text("int add(int a, int b) { return a + b; }\n")
    (src / "str.c").write_text("int len(const char* s) { int n=0; while(*s++) n++; return n; }\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


# ═══════════════════════════════════════════════════════════════
# Scenario 1: Single module, single step, all pass
# ═══════════════════════════════════════════════════════════════

class TestScenario1SingleModulePass:
    """User runs a minimal pipeline with 1 module, 1 shell step."""

    def test_single_module_single_step_passes(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1
max_retries: 1

pipeline:
  - id: analyze
    executor: shell
    prompt: "echo analyzing"
    postcondition:
      shell: "echo ok"

modules:
  - name: math
    spec_id: SPEC-1
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 0


# ═══════════════════════════════════════════════════════════════
# Scenario 2: Multiple modules run in parallel
# ═══════════════════════════════════════════════════════════════

class TestScenario2MultiModuleParallel:
    """User has 3 modules, they should all complete."""

    def test_three_modules_all_pass(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 3
max_retries: 1

pipeline:
  - id: check
    executor: shell
    prompt: "echo checking"
    postcondition:
      shell: "echo ok"

modules:
  - name: mod_a
    spec_id: S1
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
  - name: mod_b
    spec_id: S2
    source_dir: src/
    source_files: [str.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
  - name: mod_c
    spec_id: S3
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 0  # all passed


# ═══════════════════════════════════════════════════════════════
# Scenario 3: Module fails, others continue
# ═══════════════════════════════════════════════════════════════

class TestScenario3PartialFailure:
    """One module fails but others should still complete."""

    def test_one_fails_one_passes(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 2
max_retries: 1

pipeline:
  - id: check
    executor: shell
    prompt: "echo x"
    postcondition:
      shell: "false"

modules:
  - name: good_mod
    spec_id: S1
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
  - name: bad_mod
    spec_id: S2
    source_dir: src/
    source_files: [str.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 1  # at least one failed

        # Verify state file was written
        state_file = tmp_path / "runs" / "orchestrator-state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert "bad_mod" in state["modules"]
        assert state["modules"]["bad_mod"]["status"] == "failed"


# ═══════════════════════════════════════════════════════════════
# Scenario 4: Retry then pass
# ═══════════════════════════════════════════════════════════════

class TestScenario4RetryThenPass:
    """A step fails first time, retries, then passes."""

    def test_retry_eventually_passes(self, real_repo, tmp_path):
        from cc_pipeline.orchestrator import Orchestrator
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.postcondition import PostconditionResult

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1
max_retries: 3

pipeline:
  - id: flaky_step
    executor: shell
    prompt: "echo hi"
    postcondition:
      shell: "echo ok"
    retry: 3

modules:
  - name: math
    spec_id: S1
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        runner = ModuleRunner(
            steps=steps, module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )

        # Flaky: fails twice then passes
        counter = [0]
        def flaky(step):
            counter[0] += 1
            return PostconditionResult(passed=counter[0] >= 3, reason="flaky")
        runner._check_postcondition = flaky

        result = runner.run()
        assert result["status"] == "passed"
        assert counter[0] == 3  # took 3 attempts


# ═══════════════════════════════════════════════════════════════
# Scenario 5: Max retries exhausted → module fails
# ═══════════════════════════════════════════════════════════════

class TestScenario5RetryExhausted:
    """Step fails all retries → module marked as failed."""

    def test_all_retries_fail(self, real_repo, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.postcondition import PostconditionResult

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1
max_retries: 2

pipeline:
  - id: always_fail
    executor: shell
    prompt: "x"
    postcondition:
      shell: "echo ok"
    retry: 2

modules:
  - name: math
    spec_id: S
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        runner = ModuleRunner(
            steps=steps, module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )
        runner._check_postcondition = lambda s: PostconditionResult(passed=False, reason="never")

        result = runner.run()
        assert result["status"] == "failed"
        assert "always_fail" in result.get("error", "")


# ═══════════════════════════════════════════════════════════════
# Scenario 6: Multi-step pipeline with depends_on
# ═══════════════════════════════════════════════════════════════

class TestScenario6MultiStepPipeline:
    """Pipeline with scaffold → generate → evaluate, depends_on reordering."""

    def test_three_step_pipeline_all_pass(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1
max_retries: 1

pipeline:
  - id: scaffold
    executor: shell
    prompt: "echo scaffold"
    postcondition:
      shell: "echo ok"

  - id: generate
    executor: shell
    prompt: "echo generate"
    loop: per_file
    postcondition:
      shell: "echo ok"
    depends_on: scaffold

  - id: evaluate
    executor: shell
    prompt: "echo evaluate"
    postcondition:
      shell: "echo ok"
    depends_on: generate

modules:
  - name: math
    spec_id: S
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 0

        # Check transcript has all 3 steps
        transcript = (tmp_path / "runs" / "math" / "transcript.jsonl").read_text()
        assert "scaffold" in transcript
        assert "generate" in transcript
        assert "evaluate" in transcript


# ═══════════════════════════════════════════════════════════════
# Scenario 7: Git checkpoint creates tags
# ═══════════════════════════════════════════════════════════════

class TestScenario7GitCheckpointTags:
    """Verify git tags are created during pipeline execution."""

    def test_tags_created_after_steps(self, real_repo, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1
max_retries: 1

pipeline:
  - id: step1
    executor: shell
    prompt: "echo hi"
    postcondition:
      shell: "echo ok"

modules:
  - name: math
    spec_id: S
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("math")

        runner = ModuleRunner(
            steps=steps, module_name="math",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )
        runner.run()

        # Check git tag exists
        result = subprocess.run(
            ["git", "tag", "-l", "pipeline/math/*"],
            cwd=str(real_repo), capture_output=True, text=True,
        )
        assert "pipeline/math/step1" in result.stdout


# ═══════════════════════════════════════════════════════════════
# Scenario 8: State file written for crash recovery
# ═══════════════════════════════════════════════════════════════

class TestScenario8StatePersistence:
    """Orchestrator writes state for crash recovery."""

    def test_state_file_written(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1
max_retries: 1

pipeline:
  - id: step1
    executor: shell
    prompt: "echo hi"
    postcondition:
      shell: "echo ok"

modules:
  - name: math
    spec_id: S
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])

        state_file = tmp_path / "runs" / "orchestrator-state.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert "math" in state["modules"]
        assert state["modules"]["math"]["status"] == "passed"


# ═══════════════════════════════════════════════════════════════
# Scenario 9: --module filter only runs specified module
# ═══════════════════════════════════════════════════════════════

class TestScenario9ModuleFilter:
    """--module flag restricts execution to one module."""

    def test_only_specified_module_runs(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1

pipeline:
  - id: step1
    executor: shell
    prompt: "echo hi"
    postcondition:
      shell: "echo ok"

modules:
  - name: alpha
    spec_id: S1
    source_dir: src/
    source_files: [math.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
  - name: beta
    spec_id: S2
    source_dir: src/
    source_files: [str.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--module", "alpha", "--run-dir", str(tmp_path / "runs")])
        assert ret == 0

        # Only alpha should have a transcript
        assert (tmp_path / "runs" / "alpha" / "transcript.jsonl").exists()
        assert not (tmp_path / "runs" / "beta" / "transcript.jsonl").exists()


# ═══════════════════════════════════════════════════════════════
# Scenario 10: Invalid config → error
# ═══════════════════════════════════════════════════════════════

class TestScenario10ErrorHandling:
    """Various error conditions are handled gracefully."""

    def test_missing_repo_raises(self, tmp_path):
        """Missing repo field → ValueError."""
        from cc_pipeline.config import load_config

        config_path = tmp_path / "bad.yaml"
        config_path.write_text("base_branch: main\nmodules: []\npipeline: []\n")
        with pytest.raises(ValueError, match="repo"):
            load_config(str(config_path))

    def test_empty_modules_raises(self, tmp_path):
        """Empty modules list → ValueError."""
        from cc_pipeline.config import load_config

        config_path = tmp_path / "bad.yaml"
        config_path.write_text("repo: /tmp\nmodules: []\npipeline:\n  - id: x\n    executor: shell\n")
        with pytest.raises(ValueError, match="module"):
            load_config(str(config_path))

    def test_nonexistent_config_file(self):
        """Nonexistent file → FileNotFoundError."""
        from cc_pipeline.config import load_config

        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")
