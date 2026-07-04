"""TDD: uninstall command — remove cc-pipeline from system."""
import pytest
import subprocess
import os
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestUninstallCommand:
    """cc-pipeline uninstall removes installed package + scripts."""

    def test_uninstall_parser_exists(self):
        """CLI parser should accept 'uninstall' subcommand."""
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["uninstall"])
        assert args.command == "uninstall"

    def test_uninstall_parser_accepts_yes_flag(self):
        """uninstall --yes skips confirmation."""
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["uninstall", "--yes"])
        assert args.yes is True

    def test_uninstall_parser_yes_defaults_false(self):
        """uninstall without --yes defaults to requiring confirmation."""
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["uninstall"])
        assert args.yes is False

    def test_uninstall_removes_pip_package(self, tmp_path):
        """uninstall calls pip uninstall for cc-pipeline."""
        from cc_pipeline.cli import _cmd_uninstall

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ret = _cmd_uninstall(type("Args", (), {"yes": True})())
            assert ret == 0
            # Should call pip uninstall
            calls = [c[0][0] for c in mock_run.call_args_list]
            pip_call = [c for c in calls if "pip" in " ".join(c) and "uninstall" in " ".join(c)]
            assert len(pip_call) > 0, f"Expected pip uninstall call, got: {calls}"

    def test_uninstall_cleans_worktree_temp(self, tmp_path):
        """uninstall removes /tmp/cc-pipeline-worktrees if exists."""
        from cc_pipeline.cli import _cmd_uninstall

        temp_wt = Path("/tmp/cc-pipeline-worktrees")
        created = False
        if not temp_wt.exists():
            temp_wt.mkdir(parents=True)
            (temp_wt / "dummy").write_text("x")
            created = True

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _cmd_uninstall(type("Args", (), {"yes": True})())

        if created:
            assert not temp_wt.exists() or not any(temp_wt.iterdir()), \
                "Temp worktree dir should be cleaned"

    def test_uninstall_returns_0_on_success(self):
        """uninstall returns exit code 0 on success."""
        from cc_pipeline.cli import _cmd_uninstall
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            ret = _cmd_uninstall(type("Args", (), {"yes": True})())
            assert ret == 0
