"""HTML report + Mermaid DAG visualization for cc-pipeline.

Pure-Python HTML generation (no Jinja2, no external templating). Produces a
single self-contained HTML document:
  - inline <style> CSS
  - a Mermaid graph (depends_on edges) loaded via CDN <script>
  - native <details>/<summary> for collapsible CC prompts

The report is assembled from:
  state        - contents of orchestrator-state.json ({run_id, modules, ...})
  transcripts  - {module_name: [event dicts parsed from transcript.jsonl]}
  pipeline_def - [{id, executor, depends_on, loop}, ...] from config.yaml
"""
from __future__ import annotations

import html as _html
from datetime import datetime, timezone


# Bookkeeping pseudo-steps that are not real pipeline stages — mirrored from
# the Markdown report so the two reports stay consistent.
_PSEUDO_STEPS = {"resume_skip", "pr_creation"}

# Mermaid.js loaded from CDN so the HTML stays a single portable file.
_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"

# CSS inlined into every report.
_CSS = """
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       margin: 0 auto; max-width: 960px; padding: 24px; color: #1f2328; line-height: 1.5; }
h1 { border-bottom: 2px solid #d0d7de; padding-bottom: 8px; margin-bottom: 4px; }
h2 { margin-top: 32px; border-bottom: 1px solid #eaecef; padding-bottom: 6px; }
h3 { margin-top: 24px; }
.meta { color: #57606a; font-size: 14px; margin-top: 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { border: 1px solid #d0d7de; padding: 6px 12px; text-align: left; }
th { background: #f6f8fa; }
.pass { color: #1a7f37; font-weight: 600; }
.fail { color: #cf222e; font-weight: 600; }
.mermaid { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px;
           padding: 16px; text-align: center; margin: 12px 0; }
details { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px;
          padding: 8px 12px; margin: 8px 0; }
summary { cursor: pointer; font-weight: 600; }
pre { white-space: pre-wrap; word-wrap: break-word; background: #fff;
      border: 1px solid #d0d7de; border-radius: 4px; padding: 10px;
      margin-top: 8px; font-size: 13px; }
"""


# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------


def build_dag_mermaid(pipeline_steps: list) -> str:
    """Render pipeline steps as a Mermaid graph (left-to-right).

    Each step is a dict with keys: id, executor, depends_on, loop.
    - ``depends_on`` becomes a solid arrow ``dep --> id``.
    - A step with ``loop == "per_file"`` is annotated ``[per_file]`` via a
      node label definition.

    The basic form (linear chain, no loops) matches::

        graph LR
            scaffold --> generate
            generate --> evaluate
    """
    steps = list(pipeline_steps or [])
    lines = ["graph LR"]

    referenced: set[str] = set()
    edges: list[str] = []
    for step in steps:
        sid = step.get("id", "")
        dep = step.get("depends_on")
        if dep:
            edges.append(f"    {dep} --> {sid}")
            referenced.add(dep)
            referenced.add(sid)

    # Node label definitions for per_file steps so the annotation renders.
    per_file: list[str] = []
    for step in steps:
        sid = step.get("id", "")
        if step.get("loop") == "per_file":
            per_file.append(f'    {sid}["{sid} [per_file]"]')
            referenced.add(sid)

    # Any step not touched by an edge or annotation still needs to appear.
    bare: list[str] = []
    for step in steps:
        sid = step.get("id", "")
        if sid and sid not in referenced:
            bare.append(f"    {sid}")

    lines.extend(edges)
    lines.extend(per_file)
    lines.extend(bare)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Transcript summarization
# ---------------------------------------------------------------------------


def _summarize_steps(events: list) -> tuple[dict, list[str]]:
    """Reduce a module's transcript events to per-step outcomes.

    Returns (steps, step_order) where:
      steps      = {step_id: {status, attempt, reason}} (status in PASS/FAIL/?)
      step_order = step ids in order of first appearance (pseudo-steps excluded)
    Mirrors the Markdown report's transcript parsing.
    """
    steps: dict = {}
    order: list[str] = []
    for entry in events or []:
        step = entry.get("step")
        if not step or step in _PSEUDO_STEPS:
            continue
        if step not in order:
            order.append(step)
        if step not in steps:
            steps[step] = {"status": "?", "attempt": entry.get("attempt", 1), "reason": ""}

        event = entry.get("event")
        if event == "step_start":
            steps[step]["attempt"] = entry.get("attempt", steps[step]["attempt"])
        elif event == "pass":
            info = entry.get("info") or {}
            reason = info.get("reason", "") if isinstance(info, dict) else ""
            steps[step] = {
                "status": "PASS",
                "attempt": entry.get("attempt", 1),
                "reason": reason,
            }
        elif event == "fail":
            steps[step] = {
                "status": "FAIL",
                "attempt": entry.get("attempt", 1),
                "reason": entry.get("reason", ""),
            }
        elif event == "retry":
            steps[step]["attempt"] = entry.get("attempt", steps[step]["attempt"])
            steps[step]["reason"] = entry.get("reason", steps[step]["reason"])
    return steps, order


def _cc_prompt(events: list) -> str:
    """Collect CC prompt text from cc_prompt events, one block per step."""
    blocks: list[str] = []
    for entry in events or []:
        if entry.get("event") == "cc_prompt":
            step = entry.get("step", "?")
            prompt = entry.get("prompt", "")
            blocks.append(f"[{step}]\n{prompt}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------


def _esc(text) -> str:
    """HTML-escape arbitrary text for safe inline insertion."""
    return _html.escape(str(text) if text is not None else "")


def _status_label(module_status: str) -> tuple[str, str]:
    """Return (icon, label) for a module's top-level status."""
    if module_status == "passed":
        return "✅", "PASSED"
    return "❌", "FAILED"


def _summary_table(modules: dict) -> str:
    total = len(modules)
    passed = sum(1 for m in modules.values() if m.get("status") == "passed")
    failed = total - passed
    rate = f"{round(passed / total * 100, 1):g}" if total else "0"
    return (
        "<table>\n"
        "  <tr><th>Metric</th><th>Value</th></tr>\n"
        f"  <tr><td>Modules</td><td>{total}</td></tr>\n"
        f"  <tr><td>Passed</td><td>{passed}</td></tr>\n"
        f"  <tr><td>Failed</td><td>{failed}</td></tr>\n"
        f"  <tr><td>Success Rate</td><td>{rate}%</td></tr>\n"
        "</table>\n"
    )


def _module_detail(name: str, module_state: dict, events: list) -> str:
    icon, label = _status_label(module_state.get("status", ""))
    steps, order = _summarize_steps(events)

    rows: list[str] = []
    for step_id in order:
        s = steps[step_id]
        cls = "pass" if s["status"] == "PASS" else ("fail" if s["status"] == "FAIL" else "")
        status_cell = f'<span class="{cls}">{s["status"]}</span>' if cls else s["status"]
        rows.append(
            "    <tr>"
            f"<td>{_esc(step_id)}</td>"
            f"<td>{status_cell}</td>"
            f"<td>{_esc(s['attempt'])}</td>"
            f"<td>{_esc(s['reason'])}</td>"
            "</tr>"
        )

    body = [f'<h3>{_esc(name)} {icon} {label}</h3>']
    if rows:
        body.append(
            "<table>\n"
            "  <tr><th>Step</th><th>Status</th><th>Attempt</th><th>Reason</th></tr>\n"
            + "\n".join(rows) + "\n"
            "</table>"
        )

    prompt = _cc_prompt(events)
    if prompt:
        body.append(
            "<details><summary>CC Prompt</summary>"
            f"<pre>{_esc(prompt)}</pre>"
            "</details>"
        )
    return "\n".join(body) + "\n"


def build_html_report(
    state: dict,
    transcripts: dict,
    pipeline_def: list,
    run_id: str,
) -> str:
    """Assemble a complete, self-contained HTML report string.

    Args:
        state: orchestrator-state.json contents ({run_id, modules, ...}).
        transcripts: {module_name: [event dicts]}.
        pipeline_def: [{id, executor, depends_on, loop}, ...].
        run_id: the run identifier for the header.
    """
    modules = state.get("modules", {}) if state else {}
    transcripts = transcripts or {}
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append(f"<title>cc-pipeline Run Report - {_esc(run_id)}</title>")
    parts.append(f"<style>{_CSS}</style>")
    parts.append("</head>")
    parts.append("<body>")

    parts.append("<h1>cc-pipeline Run Report</h1>")
    parts.append(f'<p class="meta">Run ID: {_esc(run_id)} | Generated: {timestamp}</p>')

    parts.append("<h2>Summary</h2>")
    parts.append(_summary_table(modules))

    parts.append("<h2>Pipeline Flow</h2>")
    parts.append('<div class="mermaid">')
    parts.append(build_dag_mermaid(pipeline_def))
    parts.append("</div>")
    parts.append(f'<script src="{_MERMAID_CDN}"></script>')
    parts.append("<script>mermaid.initialize({startOnLoad:true});</script>")

    parts.append("<h2>Module Details</h2>")
    for name in sorted(modules):
        events = transcripts.get(name, [])
        parts.append(_module_detail(name, modules[name], events))

    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"
