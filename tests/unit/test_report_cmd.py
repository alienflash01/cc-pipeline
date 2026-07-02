"""TDD: tests for the `cc-pipeline report` subcommand.

Generates a Markdown run report from orchestrator-state.json + per-module
transcripts, prints to stdout and writes run_dir/report.md.
See feature 1 of the implementation brief.
"""
import json
from pathlib import Path


def _ts(s: str) -> str:
    return f"2026-07-01T10:00:{s}+00:00"


def _make_run_dir(
    tmp_path: Path,
    modules_state: dict,
    transcripts: dict | None = None,
    run_id: str = "run-1",
) -> Path:
    """Build a fake run dir with orchestrator-state.json + transcripts."""
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    state = {
        "run_id": run_id,
        "saved_at": _ts("00"),
        "modules": modules_state,
    }
    (run_dir / "orchestrator-state.json").write_text(json.dumps(state))
    for mod_name, events in (transcripts or {}).items():
        mod_dir = run_dir / mod_name
        mod_dir.mkdir()
        (mod_dir / "transcript.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events)
        )
    return run_dir


class TestReportBasics:
    def test_report_returns_zero(self, tmp_path, capsys):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "passed"}},
            transcripts={"auth": [
                {"ts": _ts("00"), "module": "auth", "event": "step_start",
                 "step": "scaffold", "attempt": 1},
                {"ts": _ts("05"), "module": "auth", "event": "pass",
                 "step": "scaffold", "attempt": 1, "info": {"reason": "ok"}},
            ]},
        )
        assert main(["report", "--run-dir", str(run_dir)]) == 0

    def test_report_writes_report_md(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "passed"}},
            transcripts={"auth": [
                {"ts": _ts("00"), "module": "auth", "event": "pass",
                 "step": "scaffold", "attempt": 1, "info": {}},
            ]},
        )
        main(["report", "--run-dir", str(run_dir)])

        report = run_dir / "report.md"
        assert report.exists()
        content = report.read_text()
        assert "# Pipeline Run Report" in content
        assert "**Run ID:** run-1" in content
        assert "**Generated:**" in content

    def test_report_prints_to_stdout(self, tmp_path, capsys):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "passed"}},
        )
        main(["report", "--run-dir", str(run_dir)])
        out = capsys.readouterr().out
        assert "# Pipeline Run Report" in out


class TestReportSummary:
    def test_summary_counts_and_rate(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={
                "auth": {"status": "passed"},
                "payment": {"status": "failed"},
            },
        )
        main(["report", "--run-dir", str(run_dir)])
        content = (run_dir / "report.md").read_text()
        assert "| Modules | 2 |" in content
        assert "| Passed | 1 |" in content
        assert "| Failed | 1 |" in content
        assert "50%" in content  # 1/2 success rate

    def test_summary_all_passed(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={
                "auth": {"status": "passed"},
                "payment": {"status": "passed"},
            },
        )
        main(["report", "--run-dir", str(run_dir)])
        content = (run_dir / "report.md").read_text()
        assert "| Passed | 2 |" in content
        assert "| Failed | 0 |" in content
        assert "100%" in content


class TestReportModuleDetails:
    def test_module_details_table_with_pass_and_fail(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "failed"}},
            transcripts={"auth": [
                {"ts": _ts("00"), "module": "auth", "event": "step_start",
                 "step": "scaffold", "attempt": 1},
                {"ts": _ts("01"), "module": "auth", "event": "pass",
                 "step": "scaffold", "attempt": 1, "info": {"reason": "files created"}},
                {"ts": _ts("02"), "module": "auth", "event": "step_start",
                 "step": "generate", "attempt": 1},
                {"ts": _ts("03"), "module": "auth", "event": "retry",
                 "step": "generate", "attempt": 1, "reason": "coverage 50 < 80"},
                {"ts": _ts("04"), "module": "auth", "event": "fail",
                 "step": "generate", "attempt": 2, "reason": "coverage 50 < 80"},
            ]},
        )
        main(["report", "--run-dir", str(run_dir)])
        content = (run_dir / "report.md").read_text()

        assert "### auth" in content
        assert "| Step | Status | Attempt | Reason |" in content
        # scaffold passed on attempt 1
        assert "| scaffold | PASS | 1 |" in content
        # generate failed on attempt 2 with the coverage reason
        assert "| generate | FAIL | 2 |" in content
        assert "coverage 50 < 80" in content

    def test_duration_computed_from_timestamps(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "passed"}},
            transcripts={"auth": [
                {"ts": _ts("00"), "module": "auth", "event": "step_start",
                 "step": "scaffold", "attempt": 1},
                {"ts": _ts("10"), "module": "auth", "event": "pass",
                 "step": "scaffold", "attempt": 1, "info": {}},
            ]},
        )
        main(["report", "--run-dir", str(run_dir)])
        content = (run_dir / "report.md").read_text()
        assert "**Duration:**" in content
        assert "10.0s" in content

    def test_pseudo_steps_excluded_from_table(self, tmp_path):
        """resume_skip / pr_creation are bookkeeping, not pipeline steps."""
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "passed"}},
            transcripts={"auth": [
                {"ts": _ts("00"), "module": "auth", "event": "pass",
                 "step": "resume_skip", "attempt": 0, "info": {"steps": ["scaffold"]}},
                {"ts": _ts("01"), "module": "auth", "event": "pass",
                 "step": "generate", "attempt": 1, "info": {}},
            ]},
        )
        main(["report", "--run-dir", str(run_dir)])
        content = (run_dir / "report.md").read_text()
        # the step table header + body: resume_skip must not appear as a row
        details = content.split("### auth")[1].split("**Duration:**")[0]
        assert "resume_skip" not in details
        assert "| generate | PASS" in details


class TestReportFailedModules:
    def test_failed_modules_section_present(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "failed"}},
            transcripts={"auth": [
                {"ts": _ts("00"), "module": "auth", "event": "fail",
                 "step": "generate", "attempt": 2, "reason": "coverage too low"},
            ]},
        )
        main(["report", "--run-dir", str(run_dir)])
        content = (run_dir / "report.md").read_text()
        assert "## Failed Modules" in content
        assert "### auth" in content
        assert "Last event: fail" in content
        assert "coverage too low" in content

    def test_no_failed_section_when_all_passed(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "passed"}},
        )
        main(["report", "--run-dir", str(run_dir)])
        content = (run_dir / "report.md").read_text()
        assert "## Failed Modules" not in content


class TestReportRobustness:
    def test_corrupt_state_returns_nonzero(self, tmp_path, capsys):
        from cc_pipeline.cli import main

        run_dir = tmp_path / "run-corrupt"
        run_dir.mkdir()
        (run_dir / "orchestrator-state.json").write_text("{ this is :: not valid json")
        ret = main(["report", "--run-dir", str(run_dir)])
        assert ret != 0
        # graceful: no report written on corrupt state
        assert not (run_dir / "report.md").exists()

    def test_missing_state_returns_nonzero(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = tmp_path / "run-nostate"
        run_dir.mkdir()
        assert main(["report", "--run-dir", str(run_dir)]) != 0

    def test_module_without_transcript_does_not_crash(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            modules_state={"auth": {"status": "passed"}},  # no transcript dir
        )
        assert main(["report", "--run-dir", str(run_dir)]) == 0
        assert (run_dir / "report.md").exists()
