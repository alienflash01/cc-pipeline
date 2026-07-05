"""TDD: cc-pipeline check — environment + config sanity check.

Strict TDD: tests written against the spec before pinning behavior. The
``check`` command runs always-on environment probes and, when ``--config`` is
given, validates the config against that environment. Output is one line per
check + a ``Summary: N/M checks passed`` tally.
"""
import pytest
from unittest.mock import patch, MagicMock


def _args(config=None):
    return type("Args", (), {"config": config})()


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


class TestCheckParser:
    def test_check_parser_exists(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["check"])
        assert args.command == "check"

    def test_check_parser_config_default_none(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["check"])
        assert args.config is None

    def test_check_parser_accepts_config(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["check", "--config", "config.yaml"])
        assert args.config == "config.yaml"


# ---------------------------------------------------------------------------
# Environment probes (no --config)
# ---------------------------------------------------------------------------


class TestCheckEnvironment:
    """Without --config, the environment probes run and a summary is printed."""

    def test_check_prints_header(self, capsys):
        from cc_pipeline.cli import _cmd_check

        with patch("cc_pipeline.cli.shutil.which", return_value="/usr/bin/git"), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=10 * 1024 ** 3)
            _cmd_check(_args())
        out = capsys.readouterr().out
        assert "🔍" in out
        assert "cc-pipeline Environment Check" in out

    def test_check_reports_python_git_claude_disk(self, capsys):
        from cc_pipeline.cli import _cmd_check

        def which(name):
            return {"git": "/usr/bin/git", "claude": "/usr/bin/claude"}.get(name)

        with patch("cc_pipeline.cli.shutil.which", side_effect=which), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            _cmd_check(_args())
        out = capsys.readouterr().out
        assert "Python" in out
        assert "Git" in out
        assert "Claude Code CLI" in out
        assert "Git user.name" in out
        assert "Disk space" in out

    def test_check_shows_emoji_status_per_line(self, capsys):
        from cc_pipeline.cli import _cmd_check

        def which(name):
            return {"git": "/usr/bin/git", "claude": "/usr/bin/claude"}.get(name)

        with patch("cc_pipeline.cli.shutil.which", side_effect=which), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            _cmd_check(_args())
        out = capsys.readouterr().out
        # healthy environment → at least one ✅ per passing check
        assert "✅" in out

    def test_check_marks_missing_claude_as_failed(self, capsys):
        from cc_pipeline.cli import _cmd_check

        def which(name):
            # claude missing → that probe should show ❌
            return {"git": "/usr/bin/git"}.get(name)

        with patch("cc_pipeline.cli.shutil.which", side_effect=which), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            _cmd_check(_args())
        out = capsys.readouterr().out
        assert "❌" in out

    def test_check_prints_summary_tally(self, capsys):
        from cc_pipeline.cli import _cmd_check

        def which(name):
            return {"git": "/usr/bin/git", "claude": "/usr/bin/claude"}.get(name)

        with patch("cc_pipeline.cli.shutil.which", side_effect=which), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            _cmd_check(_args())
        out = capsys.readouterr().out
        assert "Summary:" in out
        # 5 environment probes, all passing → "5/5"
        assert "5/5 checks passed" in out

    def test_check_returns_zero(self):
        from cc_pipeline.cli import _cmd_check

        with patch("cc_pipeline.cli.shutil.which", return_value="/usr/bin/git"), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            ret = _cmd_check(_args())
        assert ret == 0


# ---------------------------------------------------------------------------
# Config probes (--config)
# ---------------------------------------------------------------------------


class TestCheckConfig:
    def _write_config(self, tmp_path, repo, with_prompt_files=True):
        """Write a minimal valid config + its prompt file under tmp_path."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir(exist_ok=True)
        if with_prompt_files:
            (prompts_dir / "review.md").write_text("review {module}\n")
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            f"repo: {repo}\n"
            "base_branch: main\n"
            "concurrency: 1\n"
            "pipeline:\n"
            "  - id: review\n"
            "    executor: claude-code\n"
            "    prompt_file: prompts/review.md\n"
            "modules:\n"
            "  - name: auth\n"
            "    source_dir: src/\n"
        )
        return cfg

    def test_check_config_load_valid(self, tmp_path, capsys):
        from cc_pipeline.cli import _cmd_check

        cfg = self._write_config(tmp_path, str(tmp_path))
        with patch("cc_pipeline.cli.shutil.which", return_value="/usr/bin/git"), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            _cmd_check(_args(str(cfg)))
        out = capsys.readouterr().out
        assert "Config load" in out
        assert "✅" in out  # config is valid

    def test_check_config_load_invalid(self, tmp_path, capsys):
        from cc_pipeline.cli import _cmd_check

        bad = tmp_path / "config.yaml"
        bad.write_text("repo: .\npipeline: []\nmodules: []\n")  # empty pipeline/modules
        with patch("cc_pipeline.cli.shutil.which", return_value="/usr/bin/git"), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            _cmd_check(_args(str(bad)))
        out = capsys.readouterr().out
        assert "Config load" in out
        assert "❌" in out

    def test_check_reports_repo_branch_promptfiles(self, tmp_path, capsys):
        from cc_pipeline.cli import _cmd_check

        cfg = self._write_config(tmp_path, str(tmp_path))
        with patch("cc_pipeline.cli.shutil.which", return_value="/usr/bin/git"), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            _cmd_check(_args(str(cfg)))
        out = capsys.readouterr().out
        assert "Repo exists" in out
        assert "base_branch exists" in out
        assert "prompt_files present" in out
        assert "Dry-run preview" in out


# ---------------------------------------------------------------------------
# Dispatch through main()
# ---------------------------------------------------------------------------


class TestCheckDispatch:
    def test_main_check_dispatches(self, capsys):
        from cc_pipeline.cli import main

        with patch("cc_pipeline.cli.shutil.which", return_value="/usr/bin/git"), \
             patch("cc_pipeline.cli.subprocess.run") as mock_run, \
             patch("cc_pipeline.cli.shutil.disk_usage") as mock_du:
            mock_run.return_value = MagicMock(stdout="tester\n", returncode=0)
            mock_du.return_value = MagicMock(free=50 * 1024 ** 3)
            ret = main(["check"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "🔍" in out
        assert "Summary:" in out
