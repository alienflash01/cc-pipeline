"""Module Runner — executes compiled pipeline steps sequentially for one module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cc_pipeline.compiler import CompiledStep
from cc_pipeline.executor import CCExecutor, CCResult, ShellExecutor, ShellResult
from cc_pipeline.git_checkpoint import GitCheckpoint
from cc_pipeline.logger import Logger
from cc_pipeline.postcondition import evaluate as eval_postcondition, PostconditionResult


@dataclass
class RunnerResult:
    """Result of a module pipeline run."""
    status: str  # "passed" | "failed"
    module: str
    steps_completed: int = 0
    steps_total: int = 0
    error: str = ""


class ModuleRunner:
    """Runs a compiled pipeline (list of CompiledSteps) for a single module.

    Executes steps sequentially. Each step:
      1. Run executor (claude-code / shell / judge)
      2. Evaluate postcondition
      3. If pass → git checkpoint → next step
      4. If fail → git rollback → retry (up to step.retry times)
      5. If retries exhausted → module failed
    """

    def __init__(
        self,
        steps: list[CompiledStep],
        module_name: str,
        worktree_path: str,
        run_dir: str,
        cc_executor: CCExecutor | None = None,
        shell_executor: ShellExecutor | None = None,
    ):
        self.steps = steps
        self.module_name = module_name
        self.worktree_path = worktree_path
        self.run_dir = run_dir
        self.cc_executor = cc_executor or CCExecutor()
        self.shell_executor = shell_executor or ShellExecutor()
        self.logger = Logger(run_dir=run_dir, module_name=module_name)
        self.git_checkpoint = GitCheckpoint(worktree_path)

    def run(self) -> dict:
        """Execute all steps sequentially.

        Returns:
            Dict with keys: status, module, steps_completed, steps_total.
        """
        total = len(self.steps)
        completed = 0

        for i, step in enumerate(self.steps, 1):
            step_label = f"{step.step_id}" + (f"[{step.loop_file}]" if step.loop_file else "")
            passed = False

            for attempt in range(1, step.retry + 1):
                self.logger.event("step_start", step=step.step_id, attempt=attempt,
                                  loop_file=step.loop_file)

                # Execute the step
                self._execute_step(step)

                # Evaluate postcondition
                pc_result = self._check_postcondition(step)

                if pc_result.passed:
                    self.logger.log_pass(step=step.step_id, attempt=attempt,
                                         info={"reason": pc_result.reason})
                    self.git_checkpoint.checkpoint(
                        step=step.step_id,
                        module=self.module_name,
                        attempt=attempt,
                    )
                    completed = i
                    passed = True
                    break
                else:
                    if attempt < step.retry:
                        self.logger.log_retry(
                            step=step.step_id, attempt=attempt,
                            reason=pc_result.reason,
                        )
                        # Rollback to previous step's checkpoint before retry
                        if i > 1:
                            prev_step = self.steps[i - 2]
                            self.git_checkpoint.rollback(
                                step=prev_step.step_id,
                                module=self.module_name,
                                attempt=1,  # rollback to first attempt of previous step
                            )
                    else:
                        self.logger.log_fail(
                            step=step.step_id, attempt=attempt,
                            reason=pc_result.reason,
                        )

            if not passed:
                return {
                    "status": "failed",
                    "module": self.module_name,
                    "steps_completed": completed,
                    "steps_total": total,
                    "error": f"Step '{step.step_id}' failed after {step.retry} attempts",
                }

        return {
            "status": "passed",
            "module": self.module_name,
            "steps_completed": completed,
            "steps_total": total,
        }

    def _execute_step(self, step: CompiledStep) -> None:
        """Execute a step using the appropriate executor."""
        if step.executor == "shell":
            self.shell_executor.run(
                command=step.rendered_prompt,
                cwd=self.worktree_path,
            )
        else:
            # claude-code and judge both use CC
            allowed_tools = None
            if step.executor == "judge":
                allowed_tools = ["Read", "Bash"]  # judge is read-only

            self.cc_executor.run(
                prompt=step.rendered_prompt,
                cwd=self.worktree_path,
                allowed_tools=allowed_tools,
            )

    def _check_postcondition(self, step: CompiledStep) -> PostconditionResult:
        """Evaluate a step's postcondition."""
        if step.postcondition is None:
            # No postcondition = always pass
            return PostconditionResult(passed=True, reason="No postcondition")

        shell = step.postcondition.get("shell", "true")
        expect = step.postcondition.get("expect")

        return eval_postcondition(
            shell=shell,
            expect=expect,
            cwd=self.worktree_path,
        )
