"""CLI entry point for cc-pipeline."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from cc_pipeline import __version__


def main(argv: list[str] | None = None) -> int:
    """cc-pipeline CLI main entry."""
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

    # resume subcommand
    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted run")
    resume_parser.add_argument("config", help="Config YAML file path")
    resume_parser.add_argument("--run-dir", required=True, help="Run directory from previous run")
    resume_parser.add_argument("--concurrency", type=int, default=None, help="Module parallelism")
    resume_parser.add_argument("--model", default="glm-4.6", help="Claude model to use")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show pipeline status")
    status_parser.add_argument("--run-id", default=None, help="Run ID")

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

    return 0


def _cmd_run(args) -> int:
    """Execute the run command."""
    from cc_pipeline.config import load_config
    from cc_pipeline.orchestrator import Orchestrator

    # Load config
    config = load_config(args.config)

    # Override concurrency if specified
    if args.concurrency is not None:
        config.concurrency = args.concurrency

    # Filter to single module if specified
    if args.module is not None:
        config.modules = [m for m in config.modules if m.name == args.module]
        if not config.modules:
            print(f"Error: module '{args.module}' not found in config")
            return 1

    # Generate run ID
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = args.run_dir or f"~/.cc-pipeline/runs/{run_id}"
    run_dir = str(Path(run_dir).expanduser())

    print(f"🌙 cc-pipeline {__version__}")
    print(f"   run_id={run_id}  concurrency={config.concurrency}  model={args.model}")
    print(f"   modules={[m.name for m in config.modules]}")
    print()

    # Run orchestrator
    orch = Orchestrator(
        config=config,
        run_dir=run_dir,
        cc_model=args.model,
    )
    orch.run_id = run_id
    results = orch.run()

    # Print summary
    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    for r in results:
        status_icon = "✓" if r["status"] == "passed" else "✗"
        print(f"  {status_icon} {r['module']:20s}  {r['status']}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed  (run_id: {run_id})")

    return 0 if failed == 0 else 1


def _cmd_resume(args) -> int:
    """Resume an interrupted run — skip passed modules, re-run failed/error."""
    import json as _json
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
    if args.run_id:
        run_dir = Path(f"~/.cc-pipeline/runs/{args.run_id}").expanduser()
    else:
        base = Path("~/.cc-pipeline/runs").expanduser()
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

    # Read transcripts
    print(f"Run: {args.run_id}\n")
    modules_dir = run_dir
    if modules_dir.is_dir():
        for mod_dir in sorted(modules_dir.iterdir()):
            if not mod_dir.is_dir():
                continue
            transcript = mod_dir / "transcript.jsonl"
            if transcript.exists():
                lines = transcript.read_text().strip().split("\n")
                if lines and lines[0]:
                    import json
                    last = json.loads(lines[-1])
                    print(f"  {mod_dir.name:20s}  last_event={last.get('event', '?')}  step={last.get('step', '?')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
