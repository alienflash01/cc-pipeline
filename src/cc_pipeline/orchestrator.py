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
        self.worktree_mgr = WorktreeManager(
            repo_path=config.repo,
            base_branch=config.base_branch,
            worktree_root=worktree_root,
            branch_prefix=config.output_branch_prefix,
        )
        self.compiler = PipelineCompiler(config)
        self.cc_model = cc_model

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
        # Create worktree
        wt_path = self.worktree_mgr.create(module_name)

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

        # Cleanup or preserve based on result
        if result["status"] == "passed":
            self.worktree_mgr.cleanup(module_name)
        else:
            self.worktree_mgr.preserve(module_name)

        return result
