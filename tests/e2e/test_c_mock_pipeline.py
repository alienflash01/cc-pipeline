"""P0-2: C-mock project + shell executor pipeline tests."""
import pytest
import subprocess, os
from pathlib import Path
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.compiler import PipelineCompiler
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.executor import ShellExecutor, CCResult


def _make_c_repo(tmp_path):
    """Create a mock C project with auth/crypto modules."""
    repo = tmp_path / "repo"
    for mod, files in [
        ("auth", ["auth_login.c", "auth_token.c"]),
        ("crypto", ["aes.c", "rsa.c"]),
    ]:
        mod_dir = repo / "src" / mod
        mod_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            (mod_dir / f).write_text(f"int {f[:-2]}() {{ return 0; }}\n")

    # Makefile
    (repo / "Makefile").write_text("test:\n\t@echo '4/4 passed'\n")

    # Init git
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "test"}
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=env)

    return repo


def _make_shell_config(repo_path):
    """Create pipeline config with shell executors only (no CC needed)."""
    return PipelineConfig(
        repo=str(repo_path),
        pipeline=[
            PipelineStep(
                id="check", executor="shell",
                prompt="echo checking {module}",
                postcondition={"shell": "echo ok", "expect": "true"},
            ),
            PipelineStep(
                id="build", executor="shell",
                prompt="echo building {file}",
                loop="per_file",
            ),
            PipelineStep(
                id="test", executor="shell",
                prompt="make test",
                postcondition={"shell": "make test 2>&1 | tail -1", "expect": "contains('passed')"},
            ),
        ],
        modules=[
            Module(
                name="auth",
                source_dir="src/auth/",
                source_files=["auth_login.c", "auth_token.c"],
                file_order="sequential",
            ),
        ],
    )


class TestCMockShellPipeline:
    """Full pipeline with shell executors on a mock C project."""

    def test_pipeline_pass(self, tmp_path):
        """Normal full pipeline passes: check → build × 2 → test."""
        repo = _make_c_repo(tmp_path)
        config = _make_shell_config(repo)
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")
        assert len(steps) == 4  # check + build[a] + build[b] + test

        # Verify execution order
        runner = ModuleRunner(
            steps=steps, module_name="auth",
            worktree_path=str(repo),
            run_dir=str(tmp_path / "runs"),
        )
        result = runner.run()
        assert result["status"] == "passed"

    def test_postcondition_fail_retries(self, tmp_path):
        """Postcondition failure triggers retry."""
        repo = _make_c_repo(tmp_path)

        # Make make test fail on first call, pass on second
        call_count = [0]
        original_run = ShellExecutor.run

        class FlakyShell(ShellExecutor):
            def run(self, command, cwd, timeout=None):
                if "make test" in command:
                    call_count[0] += 1
                    if call_count[0] == 1:
                        return CCResult(1, "", "make: error")
                return super().run(command, cwd, timeout)

        config = _make_shell_config(repo)
        config.pipeline[2].retry = 2  # test step
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")

        runner = ModuleRunner(
            steps=steps, module_name="auth",
            worktree_path=str(repo),
            run_dir=str(tmp_path / "runs"),
            shell_executor=FlakyShell(),
        )
        result = runner.run()
        assert result["status"] == "passed"

    def test_file_order_sequential(self, tmp_path):
        """file_order: sequential — build[a] before build[b]."""
        repo = _make_c_repo(tmp_path)
        config = _make_shell_config(repo)
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")

        # With sequential, steps should be:
        # check, build[auth_login.c], build[auth_token.c], test
        # (check and test are non-loop, so they bookend)
        assert steps[0].step_id == "check"
        assert steps[1].step_id == "build"
        assert steps[1].loop_file == "auth_login.c"
        assert steps[2].step_id == "build"
        assert steps[2].loop_file == "auth_token.c"
        assert steps[3].step_id == "test"

    def test_dry_run_preview(self, tmp_path):
        """Dry-run correctly previews the pipeline."""
        repo = _make_c_repo(tmp_path)
        config = _make_shell_config(repo)
        compiler = PipelineCompiler(config)
        steps = compiler.compile_module("auth")

        # Verify steps are correctly compiled
        assert len(steps) == 4
        ids = [s.step_id for s in steps]
        assert ids == ["check", "build", "build", "test"]

        # Verify variables were rendered
        assert "auth" in steps[0].rendered_prompt  # {module} → auth
