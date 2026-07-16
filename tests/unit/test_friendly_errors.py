"""TDD: user-friendly error messages instead of raw tracebacks."""
import pytest
from unittest.mock import patch
from cc_pipeline.cli import main


class TestFriendlyErrors:
    """Config errors should print helpful messages, not Python tracebacks."""

    def test_config_file_not_found(self, capsys):
        """Missing config file → friendly message, exit 1."""
        ret = main(["run", "/tmp/nonexistent-config-xyz.yaml"])
        assert ret == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err.lower() or "not found" in captured.out.lower()
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out

    def test_config_missing_repo(self, tmp_path, capsys):
        """Config without repo → friendly message."""
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("pipeline: []\nmodules: []\n")
        ret = main(["run", str(cfg)])
        assert ret == 1
        captured = capsys.readouterr()
        assert "repo" in captured.err.lower() or "repo" in captured.out.lower()
        assert "Traceback" not in captured.err

    def test_prompt_file_not_found(self, tmp_path, capsys):
        """prompt_file pointing to nonexistent file → friendly message."""
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: gen
    executor: claude-code
    prompt_file: /tmp/nonexistent-prompt.md
modules:
  - name: m
    source_dir: src/
    source_files: [a.c]
""")
        ret = main(["run", str(cfg)])
        assert ret == 1
        captured = capsys.readouterr()
        assert "prompt_file" in captured.err.lower() or "prompt_file" in captured.out.lower()
        assert "Traceback" not in captured.err

    def test_missing_modules(self, tmp_path, capsys):
        """Config without modules → friendly message."""
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(f"""
repo: {tmp_path}
pipeline:
  - id: x
    executor: shell
    prompt: "echo ok"
""")
        ret = main(["run", str(cfg)])
        assert ret == 1
        captured = capsys.readouterr()
        assert "module" in captured.err.lower() or "module" in captured.out.lower()

    def test_no_traceback_on_any_config_error(self, tmp_path, capsys):
        """Any config error should never show a Python traceback."""
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("invalid: yaml: content: [")
        ret = main(["run", str(cfg)])
        assert ret == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out
