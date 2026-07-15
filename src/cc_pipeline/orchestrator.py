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
        self._merge_lock = __import__("threading").Lock()

        # Shutdown flag (self-contained, no cli import)
        self._shutdown_requested = False

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
        """Check if shutdown has been requested."""
        return self._shutdown_requested

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
                    print(f"  ❌ Module '{module.name}' failed: {e}")
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
                    print(f"  ❌ Module '{module_name}' failed: {e}")
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
                skip_steps = self.state_mgr.get_completed_steps(module_name)
                if skip_steps:
                    print(f"  ⏭️  Resume: skipping {len(skip_steps)} completed step(s) for '{module_name}': {sorted(skip_steps)}")

            wt_path = self.worktree_mgr.create(module_name, from_ref=from_ref, resume=self.resume)

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
                # skip_steps keys are "step_id" or "step_id/loop_file"
                # For non-loop steps: match step_id
                # For loop steps: match step_id + loop_file
                def _is_completed(s):
                    key = f"{s.step_id}/{s.loop_file}" if s.loop_file else s.step_id
                    return key in skip_steps or s.step_id in skip_steps and not s.loop_file
                all_steps = [s for s in all_steps if not _is_completed(s)]
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
                state_manager=state,
                shutdown_check=lambda: self._shutdown_requested,
            )
            runner._continue_on_error = module.continue_on_error

            # Run pipeline
            result = runner.run()

            # Update state
            state.update_module(
                module_name,
                status=result["status"],
                steps_completed=result.get("steps_completed", 0),
                steps_total=result.get("steps_total", 0),
            )

            # On success: merge to base_branch + cleanup
            if result["status"] == "passed":
                if getattr(self.config, "auto_merge", False):
                    merge_ok = False
                    try:
                        merge_ok = self._merge_branch(module_name, branch)
                    except Exception as e:
                        logger.event("merge_error", error=str(e))
                        print(f"  ⚠️  Merge failed: {e}")
                    if merge_ok:
                        self.worktree_mgr.cleanup(module_name)
                        logger.event("merge_success", step="merge", module=module_name)
                        print(f"  🔀 Merged {branch} → {self.config.base_branch}")
                    else:
                        # Merge failed or conflict — preserve worktree for manual fix
                        self.worktree_mgr.preserve(module_name)
                        logger.event("merge_skipped", step="merge", module=module_name,
                                     info="worktree preserved for manual merge")
                        print(f"  ⚠️  Merge conflict — worktree preserved")
                        print(f"     Manual merge: git checkout {self.config.base_branch} && git merge {branch}")
                else:
                    # auto_merge disabled — keep worktree for user to merge manually
                    self.worktree_mgr.preserve(module_name)
                    logger.event("merge_skipped", step="merge", module=module_name,
                                 info="auto_merge disabled, worktree preserved")
                    print(f"  📁 Worktree preserved at {self.worktree_mgr._worktrees.get(module_name, '?')}")
                    print(f"     Branch: {branch}")
                    print(f"     Manual merge: git checkout {self.config.base_branch} && git merge --squash {branch}")
            else:
                self.worktree_mgr.preserve(module_name)

            self._print_module_summary(result)
            return result

        except Exception as e:
            # Log full traceback to transcript
            tb_str = tb_module.format_exc()
            logger.event("module_exception", error=str(e), traceback=tb_str)
            print(f"  ❌ Module '{module_name}' exception: {e}")

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

    def _merge_branch(self, module_name: str, branch: str) -> bool:
        """Squash-merge module worktree branch back to base_branch.

        Thread-safe via _merge_lock.
        If auto_resolve_conflicts is enabled, uses CC to resolve conflicts.

        Returns:
            True if merge succeeded.
            False if merge had conflicts (worktree preserved for manual fix).
        Raises:
            Exception for non-merge errors.
        """
        import subprocess as _sp
        from cc_pipeline.render import render as _render
        repo = str(self.worktree_mgr.repo_path)

        with self._merge_lock:
            # Checkout base_branch
            co_result = _sp.run(["git", "checkout", self.config.base_branch],
                    cwd=repo, capture_output=True, text=True)
            if co_result.returncode != 0:
                raise RuntimeError(
                    f"git checkout {self.config.base_branch} failed (exit {co_result.returncode}):\n"
                    f"  stderr: {co_result.stderr.strip()}"
                )

            # Squash merge (stages all changes without commit)
            result = _sp.run(
                ["git", "merge", "--squash", branch],
                cwd=repo, capture_output=True, text=True,
            )

            if result.returncode != 0:
                # Merge conflict detected
                if self.config.auto_resolve_conflicts:
                    # Try AI resolution
                    if self._try_ai_resolve_conflicts(repo, module_name):
                        # Conflict resolved — stage and commit
                        pass
                    else:
                        _sp.run(["git", "merge", "--abort"], cwd=repo, capture_output=True)
                        return False
                else:
                    _sp.run(["git", "reset", "--hard", "HEAD"], cwd=repo, capture_output=True)
                    return False

            # Build commit message
            msg_tpl = self.config.commit_message or "feat({module}): auto-generated by cc-pipeline"
            commit_msg = _render(msg_tpl, {"module": module_name})

            # Commit the squashed changes
            commit_result = _sp.run(
                ["git", "commit", "-m", commit_msg],
                cwd=repo, capture_output=True, text=True,
            )
            if commit_result.returncode != 0:
                # Nothing to commit (no changes) or other error
                if "nothing to commit" in (commit_result.stdout + commit_result.stderr).lower():
                    return True  # No changes is OK
                raise RuntimeError(f"git commit failed: {commit_result.stderr[:200]}")

            return True

    def _try_ai_resolve_conflicts(self, repo: str, module_name: str) -> bool:
        """Use CC to resolve merge conflicts. Returns True if resolved."""
        import subprocess as _sp

        # Find conflicted files
        status = _sp.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo, capture_output=True, text=True,
        )
        conflicted_files = [f.strip() for f in status.stdout.splitlines() if f.strip()]
        if not conflicted_files:
            return True  # No conflicts to resolve

        print(f"  🤖 Attempting AI conflict resolution for: {conflicted_files}")

        # Build conflict resolution prompt
        files_info = "\n".join(f"  - {f}" for f in conflicted_files)
        prompt = (
            f"以下文件有 git merge 冲突，请分析冲突并解决：\n{files_info}\n\n"
            f"规则：\n"
            f"1. 读取每个冲突文件，找到 <<<<<<< / ======= / >>>>>>> 标记\n"
            f"2. 分析两侧代码的意图，合并成一个正确的版本\n"
            f"3. 移除所有冲突标记\n"
            f"4. 保存修改后的文件\n"
            f"5. 不要删除任何一方的功能代码\n"
        )

        try:
            from cc_pipeline.executor import CCExecutor, CCResult
            executor = CCExecutor(model=self.cc_model or None)
            result = executor.run(
                prompt=prompt,
                cwd=repo,
                allowed_tools=["Read", "Write", "Edit", "Bash"],
            )
            if result.returncode != 0:
                print(f"  ⚠️  AI resolution failed (exit {result.returncode})")
                return False

            # Check if conflicts still remain
            status2 = _sp.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=repo, capture_output=True, text=True,
            )
            remaining = [f.strip() for f in status2.stdout.splitlines() if f.strip()]
            if remaining:
                print(f"  ⚠️  AI could not resolve: {remaining}")
                return False

            # Stage resolved files
            _sp.run(["git", "add", "-A"], cwd=repo, capture_output=True)
            print(f"  ✅ AI resolved all conflicts")
            return True

        except Exception as e:
            print(f"  ⚠️  AI resolution error: {e}")
            return False
