"""Orchestrator — top-level parallel pipeline coordinator."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cc_pipeline.config import PipelineConfig
from cc_pipeline.compiler import PipelineCompiler
from cc_pipeline.executor import CCExecutor, ShellExecutor
from cc_pipeline.runner import ModuleRunner
from cc_pipeline.worktree import WorktreeManager


class Orchestrator:
    """Runs multiple module pipelines in parallel.

    Each module gets:
      1. An isolated git worktree
      2. A compiled pipeline (from the shared config)
      3. A ModuleRunner that executes steps serially

    Modules run in parallel via ThreadPoolExecutor.
    """

    def __init__(
        self,
        config: PipelineConfig,
        run_dir: str,
        worktree_root: str | None = None,
        cc_model: str | None = None,
        resume: bool = False,
        verbose: int = 0,
        config_path: str | None = None,
    ):
        self.config = config
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.resume = resume
        self.verbose = verbose
        self.concurrency = config.concurrency
        self.run_id = "unknown"  # set by CLI
        # Worktree root: explicit arg > config.worktree_root > run_dir/worktrees
        if worktree_root is None:
            worktree_root = config.worktree_root if config.worktree_root else str(self.run_dir / "worktrees")
        self.worktree_mgr = WorktreeManager(
            repo_path=config.repo,
            base_branch=config.base_branch,
            worktree_root=worktree_root,
            branch_prefix=config.output_branch_prefix,
        )
        self.compiler = PipelineCompiler(config, config_dir=str(Path(config_path).parent) if config_path else None)
        self.cc_model = cc_model

        # Shutdown flag (self-contained, no cli import)
        self._shutdown_requested = False
        # Reset legacy global flag to avoid test pollution
        try:
            import cc_pipeline.cli as _cli_mod
            _cli_mod._shutdown_requested = False
        except Exception:
            pass

        # Shared state manager (thread-safe)
        from cc_pipeline.state import StateManager
        self.state_mgr = StateManager(run_dir=str(self.run_dir))

    def request_shutdown(self) -> None:
        """Request a graceful shutdown of the orchestrator."""
        self._shutdown_requested = True

    def _print_module_summary(self, result: dict) -> None:
        """Print a one-line per-module summary (shown in both verbose and quiet modes).

        This is the only progress a non-verbose user sees while a run is in
        flight, so it fires regardless of ``self.verbose`` (BP-3.1). Step-level
        detail remains gated behind verbose in the runner.
        """
        name = result.get("module", "?")
        status = result.get("status", "?")
        if status == "passed":
            steps = result.get("steps_total", 0)
            module = next((m for m in self.config.modules if m.name == name), None)
            n_files = len(module.source_files) if module and module.source_files else 0
            detail = f"({steps} steps, {n_files} files)" if n_files else f"({steps} steps)"
            print(f"  ✅ {name:<8} passed  {detail}")
        else:
            reason = result.get("error") or result.get("reason") or status
            print(f"  ✗ {name:<8} failed — {reason}")

    @property
    def shutdown_requested(self) -> bool:
        """Check if shutdown has been requested (self flag or legacy cli flag)."""
        if self._shutdown_requested:
            return True
        # Backward compat: check legacy cli module flag
        try:
            import cc_pipeline.cli as cli_mod
            return getattr(cli_mod, "_shutdown_requested", False)
        except Exception:
            return False

    def run(self) -> list[dict]:
        """Run all modules in parallel.

        Returns:
            List of result dicts, one per module.
        """
        results: list[dict] = []

        # Serial mode (concurrency=1): check shutdown before each module
        if self.concurrency <= 1:
            for module in self.config.modules:
                if self.shutdown_requested:
                    results.append({
                        "status": "skipped",
                        "module": module.name,
                        "reason": "graceful shutdown requested",
                    })
                    continue
                try:
                    result = self._run_module(module.name)
                    results.append(result)
                except Exception as e:
                    results.append({
                        "status": "failed",
                        "module": module.name,
                        "error": str(e),
                    })
            return results

        # Parallel mode: submit all at once, but check shutdown before dispatch
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            future_to_module = {}
            for module in self.config.modules:
                if self.shutdown_requested:
                    results.append({
                        "status": "skipped",
                        "module": module.name,
                        "reason": "graceful shutdown requested",
                    })
                    continue
                future = pool.submit(self._run_module, module.name)
                future_to_module[future] = module.name

            for future in as_completed(future_to_module):
                module_name = future_to_module[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append({
                        "status": "failed",
                        "module": module_name,
                        "error": str(e),
                    })

        return results

    def _run_module(self, module_name: str) -> dict:
        """Run a single module's pipeline in its own worktree.

        Full exception handling:
          - Logs exceptions with traceback to transcript
          - Preserves worktree on mid-pipeline failure
          - Updates state to "error" on exception
          - No silent swallowing
        """
        import traceback as tb_module
        from cc_pipeline.logger import Logger

        from cc_pipeline.pr import PRCreator

        # Use shared state manager
        state = self.state_mgr
        # Module-level logger (created early so exceptions are logged)
        logger = Logger(run_dir=str(self.run_dir), module_name=module_name)

        wt_path = None
        try:
            # Create worktree — resume from checkpoint if available
            from_ref = None
            skip_steps = set()
            if self.resume:
                from cc_pipeline.git_checkpoint import GitCheckpoint
                gc = GitCheckpoint(repo_path=str(self.worktree_mgr.repo_path))
                skip_steps = set(gc.list_completed_steps(module=module_name))
                if skip_steps:
                    # Find the latest checkpoint across all completed steps
                    for step_id in sorted(skip_steps, reverse=True):
                        latest = gc.find_latest_checkpoint(step=step_id, module=module_name)
                        if latest:
                            from_ref = latest
                            break

            wt_path = self.worktree_mgr.create(module_name, from_ref=from_ref)

            # Find the module config
            module = None
            for m in self.config.modules:
                if m.name == module_name:
                    module = m
                    break
            if module is None:
                not_found = {
                    "status": "error",
                    "module": module_name,
                    "error": f"Module '{module_name}' not found in config.modules",
                }
                self._print_module_summary(not_found)
                return not_found
            branch = f"{self.config.output_branch_prefix}/{module_name}"

            # Save initial state
            state.update_module(
                module_name,
                status="running", worktree=wt_path, branch=branch,
            )
            state.set_run_id(getattr(self, "run_id", "unknown"))

            # Compile steps
            all_steps = self.compiler.compile_module(module_name)

            # Filter out completed steps in resume mode
            if skip_steps:
                all_steps = [s for s in all_steps if s.step_id not in skip_steps]
                logger.log_pass(step="resume_skip", attempt=0,
                                info={"steps": sorted(skip_steps), "from_ref": from_ref})

            # Create runner
            runner = ModuleRunner(
                steps=all_steps,
                module_name=module_name,
                worktree_path=wt_path,
                run_dir=str(self.run_dir),
                cc_executor=CCExecutor(model=self.cc_model),
                shell_executor=ShellExecutor(),
                verbose=self.verbose,
            )

            # Run pipeline
            result = runner.run()

            # Update state
            state.update_module(
                module_name,
                status=result["status"],
                steps_completed=result.get("steps_completed", 0),
                steps_total=result.get("steps_total", 0),
            )

            # On success: merge + PR + cleanup
            if result["status"] == "passed":
                try:
                    # PR metadata is fixed (pr_labels/pr_title_template removed).
                    labels = ["auto-generated"]
                    title = f"Pipeline for {module_name}"
                    body = f"Auto-generated by cc-pipeline for {module_name} (spec: {module.spec_id})"

                    pr_creator = PRCreator(repo_path=str(self.worktree_mgr.repo_path))
                    pr_url = pr_creator.create(
                        branch=branch,
                        title=title,
                        body=body,
                        labels=labels,
                    )
                    if pr_url:
                        state.update_module(module_name, pr_url=pr_url)
                        result["pr_url"] = pr_url
                except Exception as e:
                    logger.log_fail(step="pr_creation", attempt=0, reason=str(e))

                self.worktree_mgr.cleanup(module_name)
            else:
                self.worktree_mgr.preserve(module_name)

            self._print_module_summary(result)
            return result

        except Exception as e:
            # Log full traceback to transcript
            tb_str = tb_module.format_exc()
            logger.event("module_exception", error=str(e), traceback=tb_str)

            # Update state to error
            state.update_module(module_name, status="error", error=str(e))

            # Preserve worktree if it was created (for debugging)
            if wt_path:
                self.worktree_mgr.preserve(module_name)

            err_result = {
                "status": "failed",
                "module": module_name,
                "error": str(e),
            }
            self._print_module_summary(err_result)
            return err_result
