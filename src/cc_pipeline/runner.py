"""Module Runner — executes compiled pipeline steps sequentially for one module.

Implements CO-style layered error handling:
  1. CC returncode != 0 → immediate failure, skip postcondition
  2. CC zero-work detection (empty output) → immediate failure
  3. Rate limit (429) → retry without consuming budget (with backoff + max limit)
  4. Timeout → retry, consumes budget
  5. CC success → proceed to postcondition
"""
from __future__ import annotations

import subprocess
import time as _time_mod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from cc_pipeline.compiler import CompiledStep
from cc_pipeline.executor import CCExecutor, CCResult, ShellExecutor, ShellResult
from cc_pipeline.git_checkpoint import GitCheckpoint
from cc_pipeline.logger import Logger
from cc_pipeline.postcondition import evaluate as eval_postcondition, PostconditionResult


class ExecOutcome(Enum):
    """Outcome of a single CC/shell execution."""
    SUCCESS = "success"
    CC_FAILED = "cc_failed"           # CC returncode != 0
    ZERO_WORK = "zero_work"            # CC returned nothing useful
    RATE_LIMITED = "rate_limited"      # 429 — don't consume retry
    TIMEOUT = "timeout"                # subprocess timeout
    UNKNOWN_ERROR = "unknown_error"


# CO-style rate limit protection
MAX_FREE_RATE_LIMIT_RETRIES = 5   # max free retries before consuming budget
RATE_LIMIT_BACKOFF_SECS = 60      # wait between rate-limit retries (CO default: 120)


@dataclass
class ExecResult:
    """Result of executing one step."""
    outcome: ExecOutcome
    cc_result: CCResult | ShellResult | None = None
    reason: str = ""


@dataclass
class RunnerResult:
    """Result of a module pipeline run."""
    status: str  # "passed" | "failed"
    module: str
    steps_completed: int = 0
    steps_total: int = 0
    error: str = ""


RATE_LIMIT_PATTERNS = ["429", "rate_limit", "rate limit", "too many requests", "1302"]


def _is_rate_limited(stderr: str) -> bool:
    """Check if stderr indicates a rate limit error."""
    lower = stderr.lower()
    return any(p in lower for p in RATE_LIMIT_PATTERNS)


def _is_zero_work(cc_result: CCResult) -> bool:
    """Detect CC that exited without producing meaningful output."""
    return (
        cc_result.returncode == 0
        and not cc_result.stdout.strip()
        and not cc_result.stderr.strip()
    )


class ModuleRunner:
    """Runs a compiled pipeline (list of CompiledSteps) for a single module.

    Executes steps sequentially. Each step:
      1. Run executor (claude-code / shell / judge) with layered error handling
      2. If CC failed / zero-work → skip postcondition, go to retry
      3. If rate-limited → retry without consuming budget
      4. If CC succeeded → evaluate postcondition
      5. If pass → git checkpoint → next step
      6. If fail → git rollback → retry (up to step.retry times)
      7. If retries exhausted → module failed
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
            passed = False

            retry_budget = step.retry  # budget that CAN be consumed
            extra_retries = 0  # rate-limit retries (free, don't consume budget)

            while True:
                attempt_num = retry_budget - step.retry + extra_retries + 1 if step.retry > 0 else 1
                current_attempt = step.retry - retry_budget + 1 + extra_retries
                if current_attempt < 1:
                    current_attempt = 1

                self.logger.event("step_start", step=step.step_id, attempt=current_attempt,
                                  loop_file=step.loop_file)

                # Execute the step with layered error handling
                exec_result = self._execute_step(step)

                # Layer 1: Rate limit → free retry with backoff, limited count
                if exec_result.outcome == ExecOutcome.RATE_LIMITED:
                    if extra_retries < MAX_FREE_RATE_LIMIT_RETRIES:
                        self.logger.log_retry(
                            step=step.step_id, attempt=current_attempt,
                            reason=f"Rate limited (free retry {extra_retries+1}/{MAX_FREE_RATE_LIMIT_RETRIES}): {exec_result.reason}",
                        )
                        _time_mod.sleep(RATE_LIMIT_BACKOFF_SECS)
                        extra_retries += 1
                        continue  # retry without touching retry_budget
                    else:
                        # Free retries exhausted — treat as CC failure, consume budget
                        self.logger.log_retry(
                            step=step.step_id, attempt=current_attempt,
                            reason=f"Rate limit free retries exhausted, consuming budget",
                        )
                        exec_result = ExecResult(
                            ExecOutcome.CC_FAILED,
                            reason=f"Rate limit persisted after {MAX_FREE_RATE_LIMIT_RETRIES} free retries",
                        )

                # Layer 2: CC failed / zero-work / timeout / error → skip postcondition
                if exec_result.outcome in (ExecOutcome.CC_FAILED, ExecOutcome.ZERO_WORK,
                                           ExecOutcome.TIMEOUT, ExecOutcome.UNKNOWN_ERROR):
                    failure_reason = f"{exec_result.outcome.value}: {exec_result.reason}"

                    if retry_budget > 1:
                        retry_budget -= 1
                        self.logger.log_retry(
                            step=step.step_id, attempt=current_attempt,
                            reason=failure_reason,
                        )
                        if i > 1:
                            prev_step = self.steps[i - 2]
                            self.git_checkpoint.rollback_to_latest(
                                step=prev_step.step_id,
                                module=self.module_name,
                            )
                        continue
                    else:
                        self.logger.log_fail(
                            step=step.step_id, attempt=current_attempt,
                            reason=failure_reason,
                        )
                        break  # exit while loop, step failed

                # Layer 3: CC succeeded → check postcondition
                pc_result = self._check_postcondition(step)

                if pc_result.passed:
                    self.logger.log_pass(step=step.step_id, attempt=current_attempt,
                                         info={"reason": pc_result.reason})
                    self.git_checkpoint.checkpoint(
                        step=step.step_id,
                        module=self.module_name,
                        attempt=current_attempt,
                    )
                    completed = i
                    passed = True
                    break
                else:
                    if retry_budget > 1:
                        retry_budget -= 1
                        self.logger.log_retry(
                            step=step.step_id, attempt=current_attempt,
                            reason=pc_result.reason,
                        )
                        if i > 1:
                            prev_step = self.steps[i - 2]
                            self.git_checkpoint.rollback_to_latest(
                                step=prev_step.step_id,
                                module=self.module_name,
                            )
                        continue
                    else:
                        self.logger.log_fail(
                            step=step.step_id, attempt=current_attempt,
                            reason=pc_result.reason,
                        )
                        break

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

    def _ensure_pipeline_dir(self) -> Path:
        """Ensure .pipeline/ directory exists in worktree."""
        pd = Path(self.worktree_path) / ".pipeline"
        pd.mkdir(parents=True, exist_ok=True)
        return pd

    def _inject_context(self, prompt: str, step: CompiledStep) -> str:
        """Inject prior step outputs + output write instruction into prompt."""
        pipeline_dir = Path(self.worktree_path) / ".pipeline"

        # Inject prior step outputs if they exist
        if pipeline_dir.exists():
            prior_files = sorted(pipeline_dir.glob("*.json"))
            if prior_files:
                context_lines = ["\n\n--- 前序步骤的上下文 ---"]
                for f in prior_files:
                    try:
                        content = f.read_text().strip()
                        if content:
                            context_lines.append(f"[{f.name}]:\n{content}")
                    except Exception:
                        pass
                context_lines.append("---\n")
                prompt += "\n".join(context_lines)

        # Inject output write instruction
        if step.output:
            prompt += (
                f"\n\n---\n请将本次执行的关键信息（创建的文件、关键决策、覆盖率数据等）"
                f"以 JSON 格式写入 .pipeline/{step.output}"
            )

        return prompt

    def _execute_step(self, step: CompiledStep) -> ExecResult:
        """Execute a step using the appropriate executor.

        Returns ExecResult with classified outcome.
        CO-style layered error handling.
        """
        # Ensure .pipeline/ exists for context passing
        self._ensure_pipeline_dir()

        # Shell executor: use raw prompt as-is (it IS the shell command)
        if step.executor == "shell":
            full_prompt = step.rendered_prompt
        else:
            # CC/judge: inject prior context + output instruction
            full_prompt = self._inject_context(step.rendered_prompt, step)

        if step.executor == "shell":
            try:
                result = self.shell_executor.run(
                    command=full_prompt,
                    cwd=self.worktree_path,
                )
                if result.returncode != 0:
                    if _is_rate_limited(result.stderr):
                        return ExecResult(ExecOutcome.RATE_LIMITED, result, "Shell rate limited")
                    return ExecResult(ExecOutcome.CC_FAILED, result, f"exit {result.returncode}")
                return ExecResult(ExecOutcome.SUCCESS, result)
            except subprocess.TimeoutExpired:
                return ExecResult(ExecOutcome.TIMEOUT, reason="Shell timeout")
            except Exception as e:
                return ExecResult(ExecOutcome.UNKNOWN_ERROR, reason=str(e))

        # claude-code and judge both use CC
        allowed_tools = None
        if step.executor == "judge":
            allowed_tools = ["Read", "Bash"]  # judge is read-only

        try:
            cc_result = self.cc_executor.run(
                prompt=full_prompt,
                cwd=self.worktree_path,
                allowed_tools=allowed_tools,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(ExecOutcome.TIMEOUT, reason="CC timeout")
        except Exception as e:
            return ExecResult(ExecOutcome.UNKNOWN_ERROR, reason=str(e))

        # Layer 1: Rate limit detection (don't consume retry budget)
        if _is_rate_limited(cc_result.stderr):
            return ExecResult(ExecOutcome.RATE_LIMITED, cc_result, "API rate limited")

        # Layer 2: CC non-zero exit → immediate failure
        if cc_result.returncode != 0:
            return ExecResult(
                ExecOutcome.CC_FAILED, cc_result,
                f"CC exit {cc_result.returncode}: {cc_result.stderr[:120]}",
            )

        # Layer 3: Zero-work detection
        if _is_zero_work(cc_result):
            return ExecResult(
                ExecOutcome.ZERO_WORK, cc_result,
                "CC produced no output — likely did no work",
            )

        # Success
        return ExecResult(ExecOutcome.SUCCESS, cc_result)

    def _check_postcondition(self, step: CompiledStep) -> PostconditionResult:
        """Evaluate a step's postcondition."""
        if step.postcondition is None:
            return PostconditionResult(passed=True, reason="No postcondition")

        shell = step.postcondition.get("shell", "true")
        expect = step.postcondition.get("expect")

        return eval_postcondition(
            shell=shell,
            expect=expect,
            cwd=self.worktree_path,
        )
