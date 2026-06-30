"""Orchestrator — top-level parallel pipeline coordinator."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cc_pipeline.config import PipelineConfig
from cc_pipeline.compiler import PipelineCompiler
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

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            future_to_module = {}
            for module in self.config.modules:
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
        """Run a single module's pipeline in its own worktree."""
        from cc_pipeline.pr import PRCreator

        # Use shared state manager
        state = self.state_mgr

        # Create worktree
        wt_path = self.worktree_mgr.create(module_name)

        # Find the module config
        module = next(m for m in self.config.modules if m.name == module_name)
        branch = f"{self.config.output_branch_prefix}/{module_name}"

        # Save initial state (use update_module, not save, to avoid overwriting other modules)
        state.update_module(
            module_name,
            status="running", worktree=wt_path, branch=branch,
        )
        # Ensure run_id is set (only once, by first thread — but update_module is idempotent)
        state.set_run_id(getattr(self, "run_id", "unknown"))

        # Compile steps for this module
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
            # Create PR if we have gh available
            try:
                pr_creator = PRCreator(repo_path=str(self.worktree_mgr.repo_path))
                pr_url = pr_creator.create(
                    branch=branch,
                    title=f"UT for {module_name}",
                    body=f"Auto-generated tests for {module_name} (spec: {module.spec_id})",
                    labels=["auto-generated", "ut"],
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
