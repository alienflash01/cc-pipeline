"""CLI entry point for cc-pipeline."""
import argparse
import json as _json
import os
import signal
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
    # Re-raise KeyboardInterrupt for non-daemon mode
    if not _shutdown_requested:
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
    run_parser.add_argument("--model", default="glm-4.6", help="Claude model to use")
    run_parser.add_argument("--run-dir", default=None, help="Run output directory")
    run_parser.add_argument("--daemon", action="store_true", default=False,
                            help="Run as daemon (fork to background)")

    # resume subcommand
    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted run")
    resume_parser.add_argument("config", help="Config YAML file path")
    resume_parser.add_argument("--run-dir", required=True, help="Run directory from previous run")
    resume_parser.add_argument("--concurrency", type=int, default=None, help="Module parallelism")
    resume_parser.add_argument("--model", default="glm-4.6", help="Claude model to use")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show pipeline status")
    status_parser.add_argument("--run-id", default=None, help="Show specific run details")
    status_parser.add_argument("--run-dir", default=None, help="Run directory (default: ~/.cc-pipeline/runs)")

    # stop subcommand
    stop_parser = subparsers.add_parser("stop", help="Stop a running daemon")
    stop_parser.add_argument("--run-dir", required=True, help="Run directory containing cc-pipeline.pid")
    stop_parser.add_argument("--force", action="store_true", default=False,
                             help="Force stop (SIGKILL instead of SIGTERM)")

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

    return 0


def _cmd_run(args) -> int:
    """Execute the run command, optionally as daemon."""
    from cc_pipeline.config import load_config
    from cc_pipeline.orchestrator import Orchestrator

    # Set up run directory
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
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

    # Build config
    config = load_config(args.config)
    if args.concurrency is not None:
        config.concurrency = args.concurrency

    # Filter to single module if specified
    if args.module:
        config.modules = [m for m in config.modules if m.name == args.module]
        if not config.modules:
            print(f"Module '{args.module}' not found in config", file=sys.stderr)
            return 1

    # Run orchestrator (checks _shutdown_requested between modules)
    orch = Orchestrator(
        config=config,
        run_dir=str(run_dir),
        cc_model=args.model,
    )

    results = orch.run()

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

    # Run orchestrator for remaining modules
    orch = Orchestrator(
        config=config,
        run_dir=str(run_dir),
        cc_model=args.model,
    )

    # Filter config to only remaining modules
    config.modules = [m for m in config.modules if m.name in modules_to_run]

    results = orch.run()

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
        state = json.loads(state_file.read_text())
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
        os.waitpid(pid, 0)
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


if __name__ == "__main__":
    sys.exit(main())
