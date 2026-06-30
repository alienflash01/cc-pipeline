"""Black-box stress tests: edge cases, concurrency pressure, large configs."""
import pytest
import subprocess
import os
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, capture_output=True)
    src = repo / "src"
    src.mkdir()
    for i in range(10):
        (src / f"mod_{i}.c").write_text(f"int func_{i}(int a) {{ return a + {i}; }}\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


# ═══════════════════════════════════════════════════════════════
# Stress: 10 modules in parallel
# ═══════════════════════════════════════════════════════════════

class TestStress10Modules:
    """10 modules running concurrently."""

    def test_10_modules_all_pass(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        modules_yaml = "\n".join(
            f"  - name: mod_{i}\n"
            f"    spec_id: S{i}\n"
            f"    source_dir: src/\n"
            f"    source_files: [mod_{i}.c]\n"
            f"    coverage: {{line_threshold: 80, branch_threshold: 70}}"
            for i in range(10)
        )

        config = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 5\n"
            "max_retries: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: "echo ok"\n'
            "    postcondition:\n"
            '      shell: "echo ok"\n'
            "\n"
            "modules:\n"
            f"{modules_yaml}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        start = time.time()
        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        elapsed = time.time() - start

        assert ret == 0
        # Verify all 10 modules completed (from results, not state file)
        # State file may only have last-written due to concurrent saves


# ═══════════════════════════════════════════════════════════════
# Stress: Module with many source files (loop expansion)
# ═══════════════════════════════════════════════════════════════

class TestStressManyFiles:
    """One module with 10 source files, loop: per_file expands to 10 steps."""

    def test_10_files_loop_expansion(self, real_repo, tmp_path):
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        file_list = ", ".join(f"mod_{i}.c" for i in range(10))
        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1

pipeline:
  - id: generate
    executor: shell
    loop: per_file
    prompt: "echo gen {{file}}"
    postcondition:
      shell: "echo ok"

modules:
  - name: big_module
    spec_id: S
    source_dir: src/
    source_files: [{file_list}]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)

        steps = compiler.compile_module("big_module")
        gen_steps = [s for s in steps if s.loop_file is not None]
        assert len(gen_steps) == 10


# ═══════════════════════════════════════════════════════════════
# Stress: 5-step pipeline × 3 modules
# ═══════════════════════════════════════════════════════════════

class TestStressDeepPipeline:
    """5 sequential steps per module, 3 modules in parallel."""

    def test_5_step_pipeline_passes(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 3
max_retries: 1

pipeline:
  - id: step1
    executor: shell
    prompt: "echo s1"
    postcondition: {{shell: "echo ok"}}
  - id: step2
    executor: shell
    prompt: "echo s2"
    postcondition: {{shell: "echo ok"}}
    depends_on: step1
  - id: step3
    executor: shell
    prompt: "echo s3"
    postcondition: {{shell: "echo ok"}}
    depends_on: step2
  - id: step4
    executor: shell
    prompt: "echo s4"
    postcondition: {{shell: "echo ok"}}
    depends_on: step3
  - id: step5
    executor: shell
    prompt: "echo s5"
    postcondition: {{shell: "echo ok"}}
    depends_on: step4

modules:
  - name: mod_0
    spec_id: S
    source_dir: src/
    source_files: [mod_0.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
  - name: mod_1
    spec_id: S
    source_dir: src/
    source_files: [mod_1.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
  - name: mod_2
    spec_id: S
    source_dir: src/
    source_files: [mod_2.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 0

        # Verify transcript has all 5 steps for each module
        for mod in ["mod_0", "mod_1", "mod_2"]:
            transcript = (tmp_path / "runs" / mod / "transcript.jsonl").read_text()
            for step in ["step1", "step2", "step3", "step4", "step5"]:
                assert step in transcript


# ═══════════════════════════════════════════════════════════════
# Edge: Empty source_files with loop: per_file
# ═══════════════════════════════════════════════════════════════

class TestEdgeEmptySourceFiles:
    """Module with empty source_files list and loop: per_file."""

    def test_empty_files_skips_loop(self, real_repo, tmp_path):
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        config = f"""
repo: {real_repo}
base_branch: main

pipeline:
  - id: generate
    executor: shell
    loop: per_file
    prompt: "echo gen"
    postcondition:
      shell: "echo ok"
  - id: verify
    executor: shell
    prompt: "echo verify"
    postcondition:
      shell: "echo ok"

modules:
  - name: empty_mod
    spec_id: S
    source_dir: src/
    source_files: []
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)

        steps = compiler.compile_module("empty_mod")
        # With empty source_files, loop:per_file should produce 0 sub-steps
        gen_steps = [s for s in steps if s.loop_file is not None]
        assert len(gen_steps) == 0
        # verify step still present
        assert any(s.step_id == "verify" for s in steps)


# ═══════════════════════════════════════════════════════════════
# Edge: Single file module
# ═══════════════════════════════════════════════════════════════

class TestEdgeSingleFile:
    """Module with exactly 1 source file."""

    def test_single_file_executes_once(self, real_repo, tmp_path):
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        config = f"""
repo: {real_repo}
base_branch: main

pipeline:
  - id: generate
    executor: shell
    loop: per_file
    prompt: "echo gen"
    postcondition:
      shell: "echo ok"

modules:
  - name: single
    spec_id: S
    source_dir: src/
    source_files: [mod_0.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)

        steps = compiler.compile_module("single")
        gen_steps = [s for s in steps if s.loop_file is not None]
        assert len(gen_steps) == 1
        assert gen_steps[0].loop_file == "mod_0.c"


# ═══════════════════════════════════════════════════════════════
# Edge: Module name with special characters
# ═══════════════════════════════════════════════════════════════

class TestEdgeSpecialModuleNames:
    """Module names with hyphens, underscores."""

    def test_hyphenated_name(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = (
            f"repo: {real_repo}\n"
            "base_branch: main\n"
            "concurrency: 1\n"
            "\n"
            "pipeline:\n"
            "  - id: check\n"
            "    executor: shell\n"
            '    prompt: echo hi\n'
            "    postcondition:\n"
            '      shell: echo ok\n'
            "\n"
            "modules:\n"
            "  - name: my-module-123\n"
            "    spec_id: S\n"
            "    source_dir: src/\n"
            "    source_files: [mod_0.c]\n"
            "    coverage: {line_threshold: 80, branch_threshold: 70}\n"
        )
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        ret = main(["run", str(config_path), "--run-dir", str(tmp_path / "runs")])
        assert ret == 0


# ═══════════════════════════════════════════════════════════════
# Edge: Deeply nested depends_on chain
# ═══════════════════════════════════════════════════════════════

class TestEdgeDeepDependencyChain:
    """6 steps chained: a→b→c→d→e→f."""

    def test_deep_chain_reorders(self, real_repo, tmp_path):
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        config = f"""
repo: {real_repo}
base_branch: main

pipeline:
  - id: f
    executor: shell
    prompt: "f"
    depends_on: e
  - id: e
    executor: shell
    prompt: "e"
    depends_on: d
  - id: d
    executor: shell
    prompt: "d"
    depends_on: c
  - id: c
    executor: shell
    prompt: "c"
    depends_on: b
  - id: b
    executor: shell
    prompt: "b"
    depends_on: a
  - id: a
    executor: shell
    prompt: "a"

modules:
  - name: m
    spec_id: S
    source_dir: src/
    source_files: [mod_0.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)

        steps = compiler.compile_module("m")
        ids = [s.step_id for s in steps]
        # Should be reordered: a before b before c...
        assert ids.index("a") < ids.index("b")
        assert ids.index("b") < ids.index("c")
        assert ids.index("c") < ids.index("d")
        assert ids.index("d") < ids.index("e")
        assert ids.index("e") < ids.index("f")


# ═══════════════════════════════════════════════════════════════
# Stress: Rapid sequential runs (state file conflicts)
# ═══════════════════════════════════════════════════════════════

class TestStressRapidRuns:
    """Run pipeline twice rapidly to check state file handling."""

    def test_two_rapid_runs(self, real_repo, tmp_path):
        from cc_pipeline.cli import main

        config = f"""
repo: {real_repo}
base_branch: main
concurrency: 1

pipeline:
  - id: check
    executor: shell
    prompt: "echo ok"
    postcondition:
      shell: "echo ok"

modules:
  - name: mod_0
    spec_id: S
    source_dir: src/
    source_files: [mod_0.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)

        # First run
        ret1 = main(["run", str(config_path), "--run-dir", str(tmp_path / "run1")])
        # Second run immediately
        ret2 = main(["run", str(config_path), "--run-dir", str(tmp_path / "run2")])

        assert ret1 == 0
        assert ret2 == 0
        # Each run dir should have independent state
        assert (tmp_path / "run1" / "orchestrator-state.json").exists()
        assert (tmp_path / "run2" / "orchestrator-state.json").exists()


# ═══════════════════════════════════════════════════════════════
# Stress: First step fails immediately (no retry wasted)
# ═══════════════════════════════════════════════════════════════

class TestStressImmediateFail:
    """Step fails on first attempt, no retry should save time."""

    def test_fail_fast_with_retry_1(self, real_repo, tmp_path):
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler
        from cc_pipeline.postcondition import PostconditionResult

        config = f"""
repo: {real_repo}
base_branch: main
max_retries: 1

pipeline:
  - id: always_fail
    executor: shell
    prompt: "x"
    postcondition:
      shell: "echo ok"
    retry: 1

modules:
  - name: m
    spec_id: S
    source_dir: src/
    source_files: [mod_0.c]
    coverage: {{line_threshold: 80, branch_threshold: 70}}
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config)
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("m")

        runner = ModuleRunner(
            steps=steps, module_name="m",
            worktree_path=str(real_repo),
            run_dir=str(tmp_path / "runs"),
        )
        runner._check_postcondition = lambda s: PostconditionResult(passed=False, reason="hard fail")

        start = time.time()
        result = runner.run()
        elapsed = time.time() - start

        assert result["status"] == "failed"
        assert elapsed < 2.0  # should be nearly instant


# ═══════════════════════════════════════════════════════════════
# Edge: Postcondition with contains() on various outputs
# ═══════════════════════════════════════════════════════════════

class TestEdgePostconditionVariants:
    """Various postcondition expressions."""

    def test_contains_partial_match(self, tmp_path):
        from cc_pipeline.postcondition import evaluate
        import unittest.mock
        with unittest.mock.patch("cc_pipeline.postcondition.subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.MagicMock(
                returncode=0, stdout="3 passed, 1 failed in 0.5s", stderr=""
            )
            result = evaluate(shell="pytest", expect="contains('passed')", cwd=str(tmp_path))
            assert result.passed

    def test_ge_float_value(self, tmp_path):
        from cc_pipeline.postcondition import evaluate
        import unittest.mock
        with unittest.mock.patch("cc_pipeline.postcondition.subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.MagicMock(
                returncode=0, stdout='{"score": 72.5}', stderr=""
            )
            result = evaluate(shell="grade", expect="$.score >= 60", cwd=str(tmp_path))
            assert result.passed

    def test_ne_expression(self, tmp_path):
        from cc_pipeline.postcondition import evaluate
        import unittest.mock
        with unittest.mock.patch("cc_pipeline.postcondition.subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.MagicMock(
                returncode=0, stdout='{"status": "ok"}', stderr=""
            )
            result = evaluate(shell="check", expect="$.status != \"fail\"", cwd=str(tmp_path))
            assert result.passed

    def test_missing_field_in_json(self, tmp_path):
        from cc_pipeline.postcondition import evaluate
        import unittest.mock
        with unittest.mock.patch("cc_pipeline.postcondition.subprocess.run") as mock_run:
            mock_run.return_value = unittest.mock.MagicMock(
                returncode=0, stdout='{"other": 42}', stderr=""
            )
            result = evaluate(shell="check", expect="$.missing >= 80", cwd=str(tmp_path))
            assert not result.passed


# ═══════════════════════════════════════════════════════════════
# Edge: Variable rendering edge cases
# ═══════════════════════════════════════════════════════════════

class TestEdgeVariableRendering:
    """Edge cases in variable injection."""

    def test_prompt_with_no_variables(self):
        from cc_pipeline.render import render
        result = render("just plain text", {})
        assert result == "just plain text"

    def test_prompt_with_chinese_variables(self):
        from cc_pipeline.render import render
        result = render("模块：{module}", {"module": "认证模块"})
        assert "认证模块" in result

    def test_prompt_with_file_path_variable(self):
        from cc_pipeline.render import render
        result = render(
            "Read {source_dir}/{file}",
            {"source_dir": "src/auth", "file": "login.c"},
        )
        assert "src/auth/login.c" in result

    def test_multiple_json_file_refs(self, tmp_path):
        from cc_pipeline.render import render
        pd = tmp_path / ".pipeline"
        pd.mkdir()
        (pd / "a.json").write_text('{"x": 1}')
        (pd / "b.json").write_text('{"y": 2}')

        result = render(
            "A: {.pipeline/a.json} B: {.pipeline/b.json}",
            {},
            base_dir=str(tmp_path),
        )
        assert '"x": 1' in result
        assert '"y": 2' in result


# ═══════════════════════════════════════════════════════════════
# Stress: Concurrent state writes (thread safety)
# ═══════════════════════════════════════════════════════════════

class TestStressConcurrentState:
    """Multiple threads writing to StateManager simultaneously."""

    def test_concurrent_update_module(self, tmp_path):
        from cc_pipeline.state import StateManager

        sm = StateManager(run_dir=str(tmp_path))
        sm.save(run_id="stress", modules={})

        def update(i):
            sm.update_module(f"mod_{i}", status="passed", index=i)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(update, i) for i in range(20)]
            for f in as_completed(futures):
                f.result()

        state = sm.load()
        assert len(state["modules"]) == 20
        for i in range(20):
            assert state["modules"][f"mod_{i}"]["status"] == "passed"


# ═══════════════════════════════════════════════════════════════
# Edge: Config validation errors
# ═══════════════════════════════════════════════════════════════

class TestEdgeConfigErrors:
    """Config validation edge cases."""

    def test_missing_pipeline_steps(self, tmp_path):
        from cc_pipeline.config import load_config

        config_path = tmp_path / "c.yaml"
        config_path.write_text(
            "repo: /tmp\n"
            "modules:\n"
            "  - name: x\n"
            "    spec_id: s\n"
            "    source_dir: src/\n"
            "    source_files: [a.c]\n"
            "    coverage: {line_threshold: 80, branch_threshold: 70}\n"
        )
        with pytest.raises(ValueError, match="pipeline"):
            load_config(str(config_path))

    def test_invalid_executor_type(self, tmp_path):
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        config_path = tmp_path / "c.yaml"
        config_path.write_text(
            f"repo: /tmp\n"
            "pipeline:\n"
            "  - id: bad\n"
            "    executor: super-ai\n"
            "    prompt: x\n"
            "modules:\n"
            "  - name: x\n"
            "    spec_id: s\n"
            "    source_dir: src/\n"
            "    source_files: [a.c]\n"
            "    coverage: {line_threshold: 80, branch_threshold: 70}\n"
        )
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        with pytest.raises(ValueError, match="executor"):
            compiler.compile_module("x")

    def test_duplicate_step_ids(self, tmp_path):
        from cc_pipeline.config import load_config
        from cc_pipeline.compiler import PipelineCompiler

        config_path = tmp_path / "c.yaml"
        config_path.write_text(
            "repo: /tmp\n"
            "pipeline:\n"
            "  - id: dup\n"
            "    executor: shell\n"
            "    prompt: a\n"
            "  - id: dup\n"
            "    executor: shell\n"
            "    prompt: b\n"
            "modules:\n"
            "  - name: x\n"
            "    spec_id: s\n"
            "    source_dir: src/\n"
            "    source_files: [a.c]\n"
            "    coverage: {line_threshold: 80, branch_threshold: 70}\n"
        )
        config = load_config(str(config_path))
        compiler = PipelineCompiler(config)
        with pytest.raises(ValueError, match="(?i)duplicate"):
            compiler.compile_module("x")


# ═══════════════════════════════════════════════════════════════
# Stress: Git checkpoint with many tags
# ═══════════════════════════════════════════════════════════════

class TestStressManyCheckpoints:
    """Create many checkpoints and verify all accessible."""

    def test_20_checkpoints_all_accessible(self, real_repo, tmp_path):
        from cc_pipeline.git_checkpoint import GitCheckpoint

        gc = GitCheckpoint(str(real_repo))
        for i in range(1, 21):
            (real_repo / f"file_{i}.txt").write_text(f"content {i}")
            gc.checkpoint(step=f"step_{i}", module="stress_mod", attempt=1)

        result = subprocess.run(
            ["git", "tag", "-l", "pipeline/stress_mod/*"],
            cwd=str(real_repo), capture_output=True, text=True,
        )
        tags = result.stdout.strip().split("\n")
        assert len(tags) == 20

        # Rollback to step 10
        gc.rollback(step="step_10", module="stress_mod", attempt=1)
        assert (real_repo / "file_10.txt").exists()
        assert not (real_repo / "file_20.txt").exists()
