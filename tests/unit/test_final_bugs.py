"""TDD tests for 5 bugs from blackbox-test-final.md."""
import pytest, subprocess, os, json, io, sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from cc_pipeline.compiler import CompiledStep, PipelineCompiler
from cc_pipeline.config import PipelineConfig, PipelineStep, Module
from cc_pipeline.runner import ModuleRunner


ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "a.c").write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ Bug 3/4: {prev_output_path} for shell executor ═══

class TestPrevOutputShell:
    """{prev_output_path} and {current_output_path} work in shell executor."""

    def test_prev_output_path_replaced_in_shell(self, git_repo):
        """Shell executor: {prev_output_path} → file path."""
        step = CompiledStep(step_id="P2", executor="shell",
                            rendered_prompt="cat {prev_output_path}",
                            prev_output_path=".pipeline/P1.json",
                            output="P2.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        cmd = mock_run.call_args[0][0]
        assert ".pipeline/P1.json" in cmd  # replaced by runtime

    def test_current_output_path_replaced_in_shell(self, git_repo):
        """Shell executor: {current_output_path} → file path."""
        step = CompiledStep(step_id="P2", executor="shell",
                            rendered_prompt="test -f {current_output_path}",
                            output="P2.json")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))

        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            runner._execute_step(step)

        cmd = mock_run.call_args[0][0]
        assert ".pipeline/P2.json" in cmd


# ═══ Bug 5: expect: false crash ═══

class TestExpectFalse:
    """expect: false in YAML → Python False, not string 'false'."""

    def test_expect_false_bool_works(self):
        """YAML expect: false → bool False → treated as false."""
        from cc_pipeline.postcondition import evaluate
        result = evaluate(shell="true", expect=False, cwd="/tmp")
        # false means "commmand must fail" → true succeeds (exit 0) → should NOT pass
        assert not result.passed

    def test_expect_true_bool_works(self):
        """YAML expect: true → bool True → treated as true."""
        from cc_pipeline.postcondition import evaluate
        result = evaluate(shell="true", expect=True, cwd="/tmp")
        assert result.passed  # true succeeds → expect:true matches

    def test_expect_string_false_still_works(self):
        """String 'false' still works (backward compat)."""
        from cc_pipeline.postcondition import evaluate
        result = evaluate(shell="true", expect="false", cwd="/tmp")
        assert not result.passed


# ═══ Bug 6: prompt + prompt_file validation ═══

class TestPromptFileValidation:
    """prompt + prompt_file together → warn, not error."""

    def test_prompt_and_prompt_file_warn_only(self, tmp_path):
        """Both set → warns but config loads."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo)
        (repo / "src").mkdir()
        (repo / "a.c").write_text("int f(){}")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "i"], cwd=repo,
                       capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t",
                                                 "GIT_AUTHOR_EMAIL": "t@t.com",
                                                 "GIT_COMMITTER_NAME": "t",
                                                 "GIT_COMMITTER_EMAIL": "t@t.com"})

        config_file = tmp_path / "config.yaml"
        (tmp_path / "prompts").mkdir()
        (tmp_path / "prompts" / "gen.md").write_text("Generate tests")
        config_file.write_text(f"""
repo: {repo}
pipeline:
  - id: gen
    executor: shell
    prompt: "inline prompt"
    prompt_file: "prompts/gen.md"
modules:
  - name: mod
    source_dir: src/
""")
        from cc_pipeline.config import load_config
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config = load_config(str(config_file))
        # Should not crash — prompt+prompt_file coexistence is a warning
        assert config is not None


# ═══ Bug 7: output path sanitizer rejects .pipeline/ ═══

class TestOutputPathSanitizer:
    """output: .pipeline/gen.json should work."""

    def test_output_with_pipeline_prefix(self):
        """output: 'gen.json' is used as-is in the prompt."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do it", output="gen.json")
        runner = ModuleRunner([step], "auth", "/tmp/x", "/tmp/runs")
        result = runner._inject_context("do it", step)
        assert ".pipeline/gen.json" in result

    def test_output_strips_path_traversal(self):
        """output: '../../../etc/passwd' sanitized."""
        step = CompiledStep(step_id="gen", executor="claude-code",
                            rendered_prompt="do it", output="../../../etc/passwd")
        runner = ModuleRunner([step], "auth", "/tmp/x", "/tmp/runs")
        result = runner._inject_context("do it", step)
        assert ".." not in result.replace("...", "")
        # Should contain sanitized filename, not path traversal
        assert "etcpasswd" in result


# ═══ Bug 9: step.modules in _KNOWN_STEP_FIELDS ═══

class TestStepModulesKnown:
    """step.modules should not warn 'Unknown field'."""

    def test_modules_in_known_fields(self):
        """modules: [A, B] → no Unknown field warning."""
        from cc_pipeline.config import load_config
        config_file = Path("/tmp/test_config.yaml")
        config_file.write_text("""
repo: /tmp/fake
pipeline:
  - id: gen
    executor: shell
    prompt: "echo ok"
    modules: [auth, crypto]
modules:
  - name: auth
    source_dir: src/
  - name: crypto
    source_dir: src/
""")
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                load_config(str(config_file))
            except ValueError:
                pass
        unknown = [x for x in w if "Unknown field" in str(x.message) and "modules" in str(x.message)]
        assert len(unknown) == 0, f"Should not warn about modules: {unknown}"
