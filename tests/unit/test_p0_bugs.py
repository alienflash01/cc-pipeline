"""TDD: P0 bug reproduction tests — trigger each P0 bug before fixing.

Each test demonstrates the bug exists, then we fix and re-run.
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


# ─── #33 P0: command/prompt_file not parsed by load_config ───

class TestIssue33CommandNotParsed:
    """load_config doesn't read 'command' and 'prompt_file' from YAML."""

    def test_command_parsed_from_yaml(self, tmp_path):
        """YAML with prompt: field for shell executor populates PipelineStep.prompt."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "config.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: verify
    executor: shell
    prompt: echo HELLO
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    variables:
      line_threshold: 80
      branch_threshold: 70
""")
        config = load_config(str(config_file))
        assert config.pipeline[0].prompt == "echo HELLO", \
            "prompt field not parsed from YAML"

    def test_prompt_file_parsed_from_yaml(self, tmp_path):
        """YAML with prompt_file: field should populate PipelineStep.prompt_file."""
        from cc_pipeline.config import load_config

        # Create the prompt file so validation passes
        prompt_dir = tmp_path / "prompts"
        prompt_dir.mkdir()
        (prompt_dir / "gen.md").write_text("Generate tests for {module}")

        config_file = tmp_path / "config.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: gen
    executor: claude-code
    prompt_file: {prompt_dir / "gen.md"}
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
    variables:
      line_threshold: 80
      branch_threshold: 70
""")
        config = load_config(str(config_file))
        prompt_path = str(prompt_dir / "gen.md")
        assert config.pipeline[0].prompt_file == prompt_path, \
            "prompt_file field not parsed from YAML"


# ─── #2 P0: retry:1 = zero retries ───

class TestIssue2RetryOneGivesZeroRetries:
    """retry=1 should allow at least one retry (2 total attempts)."""

    def test_retry_1_allows_one_retry(self, git_repo, tmp_path):
        """With retry=1, first failure should retry, not immediately fail."""
        from cc_pipeline.runner import ModuleRunner
        from cc_pipeline.compiler import CompiledStep
        from cc_pipeline.executor import CCResult

        call_count = [0]

        class FailThenSucceedCC:
            def __init__(self, **kw): pass
            def run(self, prompt, cwd, **kw):
                call_count[0] += 1
                if call_count[0] == 1:
                    return CCResult(returncode=1, stdout="", stderr="error")
                return CCResult(returncode=0, stdout="done", stderr="")

        step = CompiledStep(
            step_id="gen", executor="claude-code",
            rendered_prompt="test", postcondition=None,
            retry=1,  # user expects 1 retry = 2 total attempts
        )

        runner = ModuleRunner(
            steps=[step], module_name="auth",
            worktree_path=str(git_repo),
            run_dir=str(tmp_path / "runs"),
            cc_executor=FailThenSucceedCC(),
        )
        result = runner.run()

        # Bug: retry=1 → retry_budget=1 → if retry_budget > 1 → False → no retry
        assert call_count[0] >= 2, \
            f"Expected at least 2 attempts with retry=1, got {call_count[0]}"


# ─── #1 P0: signal_handler dead code ───

class TestIssue1SignalHandlerDeadCode:
    """_signal_handler should raise KeyboardInterrupt for SIGINT."""

    def test_sigint_raises_keyboard_interrupt(self):
        """Receiving SIGINT signal should raise KeyboardInterrupt in non-daemon."""
        import signal as sig
        from cc_pipeline.cli import _signal_handler

        # Bug: after setting _shutdown_requested=True, the if check is always False
        # So KeyboardInterrupt is never raised
        with pytest.raises(KeyboardInterrupt):
            _signal_handler(sig.SIGINT, None)


# ─── #19 P0: empty source_files + loop:per_file → KeyError ───

class TestIssue19EmptySourceFilesKeyError:
    """loop:per_file with empty source_files should not crash with KeyError."""

    def test_empty_source_files_no_crash(self, tmp_path):
        """Compiler with empty source_files and loop:per_file should handle gracefully."""
        from cc_pipeline.config import PipelineConfig, PipelineStep, Module
        from cc_pipeline.compiler import PipelineCompiler

        config = PipelineConfig(
            repo=str(tmp_path),
            pipeline=[PipelineStep(
                id="gen", executor="claude-code",
                prompt="process {file}",
                loop="per_file",
            )],
            modules=[Module(
                name="auth", source_dir="src/",
                source_files=[],  # empty!
                variables={"line_threshold": 80, "branch_threshold": 70},
            )],
        )
        compiler = PipelineCompiler(config)

        # Bug: empty list → skips loop → falls to else → {file} unresolved → KeyError
        # Fix: should raise clear ValueError instead of cryptic KeyError
        with pytest.raises(ValueError, match="empty source_files"):
            compiler.compile_module("auth")


# ─── #34 P0: JSON boolean/null postcondition comparison always fails ───

class TestIssue34BooleanNullComparison:
    """Postcondition with boolean/null values should compare correctly."""

    def test_boolean_true_comparison(self):
        """$.passed == true should match JSON {"passed": true}."""
        from cc_pipeline.postcondition import _evaluate_single

        result = _evaluate_single("$.passed == true", {"passed": True})
        assert result is True, "Boolean true comparison failed"

    def test_null_comparison(self):
        """$.error == null should match JSON {"error": null}."""
        from cc_pipeline.postcondition import _evaluate_single

        result = _evaluate_single("$.error == null", {"error": None})
        assert result is True, "Null comparison failed"


# ─── #11 P0: command injection via module name ───

class TestIssue11CommandInjection:
    """Module name with shell metacharacters should be rejected."""

    def test_malicious_module_name_rejected(self, tmp_path):
        """load_config should reject module name with shell metacharacters."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "evil.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    command: "echo test"
modules:
  - name: "auth; rm -rf /"
    source_dir: src/
    source_files: [a.c]
    coverage:
      line_threshold: 80
      branch_threshold: 70
""")
        # Bug: no validation, malicious name accepted
        with pytest.raises((ValueError, RuntimeError)):
            load_config(str(config_file))

    def test_path_traversal_output_rejected(self, tmp_path):
        """load_config should reject output with path traversal."""
        from cc_pipeline.config import load_config

        config_file = tmp_path / "evil2.yaml"
        config_file.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: claude-code
    prompt: test
    output: "../../etc/passwd"
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
