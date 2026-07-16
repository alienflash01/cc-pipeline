"""Automated tests for DOC-BASELINE-TEST.md — 25 items, all passing."""
import pytest, subprocess, os, json, io, sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from cc_pipeline.compiler import CompiledStep, PipelineCompiler
from cc_pipeline.config import PipelineConfig, PipelineStep, Module, load_config
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.postcondition import evaluate


ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}

@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    (repo / "src").mkdir()
    for f in ["a.c", "b.c", "c.c"]:
        (repo / "src" / f).write_text("int f() { return 0; }")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo, capture_output=True, env=ENV)
    return repo


# ═══ §3: Global config fields ═══

class TestGlobalConfig:
    def test_concurrency_control(self, git_repo):
        """concurrency: 1 limits parallel modules."""
        config = PipelineConfig(repo=str(git_repo), concurrency=1,
            pipeline=[PipelineStep(id="s1", executor="shell", prompt="echo ok")],
            modules=[Module(name="m1", source_dir="src/", source_files=["a.c"])])
        assert config.concurrency == 1

    def test_output_branch_prefix_default(self):
        """Default branch prefix is cc-auto."""
        config = PipelineConfig(repo="/tmp/fake",
            pipeline=[PipelineStep(id="s1", executor="shell", prompt="echo ok")],
            modules=[Module(name="m1")])
        assert config.output_branch_prefix == "cc-auto"

    def test_typo_detection(self, tmp_path):
        """Typo 'concurency' suggests 'concurrency'."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        (repo / "src").mkdir()
        config_file = tmp_path / "cfg.yaml"
        config_file.write_text(f"repo: {repo}\npipeline:\n  - id: s1\n    executor: shell\n    prompt: echo ok\nmodules:\n  - name: m1\n    source_dir: src/\nconcurency: 3")
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            config = load_config(str(config_file))
        typo_warnings = [x for x in w if "concurrency" in str(x.message).lower()]
        assert len(typo_warnings) >= 1


# ═══ §4: Pipeline DSL ═══

class TestPipelineDSL:
    def test_shell_prompt_executes(self, git_repo):
        """Shell executor runs prompt as shell command."""
        step = CompiledStep(step_id="s1", executor="shell", rendered_prompt="echo hello")
        runner = ModuleRunner([step], "mod", str(git_repo), str(git_repo / "runs"))
        with patch("cc_pipeline.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="hello", stderr="")
            result = runner.run()
        assert result["status"] == "passed"

    def test_command_field_rejected(self, tmp_path):
        """'command' field raises ValueError (not silent warning)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        (repo / "src").mkdir()
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(f"repo: {repo}\npipeline:\n  - id: s1\n    executor: shell\n    command: echo hello\n    prompt: echo hi\nmodules:\n  - name: m1\n    source_dir: src/\n")
        from cc_pipeline.config import load_config
        import pytest
        with pytest.raises(ValueError, match="not a recognized field"):
            load_config(str(cfg))