"""TDD: Logger tests."""
import pytest
import json
from pathlib import Path


class TestLogger:
    """Test pipeline logging."""

    def test_logger_importable(self):
        from cc_pipeline.logger import Logger
        assert Logger is not None

    def test_logger_creates_transcript_file(self, tmp_path):
        """Logger creates a JSONL transcript file."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="auth")
        assert (tmp_path / "auth" / "transcript.jsonl").exists()

    def test_logger_writes_jsonl_entry(self, tmp_path):
        """log_event writes a valid JSON line."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.event("step_start", step="scaffold", attempt=1)
        log_file = tmp_path / "auth" / "transcript.jsonl"
        lines = log_file.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        assert entry["event"] == "step_start"
        assert entry["step"] == "scaffold"

    def test_logger_includes_timestamp(self, tmp_path):
        """Each entry has an ISO timestamp."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.event("test_event")
        log_file = tmp_path / "auth" / "transcript.jsonl"
        entry = json.loads(log_file.read_text().strip().split("\n")[-1])
        assert "ts" in entry

    def test_logger_includes_module_name(self, tmp_path):
        """Each entry includes the module name."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="payment")
        log.event("something")
        entry = json.loads((tmp_path / "payment" / "transcript.jsonl").read_text().strip().split("\n")[-1])
        assert entry["module"] == "payment"

    def test_logger_appends_multiple_entries(self, tmp_path):
        """Multiple events are appended, not overwritten."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.event("first")
        log.event("second")
        log.event("third")
        lines = (tmp_path / "auth" / "transcript.jsonl").read_text().strip().split("\n")
        assert len(lines) == 3

    def test_logger_log_pass_event(self, tmp_path):
        """log_pass writes a pass event."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.log_pass(step="generate", attempt=1, info={"files": 3})
        entry = json.loads((tmp_path / "auth" / "transcript.jsonl").read_text().strip().split("\n")[-1])
        assert entry["event"] == "pass"
        assert entry["step"] == "generate"

    def test_logger_log_fail_event(self, tmp_path):
        """log_fail writes a fail event."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.log_fail(step="evaluate", attempt=2, reason="score too low")
        entry = json.loads((tmp_path / "auth" / "transcript.jsonl").read_text().strip().split("\n")[-1])
        assert entry["event"] == "fail"
        assert "score too low" in entry["reason"]

    def test_logger_log_retry_event(self, tmp_path):
        """log_retry writes a retry event."""
        from cc_pipeline.logger import Logger
        log = Logger(run_dir=str(tmp_path), module_name="auth")
        log.log_retry(step="generate", attempt=2, reason="coverage 65 < 80")
        entry = json.loads((tmp_path / "auth" / "transcript.jsonl").read_text().strip().split("\n")[-1])
        assert entry["event"] == "retry"
