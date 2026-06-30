"""TDD RED: CLI entry point tests."""
import pytest
from click.testing import CliRunner  # We'll use argparse actually, not click


class TestCLIEntryPoint:
    """Test the cc-pipeline CLI basic interface."""

    def test_cli_module_importable(self):
        """cli module can be imported."""
        from cc_pipeline import cli
        assert hasattr(cli, "main")

    def test_cli_help_exits_zero(self):
        """`cc-pipeline --help` exits 0."""
        from cc_pipeline.cli import main
        import sys
        old_argv = sys.argv
        sys.argv = ["cc-pipeline", "--help"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = old_argv

    def test_cli_version_flag(self):
        """`cc-pipeline --version` prints version."""
        from cc_pipeline.cli import main
        import sys
        old_argv = sys.argv
        sys.argv = ["cc-pipeline", "--version"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        finally:
            sys.argv = old_argv
