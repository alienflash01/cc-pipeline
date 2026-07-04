"""TDD: transcript command — format transcript.jsonl for human reading."""
import pytest
import json
from pathlib import Path
from cc_pipeline.cli import main


def _make_transcript(run_dir, module_name):
    """Create a sample transcript.jsonl."""
    mod_dir = Path(run_dir) / module_name
    mod_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"ts": "2026-07-04T12:00:01", "event": "step_start", "step": "scaffold", "attempt": 1, "loop_file": None},
        {"ts": "2026-07-04T12:00:01", "event": "cc_prompt", "step": "scaffold", "prompt": "You are a test engineer.\nCreate scaffold for {module}."},
        {"ts": "2026-07-04T12:00:30", "event": "pass", "step": "scaffold", "attempt": 1, "info": {"reason": "No postcondition"}},
        {"ts": "2026-07-04T12:00:31", "event": "step_start", "step": "generate", "attempt": 1, "loop_file": "auth_login.c"},
        {"ts": "2026-07-04T12:00:31", "event": "cc_prompt", "step": "generate", "prompt": "Generate tests for auth_login.c\nUsing CHECK macro.\nSource: src/auth/auth_login.c"},
        {"ts": "2026-07-04T12:01:15", "event": "pass", "step": "generate", "attempt": 1, "info": {"reason": "contains('passed'): True"}},
        {"ts": "2026-07-04T12:01:16", "event": "fail", "step": "evaluate", "attempt": 1, "reason": "score=45 < 60"},
        {"ts": "2026-07-04T12:01:17", "event": "on_failure_jump", "step": "evaluate", "attempt": 0, "info": {"from": "evaluate", "to": "generate", "jump": 1}},
    ]
    with open(mod_dir / "transcript.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


class TestTranscriptCommand:
    """transcript command formats transcript.jsonl for human reading."""

    def test_parser_accepts_transcript(self):
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["transcript", "--run-dir", "/tmp/x"])
        assert args.command == "transcript"
        assert args.run_dir == "/tmp/x"

    def test_parser_module_optional(self):
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["transcript", "--run-dir", "/tmp/x"])
        assert args.module is None

    def test_parser_module_specified(self):
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["transcript", "--run-dir", "/tmp/x", "--module", "auth"])
        assert args.module == "auth"

    def test_output_contains_all_events(self, tmp_path, capsys):
        _make_transcript(str(tmp_path), "auth")
        ret = main(["transcript", "--run-dir", str(tmp_path), "--module", "auth"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "scaffold" in out
        assert "generate" in out
        assert "evaluate" in out

    def test_output_shows_full_prompt(self, tmp_path, capsys):
        _make_transcript(str(tmp_path), "auth")
        ret = main(["transcript", "--run-dir", str(tmp_path), "--module", "auth"])
        assert ret == 0
        out = capsys.readouterr().out
        # Full prompt text should be visible
        assert "You are a test engineer" in out
        assert "Using CHECK macro" in out
        assert "src/auth/auth_login.c" in out

    def test_output_shows_failure_reasons(self, tmp_path, capsys):
        _make_transcript(str(tmp_path), "auth")
        ret = main(["transcript", "--run-dir", str(tmp_path), "--module", "auth"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "score=45" in out
        assert "JUMP BACK" in out or "jump" in out.lower()

    def test_all_modules_when_no_module_specified(self, tmp_path, capsys):
        _make_transcript(str(tmp_path), "auth")
        _make_transcript(str(tmp_path), "crypto")
        ret = main(["transcript", "--run-dir", str(tmp_path)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "auth" in out
        assert "crypto" in out

    def test_missing_transcript_returns_1(self, tmp_path, capsys):
        ret = main(["transcript", "--run-dir", str(tmp_path), "--module", "nonexistent"])
        assert ret == 1
