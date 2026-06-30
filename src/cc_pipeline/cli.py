"""CLI entry point for cc-pipeline."""
import argparse
import sys
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
    
    # resume subcommand
    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted run")
    resume_parser.add_argument("--run-id", required=True, help="Run ID to resume")
    
    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show pipeline status")
    status_parser.add_argument("--run-id", default=None, help="Run ID")
    
    args = parser.parse_args(argv)
    
    if args.command is None:
        parser.print_help()
        return 0
    
    if args.command == "run":
        print(f"[TODO] run pipeline from {args.config}")
        return 0
    
    if args.command == "resume":
        print(f"[TODO] resume run {args.run_id}")
        return 0
    
    if args.command == "status":
        print("[TODO] show status")
        return 0
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
