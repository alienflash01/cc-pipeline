"""CLI entry point for cc-pipeline."""
import argparse
import json as _json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cc_pipeline import __version__

# Global for signal handler to set
_shutdown_requested = False


def _signal_handler(signum, frame):
    """Graceful shutdown: signal handler sets a flag for the main loop."""
    global _shutdown_requested
    _shutdown_requested = True
    if signum == signal.SIGINT:
        raise KeyboardInterrupt()


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser. Separated for testability."""
    parser = argparse.ArgumentParser(
        prog="cc-pipeline",
        description="Multi-stage serial pipeline orchestrator for Claude Code",
    )
    parser.add_argument("--version", action="version", version=f"cc-pipeline {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    # run subcommand
    run_parser = subparsers.add_parser("run", help="Run a pipeline from config file")
    run_parser.add_argument("config", help="Path to modules.yaml")
    run_parser.add_argument("--concurrency", type=int, default=None, help="Module parallelism")
    run_parser.add_argument("--module", default=None, help="Only run specific module")
    run_parser.add_argument("--model", default=None, help="Claude model (default: CC's default)")
    run_parser.add_argument("--run-dir", default=None, help="Run output directory")
    run_parser.add_argument("--daemon", action="store_true", default=False,
                            help="Run as daemon (fork to background)")
    run_parser.add_argument("--verbose", "-v", action="store_true", default=False,
                            help="Print step-by-step progress to terminal")

    # resume subcommand
    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted run")
    resume_parser.add_argument("config", help="Config YAML file path")
    resume_parser.add_argument("--run-dir", required=True, help="Run directory from previous run")
    resume_parser.add_argument("--concurrency", type=int, default=None, help="Module parallelism")
    resume_parser.add_argument("--model", default=None, help="Claude model (default: CC's default)")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show pipeline status")
    status_parser.add_argument("--run-id", default=None, help="Show specific run details")
    status_parser.add_argument("--run-dir", default=None, help="Run directory (default: ~/.cc-pipeline/runs)")

    # stop subcommand
    stop_parser = subparsers.add_parser("stop", help="Stop a running daemon")
    stop_parser.add_argument("--run-dir", required=True, help="Run directory containing cc-pipeline.pid")
    stop_parser.add_argument("--force", action="store_true", default=False,
                             help="Force stop (SIGKILL instead of SIGTERM)")

    # report subcommand
    report_parser = subparsers.add_parser("report", help="Generate a run report")
    report_parser.add_argument("--run-dir", required=True, help="Run directory to report on")
    report_parser.add_argument("--format", choices=["md", "html"], default="md",
                               help="Report format (default: md)")
    report_parser.add_argument("--config", default=None,
                               help="Config YAML (needed for the DAG in --format html)")

    # uninstall subcommand
    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall cc-pipeline")
    uninstall_parser.add_argument("--yes", action="store_true", default=False,
                                  help="Skip confirmation prompt")

    # transcript subcommand
    transcript_parser = subparsers.add_parser("transcript", help="View transcript.jsonl in readable format")
    transcript_parser.add_argument("--run-dir", required=True, help="Run directory")
    transcript_parser.add_argument("--module", default=None, help="Module name (default: all modules)")

    return parser


def main(argv: list[str] | None = None) -> int:
    """cc-pipeline CLI main entry."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "run":
        return _cmd_run(args)

    if args.command == "resume":
        return _cmd_resume(args)

    if args.command == "status":
        return _cmd_status(args)
    if args.command == "stop":
        return _cmd_stop(args)
    if args.command == "report":
        return _cmd_report(args)
    if args.command == "uninstall":
        return _cmd_uninstall(args)
    if args.command == "transcript":
        return _cmd_transcript(args)

    return 0


def _cmd_run(args) -> int:
    """Execute the run command, optionally as daemon."""
    from cc_pipeline.config import load_config
    from cc_pipeline.orchestrator import Orchestrator

    # Load config with friendly error handling
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: Config file not found: {args.config}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: Config validation failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: Failed to load config: {e}", file=sys.stderr)
        return 1

    # Set up run directory
    now = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = Path(args.run_dir) if args.run_dir else Path("~/.cc-pipeline/runs").expanduser()
    run_dir.mkdir(parents=True, exist_ok=True)

    # Daemon mode: fork to background
    if args.daemon:
        pid = os.fork()
        if pid > 0:
            # Parent: write PID file, print message, exit
            pid_file = run_dir / "cc-pipeline.pid"
            pid_file.write_text(str(pid))
            print(f"Daemon started. PID: {pid}")
            print(f"PID file: {pid_file}")
            print(f"Monitor: cc-pipeline status --run-dir {run_dir}")
            print(f"Stop:    cc-pipeline stop --run-dir {run_dir}")
            return 0
        # Child: continue execution, detach from terminal
        os.setsid()
        # Redirect stdout/stderr to run_dir/daemon.log
        log_file = run_dir / "daemon.log"
        with open(log_file, "a") as f:
            f.write(f"\n=== Daemon started at {now} ===\n")
        sys.stdout = open(log_file, "a")
        sys.stderr = sys.stdout

    # Install signal handler for graceful shutdown
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Override concurrency if specified
    if args.concurrency is not None:
        config.concurrency = args.concurrency

    # Filter to single module if specified
    if args.module:
        config.modules = [m for m in config.modules if m.name == args.module]
        if not config.modules:
            print(f"Module '{args.module}' not found in config", file=sys.stderr)
            return 1

    # Resolve model: --model > config.model > None
    cc_model = args.model or config.model or None

    # Run orchestrator (checks _shutdown_requested between modules)
    verbose = getattr(args, "verbose", False)
    if verbose:
        print(f"  verbose mode ON — printing step progress")
    orch = Orchestrator(
        config=config,
        run_dir=str(run_dir),
        cc_model=cc_model,
        verbose=verbose,
    )

    try:
        results = orch.run()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Check for graceful shutdown
    global _shutdown_requested
    if _shutdown_requested:
        print("\n=== Graceful shutdown requested ===")

    # Print summary
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] != "passed")
    print()
    print("=" * 60)
    for r in results:
        icon = "✓" if r["status"] == "passed" else "✗"
        print(f"  {icon} {r['module']:20s}  {r['status']}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed  (run_id: {orch.run_id})")

    # Cleanup PID file on exit
    pid_file = run_dir / "cc-pipeline.pid"
    if pid_file.exists():
        pid_file.unlink()

    return 0 if failed == 0 else 1


def _cmd_resume(args) -> int:
    """Resume an interrupted run — skip passed modules, re-run failed/error."""
    from cc_pipeline.config import load_config
    from cc_pipeline.orchestrator import Orchestrator

    run_dir = Path(args.run_dir)
    state_file = run_dir / "orchestrator-state.json"

    # Load config
    config = load_config(args.config)
    if args.concurrency is not None:
        config.concurrency = args.concurrency

    # Read previous state
    passed_modules = set()
    if state_file.exists():
        state = _json.loads(state_file.read_text())
        for mod_name, mod_state in state.get("modules", {}).items():
            if mod_state.get("status") == "passed":
                passed_modules.add(mod_name)

    # Determine which modules to run
    modules_to_run = [m.name for m in config.modules if m.name not in passed_modules]

    if not modules_to_run:
        print("All modules already passed. Nothing to resume.")
        return 0

    if passed_modules:
        print(f"  Skipping passed: {sorted(passed_modules)}")
        print(f"  Resuming: {modules_to_run}")

    # Filter modules BEFORE Orchestrator construction
    config.modules = [m for m in config.modules if m.name in modules_to_run]

    # Resolve model: --model > config.model > None
    cc_model = args.model or config.model or None

    orch = Orchestrator(
        config=config,
        run_dir=str(run_dir),
        cc_model=cc_model,
        resume=True,
    )

    try:
        results = orch.run()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Print summary
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] != "passed")

    print()
    print("=" * 60)
    for r in results:
        status = "✓" if r["status"] == "passed" else "✗"
        extra = f"  {r.get('pr_url', '')}" if r.get("pr_url") else ""
        print(f"  {status} {r['module']:20s}  {r['status']}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed")

    return 0 if failed == 0 else 1


def _cmd_status(args) -> int:
    """Show pipeline status."""
    # Use custom run_dir if provided, otherwise default
    if args.run_dir:
        base = Path(args.run_dir)
    else:
        base = Path("~/.cc-pipeline/runs").expanduser()

    if args.run_id:
        run_dir = base / args.run_id
    else:
        if not base.exists():
            print("No runs found.")
            return 0
        runs = sorted(base.iterdir())
        if not runs:
            print("No runs found.")
            return 0
        print("Recent runs:")
        for r in runs[-10:]:
            print(f"  {r.name}")
        return 0

    if not run_dir.exists():
        print(f"Run not found: {run_dir}")
        return 1

    # Read state file for structured status
    state_file = run_dir / "orchestrator-state.json"
    if state_file.exists():
        import json
        try:
            state = json.loads(state_file.read_text())
        except (json.JSONDecodeError, KeyError):
            print("State file is corrupt or unreadable. Check transcripts manually.")
            return 1
        modules = state.get("modules", {})
        print(f"Run: {args.run_id}\n")
        passed = sum(1 for m in modules.values() if m.get("status") == "passed")
        failed = sum(1 for m in modules.values() if m.get("status") == "failed")
        error = sum(1 for m in modules.values() if m.get("status") == "error")
        running = sum(1 for m in modules.values() if m.get("status") == "running")
        print(f"  Modules: {len(modules)} (passed={passed}, failed={failed}, error={error}, running={running})")
        print()
        for mod_name, mod_state in sorted(modules.items()):
            status = mod_state.get("status", "?")
            steps = f"{mod_state.get('steps_completed', 0)}/{mod_state.get('steps_total', 0)}"
            extra = f"  {mod_state.get('pr_url', '')}" if mod_state.get("pr_url") else ""
            error_detail = f"  error={mod_state.get('error', '')[:60]}" if mod_state.get("error") else ""
            print(f"  {mod_name:20s}  {status:8s}  steps={steps}{extra}{error_detail}")
    else:
        # Fallback to reading transcripts
        print(f"Run: {args.run_id}\n")
        for mod_dir in sorted(run_dir.iterdir()):
            if not mod_dir.is_dir():
                continue
            transcript = mod_dir / "transcript.jsonl"
            if transcript.exists():
                import json
                lines = transcript.read_text().strip().split("\n")
                if lines and lines[0]:
                    last = json.loads(lines[-1])
                    print(f"  {mod_dir.name:20s}  last_event={last.get('event', '?')}  step={last.get('step', '?')}")

    return 0


def _cmd_stop(args) -> int:
    """Stop a running daemon by reading PID file and sending signal."""
    run_dir = Path(args.run_dir)
    pid_file = run_dir / "cc-pipeline.pid"

    if not pid_file.exists():
        print(f"No PID file found at {pid_file}. Is the daemon running?")
        return 1

    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        print(f"Invalid PID file at {pid_file}")
        return 1

    signal_num = signal.SIGKILL if args.force else signal.SIGTERM
    signal_name = "SIGKILL" if args.force else "SIGTERM"
    print(f"Sending {signal_name} to PID {pid}...")

    try:
        os.kill(pid, signal_num)
        # Give process time to handle signal gracefully
        import time
        for _ in range(30):  # wait up to 30s
            try:
                os.kill(pid, 0)  # check if still alive
            except ProcessLookupError:
                break
            time.sleep(1)
        print(f"Process {pid} stopped.")
    except ProcessLookupError:
        print(f"Process {pid} already stopped.")
    except PermissionError:
        print(f"Permission denied. Try: sudo cc-pipeline stop --run-dir {args.run_dir}")
        return 1
    finally:
        # Clean up PID file
        if pid_file.exists():
            pid_file.unlink()

    return 0


# --- report helpers ---------------------------------------------------------

# Bookkeeping pseudo-steps that are not real pipeline stages.
_PSEUDO_STEPS = {"resume_skip", "pr_creation"}


def _parse_iso(ts):
    """Parse an ISO-8601 timestamp into a datetime, or None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _duration_seconds(start_ts, end_ts):
    """Return elapsed seconds between two ISO timestamps, or '?' if unknown."""
    start = _parse_iso(start_ts)
    end = _parse_iso(end_ts)
    if start and end:
        return round((end - start).total_seconds(), 1)
    return "?"


def _parse_transcript(transcript_path):
    """Parse a module's transcript.jsonl into structured step outcomes.

    Returns:
        (steps, step_order, first_ts, last_ts, events)
        steps: {step_id: {status, attempt, reason}} (status in PASS/FAIL/?)
        step_order: step_ids in order of first appearance (pseudo-steps excluded)
        first_ts / last_ts: ISO timestamps bracketing the run (or None)
        events: list of all parsed entries
    """
    steps: dict = {}
    step_order: list[str] = []
    first_ts = None
    last_ts = None
    events: list = []

    if not transcript_path.exists():
        return steps, step_order, first_ts, last_ts, events

    try:
        raw = transcript_path.read_text()
    except OSError:
        return steps, step_order, first_ts, last_ts, events

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = _json.loads(line)
        except (_json.JSONDecodeError, ValueError):
            continue  # skip corrupt lines, keep the rest
        events.append(entry)

        ts = entry.get("ts")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

        step = entry.get("step")
        if not step or step in _PSEUDO_STEPS:
            continue

        if step not in step_order:
            step_order.append(step)
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

    return steps, step_order, first_ts, last_ts, events


def _build_report(run_id: str, timestamp: str, modules: dict, run_dir: Path) -> str:
    """Assemble the Markdown report body from state + transcripts."""
    total = len(modules)
    passed = sum(1 for m in modules.values() if m.get("status") == "passed")
    failed = sum(1 for m in modules.values() if m.get("status") != "passed")
    # Drop trailing .0 for whole numbers: 50.0 -> "50", 33.3 -> "33.3".
    rate = f"{round(passed / total * 100, 1):g}" if total else "0"

    lines: list[str] = []
    lines.append("# Pipeline Run Report")
    lines.append("")
    lines.append(f"**Run ID:** {run_id}")
    lines.append(f"**Generated:** {timestamp}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Modules | {total} |")
    lines.append(f"| Passed | {passed} |")
    lines.append(f"| Failed | {failed} |")
    lines.append(f"| Success Rate | {rate}% |")
    lines.append("")

    lines.append("## Module Details")
    lines.append("")
    for mod_name in sorted(modules):
        lines.append(f"### {mod_name}")
        lines.append("")
        steps, step_order, first_ts, last_ts, _events = _parse_transcript(
            run_dir / mod_name / "transcript.jsonl"
        )
        lines.append("| Step | Status | Attempt | Reason |")
        lines.append("|------|--------|---------|--------|")
        for step_id in step_order:
            s = steps[step_id]
            lines.append(f"| {step_id} | {s['status']} | {s['attempt']} | {s['reason']} |")
        lines.append("")
        duration = _duration_seconds(first_ts, last_ts)
        lines.append(f"**Duration:** {first_ts or 'N/A'} → {last_ts or 'N/A'} ({duration}s)")
        lines.append("")

    failed_mods = [n for n, m in modules.items() if m.get("status") != "passed"]
    if failed_mods:
        lines.append("## Failed Modules")
        lines.append("")
        for mod_name in sorted(failed_mods):
            lines.append(f"### {mod_name}")
            lines.append("")
            steps, _order, _first, _last, events = _parse_transcript(
                run_dir / mod_name / "transcript.jsonl"
            )
            last_event = events[-1].get("event", "?") if events else "?"

            reason = ""
            for e in reversed(events):
                if e.get("reason"):
                    reason = e["reason"]
                    break
            if not reason:
                reason = modules[mod_name].get("error", "N/A")

            stdout_summary = ""
            for e in events:
                candidate = e.get("stdout")
                if not candidate:
                    info = e.get("info")
                    if isinstance(info, dict):
                        candidate = info.get("stdout", "")
                if candidate:
                    stdout_summary = str(candidate)[:200]
                    break

            lines.append(f"- Last event: {last_event}")
            lines.append(f"- Reason: {reason}")
            lines.append(f"- CC stdout summary: {stdout_summary}")
            lines.append("")

    return "\n".join(lines)


def _cmd_report(args) -> int:
    """Generate a run report (Markdown or HTML) from state + transcripts."""
    run_dir = Path(args.run_dir)
    state_file = run_dir / "orchestrator-state.json"

    if not state_file.exists():
        print(f"State file not found: {state_file}", file=sys.stderr)
        return 1

    try:
        state = _json.loads(state_file.read_text())
    except (_json.JSONDecodeError, ValueError) as e:
        print(f"State file is corrupt or unreadable: {e}", file=sys.stderr)
        return 1

    run_id = state.get("run_id", run_dir.name)
    modules = state.get("modules", {})

    if args.format == "html":
        return _write_html_report(args, run_dir, state, run_id, modules)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    report = _build_report(run_id, timestamp, modules, run_dir)

    print(report)
    (run_dir / "report.md").write_text(report)
    return 0


def _write_html_report(args, run_dir: Path, state: dict, run_id: str, modules: dict) -> int:
    """Generate a self-contained HTML report (with DAG + collapsible CC prompts)."""
    from cc_pipeline.report_html import build_html_report

    # Collect parsed transcript events per module (reuses the robust jsonl parser).
    transcripts: dict = {}
    for mod_name in modules:
        _, _, _, _, events = _parse_transcript(run_dir / mod_name / "transcript.jsonl")
        transcripts[mod_name] = events

    # Pull the pipeline definition from config.yaml (optional — no DAG without it).
    pipeline_def: list = []
    if getattr(args, "config", None):
        from cc_pipeline.config import load_config
        cfg = load_config(args.config)
        pipeline_def = [
            {"id": s.id, "executor": s.executor, "depends_on": s.depends_on, "loop": s.loop}
            for s in cfg.pipeline
        ]

    report = build_html_report(state, transcripts, pipeline_def, run_id)
    out_path = run_dir / "report.html"
    out_path.write_text(report)
    print(f"Report written to {out_path}")
    return 0


def _cmd_uninstall(args) -> int:
    """Uninstall cc-pipeline: remove pip package + clean temp dirs."""
    import shutil

    # Confirmation
    if not args.yes:
        print("This will uninstall cc-pipeline and remove temporary files.")
        print("Run with --yes to skip this confirmation.")
        response = input("Proceed? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

    # Step 1: pip uninstall
    print("Uninstalling cc-pipeline package...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", "cc-pipeline"],
            capture_output=True,
        )
        print("  Package uninstalled.")
    except Exception as e:
        print(f"  Warning: pip uninstall failed: {e}")

    # Step 2: Clean temp worktree dir
    temp_wt = Path("/tmp/cc-pipeline-worktrees")
    if temp_wt.exists():
        print(f"Removing {temp_wt}...")
        shutil.rmtree(temp_wt, ignore_errors=True)
        print("  Cleaned.")

    # Step 3: Clean common run dirs
    default_runs = Path.home() / ".cc-pipeline" / "runs"
    if default_runs.exists():
        print(f"Removing {default_runs}...")
        shutil.rmtree(default_runs, ignore_errors=True)
        print("  Cleaned.")

    print("\ncc-pipeline uninstalled successfully.")
    print("Note: Your project repos and worktrees are NOT touched.")
    return 0


def _cmd_transcript(args) -> int:
    """Display transcript.jsonl in human-readable format with full prompts."""
    run_dir = Path(args.run_dir)

    if args.module:
        modules = [args.module]
    else:
        # Find all modules with transcript files
        modules = sorted([
            d.name for d in run_dir.iterdir()
            if d.is_dir() and (d / "transcript.jsonl").exists()
        ])
        if not modules:
            print("No transcript files found in run directory.")
            return 1

    for mod_name in modules:
        transcript_path = run_dir / mod_name / "transcript.jsonl"
        if not transcript_path.exists():
            print(f"Module '{mod_name}': transcript not found", file=sys.stderr)
            if args.module:
                return 1
            continue

        print(f"\n{'='*60}")
        print(f"  Module: {mod_name}")
        print(f"{'='*60}\n")

        events = []
        try:
            with open(transcript_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(_json.loads(line))
        except Exception as e:
            print(f"Error reading transcript: {e}")
            continue

        for d in events:
            event = d.get("event", "?")
            ts = d.get("ts", "")
            step = d.get("step", "")
            attempt = d.get("attempt", "")
            loop_file = d.get("loop_file")

            ts_short = ts[11:19] if len(ts) >= 19 else ts  # HH:MM:SS

            if event == "step_start":
                file_info = f" [{loop_file}]" if loop_file else ""
                print(f"── {ts_short} ── {step}{file_info} ── attempt {attempt} ──")

            elif event == "cc_prompt":
                prompt = d.get("info", {}).get("prompt") if isinstance(d.get("info"), dict) else d.get("prompt", "")
                if not prompt:
                    prompt = d.get("prompt", "")
                print(f"   [PROMPT]")
                for line in prompt.splitlines():
                    print(f"   │ {line}")
                print()

            elif event == "cc_result":
                rc = d.get("returncode", "")
                stdout = d.get("stdout", "") or ""
                stderr = d.get("stderr", "") or ""
                print(f"   [CC RESULT] returncode={rc}")
                if stdout:
                    print(f"   ┌─ stdout ──────────────")
                    for line in stdout.splitlines():
                        print(f"   │ {line}")
                    print(f"   └───────────────────────")
                if stderr:
                    print(f"   ┌─ stderr ──────────────")
                    for line in stderr.splitlines():
                        print(f"   │ {line}")
                    print(f"   └───────────────────────")

            elif event == "pass":
                info = d.get("info", {})
                reason = info.get("reason", "") if isinstance(info, dict) else str(info)
                print(f"   ✅ PASS — {reason}")

            elif event == "fail":
                reason = d.get("reason", "")
                print(f"   ❌ FAIL — {reason}")

            elif event == "retry":
                reason = d.get("reason", "")
                print(f"   ⚠️  RETRY (attempt {attempt}) — {reason}")

            elif event == "on_failure_jump":
                info = d.get("info", {})
                if isinstance(info, dict):
                    print(f"   ↩️  JUMP BACK: {info.get('from','')} → {info.get('to','')} (jump {info.get('jump','')})")
                else:
                    print(f"   ↩️  JUMP BACK")

            elif event == "pr_error":
                print(f"   ⚠️  PR ERROR")

            else:
                print(f"   [{event}] {d}")

        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
