"""Tests for CLI status and resume commands."""
import pytest
import json
from pathlib import Path
from unittest.mock import patch


class TestCLIStatus:
    """Test cc-pipeline status command."""

    def test_status_no_runs_found(self, tmp_path):
        """status with no runs dir → 'No runs found' + exit 0."""
        from cc_pipeline.cli import main
        with patch("cc_pipeline.cli.Path") as mock_path:
            mock_path.return_value.expanduser.return_value = tmp_path / "nonexistent"
            # Also patch the inner Path calls
            ret = main(["status"])
            # Should return 0 (graceful "no runs")
            assert ret == 0

    def test_status_lists_recent_runs(self, tmp_path):
        """status with runs → lists run directories."""
        from cc_pipeline.cli import main

        # Create fake run dirs
        base = tmp_path / "runs"
        base.mkdir()
        (base / "2026-07-01T10-00-00").mkdir()
        (base / "2026-07-01T11-00-00").mkdir()

        with patch("cc_pipeline.cli.Path") as mock_path_class:
            def fake_path(p):
                if str(p) == "~/.cc-pipeline/runs":
                    return base
                return Path(p)
            mock_path_class.side_effect = fake_path
            mock_path_class.return_value.expanduser.return_value = base

            ret = main(["status"])
            assert ret == 0

    def test_status_specific_run_id(self, tmp_path):
        """status --run-id <id> → shows module details."""
        from cc_pipeline.cli import main

        run_dir = tmp_path / "2026-07-01T10-00-00"
        run_dir.mkdir()

        # Create module transcript
        mod_dir = run_dir / "auth"
        mod_dir.mkdir()
        transcript = mod_dir / "transcript.jsonl"
        transcript.write_text(json.dumps({
            "event": "pass", "step": "generate", "attempt": 1
        }))

        with patch("cc_pipeline.cli.Path") as mock_path_class:
            def fake_path(p):
                if "~/.cc-pipeline/runs" in str(p):
                    return tmp_path
                return Path(p)
            mock_path_class.side_effect = fake_path

            ret = main(["status", "--run-id", "2026-07-01T10-00-00"])
            assert ret == 0

    def test_status_run_not_found(self, tmp_path):
        """status --run-id nonexistent → exit 1."""
        from cc_pipeline.cli import main

        with patch("cc_pipeline.cli.Path") as mock_path_class:
            mock_path_class.return_value.expanduser.return_value = tmp_path / "nonexistent"
            ret = main(["status", "--run-id", "nonexistent"])
            assert ret == 1

    def test_no_args_prints_help(self):
        """No command → print help, exit 0."""
        from cc_pipeline.cli import main
        ret = main([])
        assert ret == 0


class TestCLIResume:
    """Test cc-pipeline resume command."""

    def test_resume_all_passed_returns_zero(self, tmp_path):
        """resume when all modules passed → exit 0, nothing to do."""
        from cc_pipeline.cli import main
        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        state = {"run_id": "r", "modules": {"m": {"status": "passed"}}}
        (run_dir / "orchestrator-state.json").write_text(json.dumps(state))
        config = tmp_path / "config.yaml"
        config.write_text("repo: /tmp\nbase_branch: main\npipeline:\n  - id: x\n    executor: shell\n    prompt: echo ok\nmodules:\n  - name: m\n    spec_id: S\n    source_dir: src/\n    source_files: [a.c]\n    coverage: {line_threshold: 80, branch_threshold: 70}\n")
        ret = main(["resume", str(config), "--run-dir", str(run_dir)])
        assert ret == 0
