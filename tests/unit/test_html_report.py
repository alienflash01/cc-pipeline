"""TDD: tests for HTML report + Mermaid DAG visualization.

Covers:
  - build_dag_mermaid: render pipeline depends_on as a Mermaid graph
  - build_html_report: assemble a self-contained HTML report
  - CLI `report --format html` integration

See the HTML Report + Pipeline DAG brief.
"""
import json
from pathlib import Path

from cc_pipeline.report_html import build_dag_mermaid, build_html_report


def _ts(s: str) -> str:
    return f"2026-07-01T10:00:{s}+00:00"


# ---------------------------------------------------------------------------
# build_dag_mermaid
# ---------------------------------------------------------------------------


class TestBuildDagMermaid:
    def test_starts_with_graph_lr(self):
        out = build_dag_mermaid([])
        assert out.startswith("graph LR")

    def test_empty_pipeline_only_header(self):
        out = build_dag_mermaid([])
        # header line, nothing else of substance
        assert out.strip() == "graph LR"

    def test_linear_chain_edges(self):
        """depends_on produces solid arrows in declaration order."""
        steps = [
            {"id": "scaffold", "executor": "claude-code", "depends_on": None, "loop": None},
            {"id": "generate", "executor": "claude-code", "depends_on": "scaffold", "loop": None},
            {"id": "evaluate", "executor": "claude-code", "depends_on": "generate", "loop": None},
        ]
        out = build_dag_mermaid(steps)
        assert "scaffold --> generate" in out
        assert "generate --> evaluate" in out

    def test_no_spurious_arrow_when_no_depends_on(self):
        steps = [{"id": "solo", "executor": "shell", "depends_on": None, "loop": None}]
        out = build_dag_mermaid(steps)
        assert "-->" not in out
        assert "solo" in out  # still referenced so it renders as a node

    def test_per_file_step_annotated(self):
        """A loop: per_file step is annotated [per_file]."""
        steps = [
            {"id": "scaffold", "executor": "claude-code", "depends_on": None, "loop": None},
            {"id": "generate", "executor": "claude-code", "depends_on": "scaffold", "loop": "per_file"},
        ]
        out = build_dag_mermaid(steps)
        assert "scaffold --> generate" in out
        assert "[per_file]" in out

    def test_non_per_file_step_not_annotated(self):
        steps = [
            {"id": "scaffold", "executor": "claude-code", "depends_on": None, "loop": None},
            {"id": "generate", "executor": "claude-code", "depends_on": "scaffold", "loop": None},
        ]
        out = build_dag_mermaid(steps)
        assert "[per_file]" not in out

    def test_handles_missing_optional_keys(self):
        """A step dict may omit depends_on / loop entirely."""
        out = build_dag_mermaid([{"id": "solo", "executor": "shell"}])
        assert "solo" in out


# ---------------------------------------------------------------------------
# build_html_report
# ---------------------------------------------------------------------------


def _state(modules: dict, run_id: str = "run-1") -> dict:
    return {"run_id": run_id, "saved_at": _ts("00"), "modules": modules}


def _pipeline():
    return [
        {"id": "scaffold", "executor": "claude-code", "depends_on": None, "loop": None},
        {"id": "generate", "executor": "claude-code", "depends_on": "scaffold", "loop": None},
    ]


class TestBuildHtmlReportStructure:
    def test_returns_html_document(self):
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "<html" in html.lower()
        assert "</html>" in html.lower()

    def test_title_and_run_id(self):
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-xyz",
        )
        assert "cc-pipeline Run Report" in html
        assert "run-xyz" in html

    def test_generated_timestamp_present(self):
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "Generated:" in html

    def test_has_inline_style_block(self):
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "<style>" in html
        assert "</style>" in html


class TestBuildHtmlReportSummary:
    def test_summary_table_counts_and_rate(self):
        html = build_html_report(
            state=_state({
                "auth": {"status": "passed"},
                "payment": {"status": "failed"},
            }),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "Summary" in html
        assert ">2<" in html  # total modules
        assert ">1<" in html  # passed = 1, failed = 1
        assert "50%" in html  # success rate

    def test_summary_all_passed(self):
        html = build_html_report(
            state=_state({
                "auth": {"status": "passed"},
                "payment": {"status": "passed"},
            }),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "100%" in html


class TestBuildHtmlReportDag:
    def test_mermaid_div_embedded(self):
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert 'class="mermaid"' in html
        assert "graph LR" in html
        assert "scaffold --> generate" in html

    def test_mermaid_cdn_script(self):
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "<script" in html
        assert "mermaid" in html.lower()
        # CDN reference (no local file dependency)
        assert "src=" in html
        assert "cdn" in html.lower() or "jsdelivr" in html.lower() or "unpkg" in html.lower()


class TestBuildHtmlReportModuleDetails:
    def test_module_heading_with_status(self):
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "auth" in html
        assert "PASSED" in html

    def test_failed_module_marker(self):
        html = build_html_report(
            state=_state({"payment": {"status": "failed"}}),
            transcripts={},
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "payment" in html
        assert "FAILED" in html

    def test_step_table_with_pass_and_fail(self):
        transcripts = {
            "auth": [
                {"ts": _ts("00"), "module": "auth", "event": "step_start",
                 "step": "scaffold", "attempt": 1},
                {"ts": _ts("01"), "module": "auth", "event": "pass",
                 "step": "scaffold", "attempt": 1, "info": {"reason": "ok"}},
                {"ts": _ts("02"), "module": "auth", "event": "step_start",
                 "step": "generate", "attempt": 1},
                {"ts": _ts("03"), "module": "auth", "event": "fail",
                 "step": "generate", "attempt": 2, "reason": "coverage 50 < 80"},
            ],
        }
        html = build_html_report(
            state=_state({"auth": {"status": "failed"}}),
            transcripts=transcripts,
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        # step table headers
        assert "Step" in html and "Status" in html
        assert "Attempt" in html and "Reason" in html
        assert "scaffold" in html
        assert "generate" in html
        assert "PASS" in html
        assert "FAIL" in html
        assert "coverage 50 &lt; 80" in html  # HTML-escaped

    def test_pseudo_steps_excluded_from_table(self):
        transcripts = {
            "auth": [
                {"ts": _ts("00"), "module": "auth", "event": "pass",
                 "step": "resume_skip", "attempt": 0, "info": {"steps": ["scaffold"]}},
                {"ts": _ts("01"), "module": "auth", "event": "pass",
                 "step": "generate", "attempt": 1, "info": {}},
            ],
        }
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts=transcripts,
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        # within the auth detail section, resume_skip must not appear as a table row
        details = html.split("Module Details", 1)[1]
        assert "resume_skip" not in details
        assert "generate" in details

    def test_collapsible_cc_prompt(self):
        transcripts = {
            "auth": [
                {"ts": _ts("00"), "module": "auth", "event": "cc_prompt",
                 "step": "generate", "attempt": 1, "prompt": "write tests for auth"},
                {"ts": _ts("01"), "module": "auth", "event": "pass",
                 "step": "generate", "attempt": 1, "info": {}},
            ],
        }
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts=transcripts,
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "<details>" in html
        assert "<summary>" in html
        assert "CC Prompt" in html
        assert "<pre>" in html
        assert "write tests for auth" in html

    def test_no_cc_prompt_section_when_absent(self):
        transcripts = {
            "auth": [
                {"ts": _ts("00"), "module": "auth", "event": "pass",
                 "step": "generate", "attempt": 1, "info": {}},
            ],
        }
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts=transcripts,
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "CC Prompt" not in html

    def test_html_escapes_prompt_content(self):
        """A prompt containing HTML special chars must be escaped, not injected."""
        transcripts = {
            "auth": [
                {"ts": _ts("00"), "module": "auth", "event": "cc_prompt",
                 "step": "generate", "attempt": 1, "prompt": "<script>alert(1)</script>"},
            ],
        }
        html = build_html_report(
            state=_state({"auth": {"status": "passed"}}),
            transcripts=transcripts,
            pipeline_def=_pipeline(),
            run_id="run-1",
        )
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# CLI: report --format html
# ---------------------------------------------------------------------------


def _make_run_dir(tmp_path: Path, modules_state: dict, transcripts=None, run_id="run-1") -> Path:
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    state = {"run_id": run_id, "saved_at": _ts("00"), "modules": modules_state}
    (run_dir / "orchestrator-state.json").write_text(json.dumps(state))
    for mod_name, events in (transcripts or {}).items():
        mod_dir = run_dir / mod_name
        mod_dir.mkdir()
        (mod_dir / "transcript.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events)
        )
    return run_dir


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "repo: /tmp/demo\n"
        "base_branch: master\n"
        "concurrency: 1\n"
        "pipeline:\n"
        "  - id: scaffold\n"
        "    executor: claude-code\n"
        "    prompt: hi\n"
        "  - id: generate\n"
        "    executor: claude-code\n"
        "    depends_on: scaffold\n"
        "    loop: per_file\n"
        "    prompt: hi\n"
        "modules:\n"
        "  - name: auth\n"
        "    source_dir: src/\n"
    )
    return cfg


class TestCliReportHtml:
    def test_format_html_writes_report_html(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(tmp_path, {"auth": {"status": "passed"}})
        cfg = _write_config(tmp_path)
        ret = main([
            "report", "--run-dir", str(run_dir),
            "--config", str(cfg), "--format", "html",
        ])
        assert ret == 0
        report = run_dir / "report.html"
        assert report.exists()
        content = report.read_text()
        assert "<html" in content.lower()
        assert "cc-pipeline Run Report" in content
        # DAG pulled from config.yaml
        assert "scaffold --> generate" in content
        assert "[per_file]" in content

    def test_format_html_prints_written_path(self, tmp_path, capsys):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(tmp_path, {"auth": {"status": "passed"}})
        cfg = _write_config(tmp_path)
        main([
            "report", "--run-dir", str(run_dir),
            "--config", str(cfg), "--format", "html",
        ])
        out = capsys.readouterr().out
        assert "report.html" in out
        assert "written" in out.lower()

    def test_format_html_without_config_still_works(self, tmp_path):
        """No config given -> no DAG edges, but report still generates."""
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(tmp_path, {"auth": {"status": "passed"}})
        ret = main(["report", "--run-dir", str(run_dir), "--format", "html"])
        assert ret == 0
        content = (run_dir / "report.html").read_text()
        assert "<html" in content.lower()

    def test_format_html_includes_cc_prompt_from_transcript(self, tmp_path):
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(
            tmp_path,
            {"auth": {"status": "passed"}},
            transcripts={"auth": [
                {"ts": _ts("00"), "module": "auth", "event": "cc_prompt",
                 "step": "generate", "attempt": 1, "prompt": "hello cc"},
                {"ts": _ts("01"), "module": "auth", "event": "pass",
                 "step": "generate", "attempt": 1, "info": {}},
            ]},
        )
        ret = main(["report", "--run-dir", str(run_dir), "--format", "html"])
        assert ret == 0
        content = (run_dir / "report.html").read_text()
        assert "CC Prompt" in content
        assert "hello cc" in content

    def test_default_format_is_markdown(self, tmp_path):
        """Omitting --format keeps the existing Markdown behavior."""
        from cc_pipeline.cli import main

        run_dir = _make_run_dir(tmp_path, {"auth": {"status": "passed"}})
        main(["report", "--run-dir", str(run_dir)])
        assert (run_dir / "report.md").exists()
        assert not (run_dir / "report.html").exists()

    def test_invalid_format_rejected(self, tmp_path):
        from cc_pipeline.cli import main
        import pytest

        run_dir = _make_run_dir(tmp_path, {"auth": {"status": "passed"}})
        with pytest.raises(SystemExit):
            main(["report", "--run-dir", str(run_dir), "--format", "pdf"])
