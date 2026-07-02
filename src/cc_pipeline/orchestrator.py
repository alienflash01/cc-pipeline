"""Orchestrator — top-level parallel pipeline coordinator."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cc_pipeline.config import PipelineConfig
from cc_pipeline.compiler import PipelineCompiler
from cc_pipeline.render import render
from cc_pipeline.executor import CCExecutor, ShellExecutor
from cc_pipeline.logger import Logger
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
        cc_model: str = "glm-4.6",
    ):
        self.config = config
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.concurrency = config.concurrency
        self.run_id = "unknown"  # set by CLI
        # Default worktree root under run_dir to avoid cross-test conflicts
        if worktree_root is None:
            worktree_root = str(self.run_dir / "worktrees")
        self.worktree_mgr = WorktreeManager(
            repo_path=config.repo,
            base_branch=config.base_branch,
            worktree_root=worktree_root,
            branch_prefix=config.output_branch_prefix,
        )
        self.compiler = PipelineCompiler(config)
        self.cc_model = cc_model

        # Shared state manager (thread-safe)
        from cc_pipeline.state import StateManager
        self.state_mgr = StateManager(run_dir=str(self.run_dir))

    def run(self) -> list[dict]:
        """Run all modules in parallel.

        Returns:
            List of result dicts, one per module.
        """
        results: list[dict] = []

        # Check for shutdown signal between module dispatches
        import cc_pipeline.cli as cli_mod

        # Serial mode (concurrency=1): check shutdown before each module
        if self.concurrency <= 1:
            for module in self.config.modules:
                if cli_mod._shutdown_requested:
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
                if cli_mod._shutdown_requested:
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
            # Create worktree
            wt_path = self.worktree_mgr.create(module_name)

            # Find the module config
            module = next(m for m in self.config.modules if m.name == module_name)
            branch = f"{self.config.output_branch_prefix}/{module_name}"

            # Save initial state
            state.update_module(
                module_name,
                status="running", worktree=wt_path, branch=branch,
            )
            state.set_run_id(getattr(self, "run_id", "unknown"))

            # Compile steps
            steps = self.compiler.compile_module(module_name)

            # Create runner
            runner = ModuleRunner(
                steps=steps,
                module_name=module_name,
                worktree_path=wt_path,
                run_dir=str(self.run_dir),
                cc_executor=CCExecutor(model=self.cc_model),
                shell_executor=ShellExecutor(),
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
                    # Use config pr_labels and pr_title if set, otherwise default
                    labels = self.config.pr_labels if self.config.pr_labels else ["auto-generated"]
                    title = self.config.pr_title_template
                    if not title:
                        title = f"Pipeline for {module_name}"
                    else:
                        title = render(title, {
                            "module": module_name,
                            "spec_id": module.spec_id,
                            "source_dir": module.source_dir,
                        })
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
                except Exception:
                    pass  # PR creation is best-effort

                self.worktree_mgr.cleanup(module_name)
            else:
                self.worktree_mgr.preserve(module_name)

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

            return {
                "status": "failed",
                "module": module_name,
                "error": str(e),
            }
