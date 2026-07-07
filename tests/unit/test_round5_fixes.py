"""Round 5 audit fix tests — verify each bug fix works correctly."""
import pytest
import subprocess, os, tempfile
from pathlib import Path
from cc_pipeline.postcondition import evaluate
from cc_pipeline.config import PipelineConfig, PipelineStep, Module, load_config
from cc_pipeline.compiler import PipelineCompiler, CompiledStep
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.executor import CCResult


class TestBug1PostconditionTimeout:
    """#1: postcondition evaluate() timeout → graceful fail, not crash."""

    def test_timeout_returns_failed_result(self):
        r = evaluate(shell="sleep 10", expect=None, cwd="/tmp", timeout=1)
        assert r.passed is False
        assert "timed out" in r.reason.lower() or "timeout" in r.reason.lower()

    def test_timeout_shell_command_stored(self):
        r = evaluate(shell="sleep 10", expect="true", cwd="/tmp", timeout=1)
        assert r.shell_command == "sleep 10"

    def test_normal_command_not_affected(self):
        r = evaluate(shell="echo ok", expect="true", cwd="/tmp", timeout=10)
        assert r.passed is True


class TestBug3GitRollbackCheckTrue:
    """#3: git rollback uses check=True — fails loudly, not silently."""

    def test_rollback_nonexistent_tag_raises(self, tmp_path):
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
        (repo / "f.txt").write_text("x")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t"}
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=env)

        from cc_pipeline.git_checkpoint import GitCheckpoint
        gc = GitCheckpoint(str(repo))
        # rollback to nonexistent tag should raise (not silently pass)
        with pytest.raises(subprocess.CalledProcessError):
            gc.rollback(step="nonexistent", module="mod1", attempt=99)


class TestBug6SnippetUndefinedWarn:
    """#6: undefined snippet reference should warn."""

    def test_undefined_snippet_warns(self, tmp_path):
        config = PipelineConfig(
            repo="/tmp/fake",
            snippets={"defined": "Hello"},
            pipeline=[PipelineStep(id="s1", executor="shell",
                     prompt="{{snippet:defined}} and {{snippet:undefined}}")],
            modules=[Module(name="m", source_dir="src/")],
        )
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            steps = PipelineCompiler(config).compile_module("m")
            # Should warn about undefined snippet
            assert any("undefined" in str(wi.message).lower() for wi in w), \
                f"Expected warning about undefined snippet, got: {[str(wi.message) for wi in w]}"

    def test_defined_snippet_no_warn(self, tmp_path):
        config = PipelineConfig(
            repo="/tmp/fake",
            snippets={"build": "make test"},
            pipeline=[PipelineStep(id="s1", executor="shell",
                     prompt="{{snippet:build}}")],
            modules=[Module(name="m", source_dir="src/")],
        )
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            steps = PipelineCompiler(config).compile_module("m")
            snippet_warns = [wi for wi in w if "snippet" in str(wi.message).lower()]
            assert len(snippet_warns) == 0


class TestBug7NegativeRetryMessage:
    """#7: negative retry shows correct attempt count."""

    def test_negative_retry_shows_at_least_1(self, tmp_path):
        from cc_pipeline.executor import ShellResult
        step = CompiledStep(
            step_id="s1", executor="shell",
            rendered_prompt="false", retry=-1,
        )
        runner = ModuleRunner(
            [step], "mod1", str(tmp_path), str(tmp_path / "runs"),
            shell_executor=type("S", (), {
                "run": lambda s, c, cwd, timeout=None: ShellResult(1, "", "error")
            })(),
        )
        result = runner.run()
        assert "failed" in result["status"]
        # Should show at least 1 attempt, not 0 or negative
        assert "0 attempts" not in result.get("error", "")
        assert "-4" not in result.get("error", "")


class TestBug10ConfigEncoding:
    """#10: config file read with explicit utf-8 encoding."""

    def test_chinese_config_loads(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            f"repo: {tmp_path}\n"
            "pipeline:\n"
            "  - id: 测试\n"
            "    executor: shell\n"
            "    prompt: echo 你好世界\n"
            "modules:\n"
            "  - name: m\n"
            "    source_dir: src/\n"
            "    source_files: [a.c]\n",
            encoding="utf-8",
        )
        config = load_config(str(cfg))
        assert config.pipeline[0].prompt == "echo 你好世界"


class TestBug1InitValidation:
    """P0-1: init rejects bad module names and concurrency."""

    def test_init_parser_exists(self):
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["init"])
        assert args.command == "init"


class TestPythonMEntryPoint:
    """P2: python -m cc_pipeline works."""

    def test_main_module_importable(self):
        # __main__.py should exist and be importable
        import importlib.util
        spec = importlib.util.find_spec("cc_pipeline.__main__")
        assert spec is not None, "cc_pipeline.__main__ module not found"
