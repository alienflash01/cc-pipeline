"""TDD: Daemon mode + stop command — PID file, signal handling, graceful shutdown."""
import signal as sig_module
from pathlib import Path
from unittest.mock import patch


class TestDaemonMode:
    """CLI --daemon flag creates PID file."""

    def test_daemon_parser_accepts_flag(self):
        """--daemon flag is recognized by argparse."""
        from cc_pipeline.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["run", "config.yaml", "--daemon"])
        assert args.daemon is True

    def test_daemon_defaults_to_false(self):
        """Without --daemon, the flag is False."""
        from cc_pipeline.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["run", "config.yaml"])
        assert args.daemon is False


class TestStopCommand:
    """cc-pipeline stop reads PID file and sends SIGTERM to daemon."""

    def test_stop_parser_accepts_run_dir(self, tmp_path):
        """stop --run-dir is required."""
        from cc_pipeline.cli import _build_parser

        parser = _build_parser()
        run_dir = str(tmp_path)
        args = parser.parse_args(["stop", "--run-dir", run_dir])
        assert args.run_dir == run_dir

    def test_stop_sends_sigterm(self, tmp_path):
        """stop reads PID from file → sends SIGTERM → waits."""
        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        pid_file = run_dir / "cc-pipeline.pid"
        pid_file.write_text("12345")

        with patch("os.kill") as mock_kill, \
             patch("time.sleep"):
            # First os.kill sends SIGTERM, second check returns ProcessLookupError (stopped)
            mock_kill.side_effect = [None, ProcessLookupError()]
            from cc_pipeline.cli import main
            ret = main(["stop", "--run-dir", str(run_dir)])

            assert mock_kill.call_count >= 2  # SIGTERM + alive check
            assert ret == 0

    def test_stop_force_sends_sigkill(self, tmp_path):
        """stop --force sends SIGKILL."""
        run_dir = tmp_path / "runs"
        run_dir.mkdir()
        pid_file = run_dir / "cc-pipeline.pid"
        pid_file.write_text("54321")

        with patch("os.kill") as mock_kill, \
             patch("time.sleep"):
            mock_kill.side_effect = [None, ProcessLookupError()]
            from cc_pipeline.cli import main
            ret = main(["stop", "--run-dir", str(run_dir), "--force"])

            assert mock_kill.call_count >= 2
            assert ret == 0

    def test_stop_no_pid_file(self, tmp_path, capsys):
        """stop with no PID file prints error."""
        run_dir = tmp_path / "runs"
        run_dir.mkdir()

        from cc_pipeline.cli import main
        ret = main(["stop", "--run-dir", str(run_dir)])

        captured = capsys.readouterr()
        assert "PID file" in captured.out or "not found" in captured.out
        assert ret != 0
