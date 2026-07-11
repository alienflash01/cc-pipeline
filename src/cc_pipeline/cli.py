"""CLI entry point for cc-pipeline."""
import argparse
import json as _json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cc_pipeline import __version__

# Global for signal handler to set
_shutdown_requested = False


def _kill_cc_subprocesses() -> None:
    """Best-effort kill of lingering Claude Code headless (`claude -p`) children.

    CC is launched with ``start_new_session=True`` (see CCExecutor), so each CC
    child runs in its own session/process group and escapes cc-pipeline's group.
    That means Ctrl+C delivered to cc-pipeline never reaches the CC children —
    they keep running in the background, burning API budget, even after
    cc-pipeline exits. There is no shared process-group handle, so we match the
    CC command line by pattern via ``pkill -f``.

    Linux-specific, but cc-pipeline only targets Linux. Best-effort: any failure
    (no ``pkill``, non-zero exit when nothing matched) is swallowed so the signal
    handler can never crash.
    """
    try:
        subprocess.run(
            ["pkill", "-f", r"claude.*-p"],
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        # Swallow — cleanup must never raise from inside the signal handler.
        pass


def _signal_handler(signum, frame):
    """Graceful shutdown: set flag, kill CC children, then raise for SIGINT."""
    global _shutdown_requested
    _shutdown_requested = True
    _kill_cc_subprocesses()
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
    run_parser.add_argument("--verbose", "-v", action="count", default=0,
                            help="Verbose (-v: steps, -vv: +prompts/CC output)")
    run_parser.add_argument("--dry-run", action="store_true", default=False,
                            help="Preview pipeline without executing CC")

    # resume subcommand
    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted run")
    resume_parser.add_argument("config", help="Config YAML file path")
    resume_parser.add_argument("--run-dir", required=True, help="Run directory from previous run")
    resume_parser.add_argument("--concurrency", type=int, default=None, help="Module parallelism")
    resume_parser.add_argument("--model", default=None, help="Claude model (default: CC's default)")
    resume_parser.add_argument("--verbose", "-v", action="count", default=0,
                               help="Verbose (-v: steps, -vv: +prompts/CC output)")
    resume_parser.add_argument("--dry-run", action="store_true", default=False,
                               help="Preview pipeline without executing CC")

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

    # clean subcommand — remove worktrees and branches
    clean_parser = subparsers.add_parser("clean", help="Remove cc-pipeline worktrees and branches")
    clean_parser.add_argument("--repo", default=None, help="Git repo path (default: from cwd)")
    clean_parser.add_argument("--all", action="store_true", default=False, help="Clean ALL cc-auto worktrees/branches")

    # init subcommand — interactive config generator
    init_parser = subparsers.add_parser("init", help="Generate a starter config interactively")
    init_parser.add_argument("--template", default=None, help="Config template (not yet supported)")
    init_parser.add_argument("--output-dir", default=".", help="Where to write generated files (default: .)")

    # check subcommand — environment + config sanity check
    check_parser = subparsers.add_parser("check", help="Check environment and (optionally) a config")
    check_parser.add_argument("--config", default=None, help="Config YAML to validate against the environment")

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
    if args.command == "clean":
        return _cmd_clean(args)
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "check":
        return _cmd_check(args)

    return 0


def _preflight_check(config, args) -> bool:
    """Pre-run environment checks. Warns on issues, never blocks the run.

    Advisories only — detecting a problem prints a WARNING to stderr but
    does not stop execution (returns True always). Checks:
      1. Claude Code CLI is installed (``which claude``)
      2. repo directory exists
      3. repo is a git repository (``.git`` present)
      4. base_branch exists in repo (``git rev-parse``)
      5. worktree_root's parent directory exists (if worktree_root configured)
    """
    warnings: list[str] = []

    # 1. Claude Code CLI installed
    if shutil.which("claude") is None:
        warnings.append(
            "Claude Code CLI not found (npm i -g @anthropic-ai/claude-code)"
        )

    # 2. repo directory exists
    repo_path = Path(config.repo)
    repo_is_git = repo_path.is_dir() and (repo_path / ".git").exists()
    if not repo_path.is_dir():
        warnings.append(f"Repo directory not found: {config.repo}")
    elif not (repo_path / ".git").exists():
        # 3. repo is a git repository
        warnings.append(f"Repo is not a git repository (no .git): {config.repo}")

    # 4. base_branch exists (only meaningful on a real git repo)
    if repo_is_git:
        rev = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--verify", config.base_branch],
            capture_output=True,
        )
        if rev.returncode != 0:
            warnings.append(f"Branch {config.base_branch} not found in repo")

    # 5. worktree_root's parent directory exists (if configured)
    if config.worktree_root:
        wt_parent = Path(config.worktree_root).parent
        if not wt_parent.exists():
            warnings.append(
                f"worktree_root parent directory not found: {wt_parent}"
            )

    if warnings:
        print("⚠️  Preflight warning:", file=sys.stderr)
        for w in warnings:
            print(f"  • {w}", file=sys.stderr)

    # Always True — warnings are advisory, never block the run.
    return True


def _do_dry_run(config, config_path) -> int:
    """Preview the pipeline without executing anything.

    Compiles each module's steps and prints:
      1. The step list (per_file steps annotated)
      2. A per-module file table (string list → File column only;
         dict entries → one column per key, ``path`` shown as ``File``)
      3. Estimated CC call count (non-loop = 1/module, per_file = files/module)
      4. Global variables

    Never creates a run_dir / worktree, never invokes Claude Code.
    Returns 0 on success, 1 if compilation fails (ValueError).
    """
    from cc_pipeline.compiler import PipelineCompiler

    config_dir = str(Path(config_path).parent) if config_path else None
    compiler = PipelineCompiler(config, config_dir)

    # Compile every module up front so a compile error bails before printing.
    try:
        for module in config.modules:
            compiler.compile_module(module.name)
    except ValueError as e:
        print(f"Error: Config compilation failed: {e}", file=sys.stderr)
        return 1

    print()
    print("📊 Pipeline Preview (dry-run)")
    print("═" * 47)
    print()

    # 1. Step list (per_file annotated), from the shared pipeline definition
    step_labels = []
    for step in config.pipeline:
        label = step.id + ("(per_file)" if step.loop == "per_file" else "")
        step_labels.append(label)
    print("  Steps: " + " → ".join(step_labels))
    print()

    # 2. Per-module file tables (skipped when source_files is empty)
    for module in config.modules:
        if not module.source_files:
            continue
        _print_module_table(module)

    # 3. Estimated CC calls
    _print_estimate(config)
    print()

    # 4. Global variables
    _print_variables(config)
    print()

    print("  ✅ Config valid. Run without --dry-run to execute.")
    return 0


def _print_module_table(module) -> None:
    """Print ``Module: <name> (<N> files)`` followed by the file table."""
    print(f"  Module: {module.name} ({len(module.source_files)} files)")
    columns, rows = _table_columns_rows(module.source_files)
    for line in _render_table(columns, rows):
        print("  " + line)
    print()


def _table_columns_rows(source_files) -> tuple[list[str], list[dict]]:
    """Build table columns + row dicts from source_files.

    - Plain string list → single ``File`` column.
    - Dict entries → one column per key (union across entries, insertion order),
      with ``path`` rendered as the ``File`` column.
    """
    has_dict = any(isinstance(e, dict) for e in source_files)
    if not has_dict:
        return ["File"], [{"File": str(sf)} for sf in source_files]

    key_order: list[str] = []
    seen: set[str] = set()
    for entry in source_files:
        if isinstance(entry, dict):
            for k in entry.keys():
                if k not in seen:
                    seen.add(k)
                    key_order.append(k)

    # path → File, placed first
    columns = (["File"] if "path" in seen else []) + [
        k for k in key_order if k != "path"
    ]

    rows: list[dict] = []
    for entry in source_files:
        if isinstance(entry, dict):
            row = {}
            for col in columns:
                row[col] = str(entry.get("path" if col == "File" else col, ""))
            rows.append(row)
        else:
            # string entry mixed among dicts: only File is populated
            rows.append({col: (str(entry) if col == "File" else "") for col in columns})
    return columns, rows


def _render_table(columns: list[str], rows: list[dict]) -> list[str]:
    """Render columns + rows as a box-drawing table (list of lines)."""
    widths = []
    for col in columns:
        w = len(col)
        for row in rows:
            w = max(w, len(str(row.get(col, ""))))
        widths.append(w)

    def border(left: str, cross: str, right: str) -> str:
        return left + cross.join("─" * (w + 2) for w in widths) + right

    def row_line(row: dict) -> str:
        cells = [" " + str(row.get(col, "")).ljust(widths[i]) + " "
                 for i, col in enumerate(columns)]
        return "│" + "│".join(cells) + "│"

    lines = [border("┌", "┬", "┐"), row_line({col: col for col in columns})]
    if rows:
        lines.append(border("├", "┼", "┤"))
        lines.extend(row_line(r) for r in rows)
    lines.append(border("└", "┴", "┘"))
    return lines


def _print_estimate(config) -> None:
    """Print the estimated CC call count + per-step breakdown.

    Non-loop step = 1 call per module; per_file step = len(source_files) per module.
    """
    n_modules = len(config.modules)
    total = 0
    pieces: list[str] = []
    for step in config.pipeline:
        if step.loop == "per_file":
            calls = sum(len(m.source_files) for m in config.modules)
        else:
            calls = n_modules
        total += calls
        pieces.append(f"{step.id}={calls}")
    print(f"  Estimated: {total} CC calls")
    print("  (" + " + ".join(pieces) + ")")


def _print_variables(config) -> None:
    """Print global config variables."""
    print("  Variables:")
    items: list[str] = [f"repo={config.repo}", f"base_branch={config.base_branch}"]
    if config.model:
        items.append(f"model={config.model}")
    if config.worktree_root:
        items.append(f"worktree_root={config.worktree_root}")
    items.append(f"concurrency={config.concurrency}")
    for item in items:
        print("    " + item)


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

    # Preflight: warn on environment issues (missing CC CLI, bad repo/branch, ...)
    _preflight_check(config, args)

    # Filter to specified modules if provided (comma-separated, before dry-run)
    if args.module:
        wanted = {m.strip() for m in args.module.split(",")}
        config.modules = [m for m in config.modules if m.name in wanted]
        missing = wanted - {m.name for m in config.modules}
        if missing:
            print(f"Module(s) not found in config: {', '.join(sorted(missing))}", file=sys.stderr)
            print(f"Available: {', '.join(m.name for m in config.modules)}", file=sys.stderr)
            return 1

    # Dry-run: preview only — no run_dir, no worktree, no CC calls
    if getattr(args, "dry_run", False):
        return _do_dry_run(config, args.config)

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
            # Cleanup stale PID file on process exit
            import atexit as _atexit
            _atexit.register(lambda: pid_file.unlink(missing_ok=True))
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
        # Flush old buffers before replacing
        sys.stdout.flush()
        sys.stderr.flush()
        new_stdout = open(log_file, "a", buffering=1)  # line-buffered
        sys.stdout = new_stdout
        sys.stderr = new_stdout
        import atexit
        atexit.register(lambda: new_stdout.close())

    # Install signal handler for graceful shutdown
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Override concurrency if specified
    if args.concurrency is not None:
        config.concurrency = args.concurrency

    # Resolve model: --model > config.model > None
    cc_model = args.model or config.model or None

    # Run orchestrator (checks _shutdown_requested between modules)
    verbose = getattr(args, "verbose", 0)
    if verbose >= 1:
        level = "steps + prompts/CC output" if verbose >= 2 else "step progress"
        print(f"  verbose mode ON (level {verbose}) — {level}")
    orch = Orchestrator(
        config=config,
        run_dir=str(run_dir),
        cc_model=cc_model,
        verbose=verbose,
        config_path=args.config,
    )
    orch.run_id = now

    # Startup banner — printed unconditionally so the terminal is never silent
    # between hitting Enter and the first module finishing (BP-3.1).
    print(f"🌙 cc-pipeline {__version__}")
    print(f"   concurrency={config.concurrency}  modules={[m.name for m in config.modules]}")
    print()

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
        if r["status"] == "passed":
            print(f"  ✓ {r['module']:20s}  {r['status']}")
        else:
            reason = r.get("error") or r["status"]
            print(f"  ✗ {r['module']:20s}  {r['status']} — {reason}")
            print(f"     💡 cc-pipeline transcript --run-dir {run_dir} --module {r['module']}")
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

    # Preflight: warn on environment issues (missing CC CLI, bad repo/branch, ...)
    _preflight_check(config, args)

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

    # Dry-run: show what resume would do
    if getattr(args, "dry_run", False):
        from cc_pipeline.state import StateManager
        sm = StateManager(str(run_dir))
        print("📊 Resume Preview (dry-run)")
        print("═══════════════════════════════════════════════\n")
        for m in config.modules:
            completed = sm.get_completed_steps(m.name)
            if completed:
                print(f"  Module: {m.name} — skip {len(completed)} completed step(s): {sorted(completed)}")
            else:
                print(f"  Module: {m.name} — no completed steps, will run all")
        print(f"\n  Modules to run: {[m.name for m in config.modules]}")
        print("  ✅ Run without --dry-run to execute resume.")
        return 0

    # Resolve model: --model > config.model > None
    cc_model = args.model or config.model or None

    verbose = getattr(args, "verbose", 0)
    if verbose >= 1:
        level = "steps + prompts/CC output" if verbose >= 2 else "step progress"
        print(f"  verbose mode ON (level {verbose}) — {level}")

    orch = Orchestrator(
        config=config,
        run_dir=str(run_dir),
        cc_model=cc_model,
        verbose=verbose,
        resume=True,
        config_path=args.config,
    )
    # Resume: reuse existing run_id from state file
    from cc_pipeline.state import StateManager
    _sm = StateManager(run_dir=str(run_dir))
    _existing_state = _sm.load()
    if _existing_state and _existing_state.get("run_id"):
        orch.run_id = _existing_state["run_id"]
    else:
        orch.run_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

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
        if r["status"] == "passed":
            extra = f"  {r.get('pr_url', '')}" if r.get("pr_url") else ""
            print(f"  ✓ {r['module']:20s}  {r['status']}{extra}")
        else:
            reason = r.get("error") or r["status"]
            print(f"  ✗ {r['module']:20s}  {r['status']} — {reason}")
            print(f"     💡 cc-pipeline transcript --run-dir {run_dir} --module {r['module']}")
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
            print("\n  💡 Getting started:")
            print("     1. cc-pipeline init          — Generate a config interactively")
            print("     2. cc-pipeline run config.yaml --dry-run   — Preview your pipeline")
            print("     3. cc-pipeline run config.yaml             — Execute")
            return 0
        runs = sorted(base.iterdir())
        if not runs:
            print("No runs found.")
            print("\n  💡 Getting started:")
            print("     1. cc-pipeline init          — Generate a config interactively")
            print("     2. cc-pipeline run config.yaml --dry-run   — Preview your pipeline")
            print("     3. cc-pipeline run config.yaml             — Execute")
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
        stopped = False
        for _ in range(30):  # wait up to 30s
            try:
                os.kill(pid, 0)  # check if still alive
            except ProcessLookupError:
                stopped = True
                break
            time.sleep(1)

        if stopped:
            # Confirmed dead — safe to report success and clean up the PID file.
            print(f"Process {pid} stopped.")
            if pid_file.exists():
                pid_file.unlink()
            return 0

        # Still alive after 30s — do NOT delete the PID file. The daemon may be
        # blocked inside a long CC call; tell the user to escalate to --force.
        print(f"Process {pid} still running after 30s.")
        print(f"  Try: cc-pipeline stop --run-dir {args.run_dir} --force")
        return 1
    except ProcessLookupError:
        print(f"Process {pid} already stopped.")
        if pid_file.exists():
            pid_file.unlink()
        return 0
    except PermissionError:
        print(f"Permission denied. Try: sudo cc-pipeline stop --run-dir {args.run_dir}")
        return 1


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


# --- init command: interactive config generator -----------------------------

# Prompt templates. Written VERBATIM to disk — their {var} are render variables
# consumed by cc-pipeline at runtime, so they must survive as literal text.
_INIT_PROMPT_SCAFFOLD = """\
你是测试工程师。
为模块 {module} 创建测试脚手架。
源码目录：{source_dir}
创建 tests/ 目录和必要的编译配置。
"""

_INIT_PROMPT_GENERATE = """\
你是测试工程师。
为文件 {source_dir}/{file} 生成单元测试。
使用 {assert_macro} 宏做断言。
先运行已有测试确认编译通过，再生成新测试。
将测试文件写到 tests/ 目录。
"""

_INIT_PROMPT_EVALUATE = """\
你是测试质量评估员。
评估模块 {module} 的测试质量。
检查：断言密度、边界覆盖、测试独立性。
将评估结果写入 .pipeline/evaluate.json（包含 score 字段，0-100）。
"""

_INIT_PROMPT_REVIEW = """\
你是代码审查专家。
审查模块 {module} 的代码。
审查重点：{review_focus}
将审查结果写入 .pipeline/review.json。
"""

_INIT_PROMPT_STEP1 = """\
为模块 {module} 执行任务。
源码目录：{source_dir}
"""

# Config templates. Collected-value placeholders ({repo_path}, {concurrency},
# {first_module}, {source_dir}, {assert_macro}) are substituted with the user's
# answers via str.replace (NOT str.format) so the prompts' literal {var} survive.
_INIT_CONFIG_UT = """\
repo: {repo_path}
base_branch: main
concurrency: {concurrency}
output_branch_prefix: cc-auto

pipeline:
  - id: scaffold
    executor: claude-code
    prompt_file: prompts/scaffold.md
    output: scaffold.json
    postcondition:
      shell: "test -d tests"

  - id: generate
    executor: claude-code
    loop: per_file
    prompt_file: prompts/generate.md
    output: generate.json
    depends_on: scaffold

  - id: evaluate
    executor: claude-code
    prompt_file: prompts/evaluate.md
    output: evaluate.json
    depends_on: generate
    on_failure: generate

modules:
  - name: {first_module}
    source_dir: {source_dir}
    source_files:
      - path: example.c
        assert_macro: {assert_macro}
"""

_INIT_CONFIG_REVIEW = """\
repo: {repo_path}
base_branch: main
concurrency: {concurrency}

pipeline:
  - id: review
    executor: claude-code
    prompt_file: prompts/review.md
    output: review.json

modules:
  - name: {first_module}
    source_dir: {source_dir}
"""

_INIT_CONFIG_CUSTOM = """\
repo: {repo_path}
base_branch: main
concurrency: {concurrency}

pipeline:
  - id: step1
    executor: claude-code
    prompt_file: prompts/step1.md

modules:
  - name: {first_module}
    source_dir: {source_dir}
"""


def _ask(prompt: str, default: str) -> str:
    """Prompt for input, returning ``default`` on an empty answer."""
    return input(prompt) or default


def _cmd_init(args) -> int:
    """Interactive config generator.

    Walks the user through a short dialog and writes a runnable ``config.yaml``
    plus a ``prompts/`` directory. Three task types are supported:
      1 = UT generation (scaffold → generate → evaluate)
      2 = code review
      3 = custom single-step
    """
    print("🧩 cc-pipeline 配置生成器")

    if getattr(args, "template", None):
        print("Note: --template not yet supported")

    repo = _ask("项目路径 repo（默认 '.'）: ", ".")
    task_type = _ask("任务类型 1=UT生成 2=代码审查 3=自定义: ", "1").strip()

    source_dir_default = "src/"
    modules_default = "auth"

    source_dir = _ask(f'source_dir（默认 "{source_dir_default}"）: ', source_dir_default)
    # Module names: validate no path separators, no special chars
    import re as _re
    while True:
        modules_str = _ask(f'模块列表逗号分隔（默认 "{modules_default}"）: ', modules_default)
        names = [n.strip() for n in modules_str.split(",")]
        bad = [n for n in names if not _re.match(r'^[a-zA-Z0-9_][a-zA-Z0-9_\-]*$', n)]
        if not bad:
            break
        print(f'  ❌ 模块名不合法: {bad}（只能用字母、数字、下划线、连字符）')
    first_module = names[0]

    # task-type-specific extras + pick the templates to write
    if task_type == "2":
        review_focus = _ask('review_focus（默认 "安全性"）: ', "安全性")
        config_template = _INIT_CONFIG_REVIEW
        prompts = {"review.md": _INIT_PROMPT_REVIEW}
        placeholders = {"review_focus": review_focus}
    elif task_type == "3":
        config_template = _INIT_CONFIG_CUSTOM
        prompts = {"step1.md": _INIT_PROMPT_STEP1}
        placeholders = {}
    else:  # "1" (UT) — also the fallback default
        assert_macro = _ask('assert_macro（默认 "CHECK"）: ', "CHECK")
        config_template = _INIT_CONFIG_UT
        prompts = {
            "scaffold.md": _INIT_PROMPT_SCAFFOLD,
            "generate.md": _INIT_PROMPT_GENERATE,
            "evaluate.md": _INIT_PROMPT_EVALUATE,
        }
        placeholders = {"assert_macro": assert_macro}

    # Concurrency: validate positive integer
    while True:
        concurrency = _ask('concurrency（默认 "5"）: ', "5")
        try:
            c = int(concurrency)
            if c > 0:
                break
            print('  ❌ concurrency 必须 > 0')
        except ValueError:
            print(f'  ❌ concurrency 必须是正整数，收到: {concurrency}')

    # Where to write. output_dir defaults to "."; resolve to an absolute path so
    # the generated files land predictably regardless of CWD.
    output_dir = Path(getattr(args, "output_dir", ".") or ".").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = output_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Substitute collected values into config.yaml (str.replace keeps {var}).
    config_text = config_template
    for key, val in {
        "repo_path": repo,
        "concurrency": concurrency,
        "first_module": first_module,
        "source_dir": source_dir,
        **placeholders,
    }.items():
        config_text = config_text.replace("{" + key + "}", str(val))

    written_files: list[str] = []
    config_path = output_dir / "config.yaml"
    config_path.write_text(config_text)
    written_files.append(str(config_path))

    # Prompt files are written verbatim (render variables preserved).
    for name, body in prompts.items():
        ppath = prompts_dir / name
        ppath.write_text(body)
        written_files.append(str(ppath))

    print("✅ 生成完成")
    print("生成的文件：")
    for f in written_files:
        print(f"  {f}")
    print("运行: cc-pipeline run config.yaml --dry-run")
    return 0


# --- check command: environment + config sanity ------------------------------


def _cmd_check(args) -> int:
    """Check the environment (and optionally a config) for readiness.

    Always runs a set of environment probes; with ``--config`` it additionally
    validates the config loads, the repo/branch exist, prompt_files are present,
    and the pipeline compiles. Output is one line per check followed by a
    ``Summary: N/M checks passed`` tally. Checks are advisory — always returns 0.
    """
    print("🔍 cc-pipeline Environment Check\n")

    passed = 0
    total = 0

    def report(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, total
        total += 1
        if ok:
            passed += 1
        mark = "✅" if ok else "❌"
        suffix = f" {detail}" if detail else ""
        print(f"  {name}: {mark}{suffix}")

    # --- Environment probes (always run) ---
    py_version = sys.version.split()[0]
    report(f"Python {py_version}", True)

    git_path = shutil.which("git")
    report("Git", git_path is not None, git_path or "not found")

    claude_path = shutil.which("claude")
    report("Claude Code CLI", claude_path is not None, claude_path or "not found")

    git_user = ""
    if git_path:
        res = subprocess.run(
            ["git", "config", "user.name"], capture_output=True, text=True
        )
        git_user = res.stdout.strip()
    report("Git user.name", bool(git_user), git_user or "not set")

    du = shutil.disk_usage(str(Path.cwd()))
    free_gb = du.free / (1024 ** 3)
    report("Disk space", free_gb > 1, f"{free_gb:.1f} GB free")

    # --- Config-specific probes (only with --config) ---
    if getattr(args, "config", None):
        config_path = args.config
        cfg = None
        try:
            from cc_pipeline.config import load_config

            cfg = load_config(config_path)
            report("Config load", True, "valid")
        except FileNotFoundError:
            report("Config load", False, "file not found")
        except ValueError as e:
            report("Config load", False, f"invalid: {e}")
        except Exception as e:  # noqa: BLE001 — surface any other failure
            report("Config load", False, f"error: {e}")

        if cfg is not None:
            repo_path = Path(cfg.repo)
            report("Repo exists", repo_path.is_dir(), cfg.repo)

            branch_ok = False
            if repo_path.is_dir() and (repo_path / ".git").exists():
                rev = subprocess.run(
                    ["git", "-C", str(repo_path), "rev-parse", "--verify",
                     cfg.base_branch],
                    capture_output=True,
                )
                branch_ok = rev.returncode == 0
            report("base_branch exists", branch_ok, cfg.base_branch)

            missing = []
            cfg_dir = Path(config_path).parent
            for step in cfg.pipeline:
                if step.prompt_file:
                    p = Path(step.prompt_file)
                    if not p.exists() and not (cfg_dir / step.prompt_file).exists():
                        missing.append(step.prompt_file)
            report("prompt_files present", not missing,
                   "all found" if not missing else f"missing: {','.join(missing)}")

            # dry-run preview = does every module compile?
            try:
                from cc_pipeline.compiler import PipelineCompiler

                compiler = PipelineCompiler(cfg, str(cfg_dir))
                for module in cfg.modules:
                    compiler.compile_module(module.name)
                report("Dry-run preview", True, "compiles")
            except ValueError as e:
                report("Dry-run preview", False, f"compile error: {e}")

    print()
    print(f"  Summary: {passed}/{total} checks passed")
    return 0


def _cmd_clean(args) -> int:
    """Remove cc-pipeline worktrees and branches."""
    repo = args.repo or os.getcwd()

    if not Path(repo, ".git").exists():
        print(f"Error: {repo} is not a git repository", file=sys.stderr)
        return 1

    # Find all cc-auto worktrees
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo, capture_output=True, text=True,
    )
    wt_count = 0
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            wt_path = line.split(" ", 1)[1]
            if "cc-auto" in wt_path or "/wt/" in wt_path or "/worktrees/" in wt_path:
                # Remove cleanly: git worktree remove first, then shutil.rmtree for residue
                subprocess.run(["git", "worktree", "remove", "--force", wt_path], cwd=repo, capture_output=True)
                import shutil as _shutil
                _shutil.rmtree(wt_path, ignore_errors=True)
                print(f"  🗑️  worktree: {wt_path}")
                wt_count += 1

    # Prune
    subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True)

    # Find and delete cc-auto branches (match cc-auto, cc-auto/mod1, etc.)
    result = subprocess.run(
        ["git", "branch", "--list", "cc-auto*", "cc-auto/*"],
        cwd=repo, capture_output=True, text=True,
    )
    br_count = 0
    for line in result.stdout.splitlines():
        branch = line.strip().lstrip("* ").strip()
        if branch and ("cc-auto" in branch):
            subprocess.run(["git", "branch", "-D", branch], cwd=repo, capture_output=True)
            print(f"  🗑️  branch: {branch}")
            br_count += 1

    # Clean up checkpoint tags
    tag_result = subprocess.run(
        ["git", "tag", "-l", "pipeline/*"],
        cwd=repo, capture_output=True, text=True,
    )
    tag_count = 0
    for line in tag_result.stdout.splitlines():
        tag = line.strip()
        if tag:
            subprocess.run(["git", "tag", "-d", tag], cwd=repo, capture_output=True)
            tag_count += 1

    total = wt_count + br_count + tag_count
    if total == 0:
        print("  ✅ Nothing to clean.")
    else:
        print(f"\n  ✅ Cleaned: {wt_count} worktrees, {br_count} branches, {tag_count} tags.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

