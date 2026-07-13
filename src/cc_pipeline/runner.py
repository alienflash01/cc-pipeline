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
import re
import time as _time_mod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from cc_pipeline.compiler import CompiledStep
from cc_pipeline.executor import CCExecutor, CCResult, ShellExecutor, ShellResult
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
MAX_FREE_RATE_LIMIT_RETRIES = 3   # max free retries before consuming budget
RATE_LIMIT_BACKOFF_SECS = 30      # wait between rate-limit retries


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


RATE_LIMIT_PATTERNS = [
    r"\b429\b",
    r"\brate[ _-]?limit\b",
    r"\btoo many requests\b",
    r"\b1302\b",
]
_RATE_LIMIT_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


def _is_rate_limited(stderr: str) -> bool:
    """Check if stderr indicates rate limiting (word-boundary match)."""
    if not stderr:
        return False
    return bool(_RATE_LIMIT_RE.search(stderr))
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
      5. If pass → record to state.json → next step
      6. If fail → retry (up to step.retry times, no rollback)
      7. If retries exhausted → on_failure jump or module failed
    """

    def __init__(
        self,
        steps: list[CompiledStep],
        module_name: str,
        worktree_path: str,
        run_dir: str,
        cc_executor: CCExecutor | None = None,
        shell_executor: ShellExecutor | None = None,
        verbose: int = 0,
        state_manager=None,
    ):
        self.steps = steps
        self.module_name = module_name
        self.worktree_path = worktree_path
        self.run_dir = run_dir
        self.cc_executor = cc_executor or CCExecutor()
        self.shell_executor = shell_executor or ShellExecutor()
        self.logger = Logger(run_dir=run_dir, module_name=module_name)
        self.verbose = verbose
        self.state_manager = state_manager

    def run(self) -> dict:
        """Execute all steps sequentially. Supports on_failure jump-back.

        When a step with on_failure set fails (after exhausting retries),
        the runner jumps back to the target step (no git rollback).
        Limited to MAX_ON_FAILURE_JUMPS to prevent infinite loops.

        Returns:
            Dict with keys: status, module, steps_completed, steps_total.
        """
        MAX_ON_FAILURE_JUMPS = 2  # default, overridden by step.on_failure_max_jumps
        total = len(self.steps)
        completed = 0

        step_idx = 0
        jump_counts = {}  # per target step: {target_step_id: count}

        while step_idx < len(self.steps):
            step = self.steps[step_idx]
            passed = False

            retry_budget = step.retry  # budget that CAN be consumed
            extra_retries = 0  # rate-limit retries (free, don't consume budget)

            while True:
                current_attempt = step.retry - retry_budget + 1 + extra_retries
                if current_attempt < 1:
                    current_attempt = 1

                self.logger.event("step_start", step=step.step_id, attempt=current_attempt,
                                  loop_file=step.loop_file)

                if self.verbose >= 1:
                    file_info = f" [{step.loop_file}]" if step.loop_file else ""
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"  [{ts}] [{self.module_name}]{file_info} {step.step_id} START")

                # -vv: print full prompt or shell command
                if self.verbose >= 2:
                    ts2 = datetime.now().strftime("%H:%M:%S")
                    if step.executor in ("claude-code", "judge"):
                        prompt_text = self._inject_context(step.rendered_prompt, step)
                        print(f"  [{ts2}]   PROMPT:")
                        for pline in prompt_text.splitlines():
                            print(f"  [{ts2}]   │ {pline}")
                    elif step.executor == "shell":
                        print(f"  [{ts2}]   SHELL: {step.rendered_prompt}")

                # Execute the step with layered error handling
                exec_result = self._execute_step(step)

                # Layer 1: Rate limit → free retry with backoff, limited count
                if exec_result.outcome == ExecOutcome.RATE_LIMITED:
                    if extra_retries < MAX_FREE_RATE_LIMIT_RETRIES:
                        if self.verbose >= 1:
                            ts = datetime.now().strftime("%H:%M:%S")
                            print(f"  [{ts}] [{self.module_name}] {step.step_id} ⏳ RATE LIMIT (retry {extra_retries+1}/{MAX_FREE_RATE_LIMIT_RETRIES})")
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
                            reason="Rate limit free retries exhausted, consuming budget",
                        )
                        exec_result = ExecResult(
                            ExecOutcome.CC_FAILED,
                            reason=f"Rate limit persisted after {MAX_FREE_RATE_LIMIT_RETRIES} free retries",
                        )

                # Layer 2: CC failed / zero-work / timeout / error → skip postcondition
                if exec_result.outcome in (ExecOutcome.CC_FAILED, ExecOutcome.ZERO_WORK,
                                           ExecOutcome.TIMEOUT, ExecOutcome.UNKNOWN_ERROR):
                    failure_reason = f"{exec_result.outcome.value}: {exec_result.reason}"

                    if retry_budget > 0:
                        retry_budget -= 1
                        self.logger.log_retry(
                            step=step.step_id, attempt=current_attempt,
                            reason=failure_reason,
                        )
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"  [{ts}] [{self.module_name}] {step.step_id} ⚠️  RETRY (attempt {current_attempt}) — {failure_reason}")
                        continue
                    else:
                        self.logger.log_fail(
                            step=step.step_id, attempt=current_attempt,
                            reason=failure_reason,
                        )
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"  [{ts}] [{self.module_name}] {step.step_id} ❌ FAIL — {failure_reason}")
                        break  # exit inner while, step failed

                # Layer 3: CC succeeded → check postcondition (inside while True)
                pc_result = self._check_postcondition(step)
                if pc_result.passed:
                    self.logger.log_pass(step=step.step_id, attempt=current_attempt,
                                         info={"reason": pc_result.reason})
                    self._mark_step_completed(step)
                    self._append_progress(step, "PASS", current_attempt)
                    completed = step_idx + 1
                    passed = True
                    if self.verbose >= 1:
                        file_info = f" [{step.loop_file}]" if step.loop_file else ""
                        ts = datetime.now().strftime("%H:%M:%S")
                        print(f"  [{ts}] [{self.module_name}]{file_info} {step.step_id} PASS")
                    break
                else:
                    if retry_budget > 0:
                        retry_budget -= 1
                        self.logger.log_retry(
                            step=step.step_id, attempt=current_attempt,
                            reason=pc_result.reason,
                        )
                        if self.verbose >= 1:
                            ts = datetime.now().strftime("%H:%M:%S")
                            print(f"  [{ts}] [{self.module_name}] {step.step_id} ⚠️  RETRY (attempt {current_attempt}) — {pc_result.reason}")
                        continue
                    else:
                        self.logger.log_fail(
                            step=step.step_id, attempt=current_attempt,
                            reason=pc_result.reason,
                        )
                        if self.verbose >= 1:
                            ts = datetime.now().strftime("%H:%M:%S")
                            print(f"  [{ts}] [{self.module_name}] {step.step_id} ❌ FAIL — {pc_result.reason}")
                            self._print_postcondition_diag(pc_result)
                        break

            # After inner while: check passed/on_failure

            if not passed:
                # Check on_failure jump-back (per-target jump count)
                target = step.on_failure
                max_jumps = getattr(step, "on_failure_max_jumps", MAX_ON_FAILURE_JUMPS)
                # Key includes loop_file for per_file steps (key consistency rule)
                target_key = f"{target}/{step.loop_file}" if step.loop_file else target
                jc = jump_counts.get(target_key, 0)
                if target and jc < max_jumps:
                    # Find target step index — match step_id AND loop_file
                    target_idx = None
                    for j, s in enumerate(self.steps):
                        if s.step_id == target and s.loop_file == step.loop_file:
                            target_idx = j
                            break
                    if target_idx is not None:
                        jump_counts[target_key] = jc + 1
                        # Clear completed marks from jump target onwards
                        # (jump invalidates all steps from target forward)
                        for s in self.steps[target_idx:]:
                            if self.state_manager:
                                self.state_manager.clear_step_completed(
                                    self.module_name, s.step_id, s.loop_file or "")
                        self.logger.event(
                            "on_failure_jump",
                            step=step.step_id, attempt=0,
                            info={"from": step.step_id, "to": target,
                                  "jump": jump_counts[target_key]},
                        )
                        step_idx = target_idx
                        if self.verbose >= 1:
                            ts = datetime.now().strftime("%H:%M:%S")
                            file_info = f"[{step.loop_file}]" if step.loop_file else ""
                            print(f"  [{ts}] [{self.module_name}] ↩️  JUMP: {step.step_id}{file_info} → {target}{file_info} (jump {jump_counts[target_key]})")
                        continue
                return {
                    "status": "failed",
                    "module": self.module_name,
                    "steps_completed": completed,
                    "steps_total": total,
                    "error": f"Step '{step.step_id}' failed after {max(0, step.retry) + 1} attempts",
                }

            step_idx += 1

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
        """Inject prior step outputs + progress + output instruction into prompt."""
        pipeline_dir = Path(self.worktree_path) / ".pipeline"

        # Inject progress.md if it exists (Anthropic harness pattern)
        progress_file = pipeline_dir / "progress.md"
        if progress_file.exists():
            content = progress_file.read_text().strip()
            if content:
                # Cap to last 20 lines to prevent unbounded growth
                lines = content.splitlines()
                if len(lines) > 20:
                    content = "\n".join(lines[-20:])
                prompt += f"\n\n--- 进度记录 ---\n{content}\n---\n"

        # Inject prior step outputs if they exist (capped to last 3 files, max 10KB total)
        if pipeline_dir.exists():
            prior_files = sorted(pipeline_dir.glob("*.json"))[-3:]  # last 3 files only
            if prior_files:
                context_lines = ["\n\n--- 前序步骤的上下文 ---"]
                total_size = 0
                for f in prior_files:
                    try:
                        content = f.read_text().strip()
                        if content:
                            total_size += len(content)
                            if total_size > 10240:  # 10KB cap
                                context_lines.append(f"[{f.name}]: (truncated, context size limit reached)")
                                break
                            context_lines.append(f"[{f.name}]:\n{content}")
                    except Exception:
                        import warnings
                        warnings.warn(f"Failed to read context file {f.name}", stacklevel=2)
                context_lines.append("---")
                prompt += "\n".join(context_lines)

        # Inject output write instruction
        if step.output:
            # Sanitize: strip path traversal and slashes
            safe_output = step.output.replace("..", "").replace("/", "").replace("\\", "")
            # Use custom output_prompt if provided, otherwise default
            output_tpl = getattr(step, "output_prompt", None)
            if output_tpl:
                prompt += "\n\n---\n" + output_tpl.replace("{output}", safe_output)
            else:
                prompt += (
                    "\n\n---\n请将本次执行的关键信息（创建的文件、关键决策、覆盖率数据等）"
                    f"以 JSON 格式写入 .pipeline/{safe_output}"
                )

        return prompt

    def _mark_step_completed(self, step: CompiledStep) -> None:
        """Mark step as completed in state.json (for resume)."""
        if self.state_manager:
            self.state_manager.mark_step_completed(
                self.module_name, step.step_id, step.loop_file or ""
            )

    def _append_progress(self, step: CompiledStep, status: str, attempt: int) -> None:
        """Append a progress entry to .pipeline/progress.md after each step.

        Follows Anthropic's harness pattern: each CC session can read what
        previous sessions accomplished.
        """
        progress_file = Path(self.worktree_path) / ".pipeline" / "progress.md"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        loop_info = f" [{step.loop_file}]" if step.loop_file else ""
        entry = f"- [{status.upper()}] {step.step_id}{loop_info} (module={self.module_name}, attempt={attempt})\n"

        with open(progress_file, "a") as f:
            f.write(entry)

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
                    timeout=step.timeout,
                )
                # Audit: log shell command execution
                self.logger.log_command_audit(
                    step=step.step_id, command=full_prompt,
                    cwd=self.worktree_path, executor="shell",
                    returncode=result.returncode,
                )
                if result.returncode != 0:
                    if _is_rate_limited(result.stderr):
                        return ExecResult(ExecOutcome.RATE_LIMITED, result, "Shell rate limited")
                    # Include stdout/stderr tail in reason for debugging
                    stderr_tail = (result.stderr or "").strip().splitlines()[-5:]
                    stdout_tail = (result.stdout or "").strip().splitlines()[-5:]
                    detail_parts = [f"exit {result.returncode}"]
                    if stderr_tail:
                        detail_parts.append("stderr: " + " | ".join(stderr_tail))
                    if stdout_tail:
                        detail_parts.append("stdout: " + " | ".join(stdout_tail))
                    reason = " — ".join(detail_parts)
                    # Always print shell error to terminal (not just verbose)
                    print(f"  ❌ Shell failed (exit {result.returncode}): {step.rendered_prompt[:80]}")
                    for line in stderr_tail:
                        print(f"     │ {line}")
                    return ExecResult(ExecOutcome.CC_FAILED, result, reason)
                return ExecResult(ExecOutcome.SUCCESS, result)
            except subprocess.TimeoutExpired:
                return ExecResult(ExecOutcome.TIMEOUT, reason="Shell timeout")
            except Exception as e:
                return ExecResult(ExecOutcome.UNKNOWN_ERROR, reason=str(e))

        # claude-code and judge both use CC
        allowed_tools = None
        if step.executor == "judge":
            allowed_tools = ["Read", "Bash"]  # judge is read-only

        # Record the exact prompt handed to CC (truncated) before execution,
        # so a failed/hung run can still be audited.
        self.logger.log_prompt(step=step.step_id, prompt=full_prompt)

        try:
            # Use step-level model override if set
            executor = self.cc_executor
            if step.model:
                executor = CCExecutor(model=step.model)

            cc_result = executor.run(
                prompt=full_prompt,
                cwd=self.worktree_path,
                allowed_tools=allowed_tools,
                timeout=step.timeout,
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
            self.logger.log_cc_result(step=step.step_id, cc_result=cc_result)
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
        self.logger.log_cc_result(step=step.step_id, cc_result=cc_result)
        # Audit: log file changes after CC execution
        changes = self._detect_file_changes()
        self.logger.log_file_changes(step=step.step_id, changes=changes)
        if self.verbose >= 2 and cc_result.stdout:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}]   CC OUTPUT (exit {cc_result.returncode}):")
            for line in (cc_result.stdout or "").strip().splitlines()[:5]:
                print(f"  [{ts}]   │ {line}")
        return ExecResult(ExecOutcome.SUCCESS, cc_result)

    def _check_postcondition(self, step: CompiledStep) -> PostconditionResult:
        """Evaluate a step's postcondition."""
        if step.postcondition is None:
            return PostconditionResult(passed=True, reason="No postcondition")

        shell = step.postcondition.get("shell", "true")
        expect = step.postcondition.get("expect")

        # Always print postcondition command (not just verbose)
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  [{ts}] [{self.module_name}] postcondition: {shell[:100]}")

        result = eval_postcondition(
            shell=shell,
            expect=expect,
            cwd=self.worktree_path,
        )

        # Always print postcondition result when failed
        if not result.passed:
            ts = datetime.now().strftime("%H:%M:%S")
            stdout_preview = (result.stdout or "")[:200]
            print(f"  [{ts}] [{self.module_name}] postcondition FAIL: {result.reason}")
            if stdout_preview:
                print(f"  [{ts}] [{self.module_name}]   stdout: {stdout_preview}")

        return result

    def _detect_file_changes(self) -> list[str]:
        """Detect files created/modified by CC via git status."""
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True,
            cwd=self.worktree_path,
        )
        changes = []
        for line in result.stdout.strip().splitlines():
            if line.strip():
                changes.append(line)
        return changes

    def _suffix_hint(self, shell: str, changes: list[str]) -> str:
        """Hint when the postcondition checks one suffix but CC made another.

        Checks both git status output and the actual filesystem for files
        with mismatched suffixes.
        """
        import os
        # Collect all relevant file paths from changes + filesystem
        all_files = list(changes)
        # Also scan tests/ directory on filesystem if it exists
        tests_dir = os.path.join(self.worktree_path, "tests")
        if os.path.isdir(tests_dir):
            for f in os.listdir(tests_dir):
                all_files.append(f)

        if ".c" in shell and any(".py" in f for f in all_files):
            return "checking .c but CC generated .py"
        if ".py" in shell and any(".c" in f for f in all_files):
            return "checking .py but CC generated .c"
        return ""

    def _print_postcondition_diag(self, pc_result: PostconditionResult) -> None:
        """Print postcondition-failure diagnostics (call only in verbose >= 1).

        Shows what was checked (the shell command), what CC changed on disk
        (git status), and a suffix-mismatch hint — so a failed postcondition
        is debuggable instead of a bare exit code.
        """
        ts = datetime.now().strftime("%H:%M:%S")
        shell = pc_result.shell_command or ""
        if shell:
            print(f"  [{ts}]   postcondition: {shell}")

        # Shell stdout tail (cap 3 lines) — often explains the mismatch
        if pc_result.stdout.strip():
            for line in pc_result.stdout.strip().splitlines()[:3]:
                print(f"  [{ts}]   │ {line}")

        changes = self._detect_file_changes()
        if changes:
            print(f"  [{ts}]   CC changed files:")
            for line in changes:
                print(f"  [{ts}]     {line}")

        hint = self._suffix_hint(shell, changes)
        if hint:
            print(f"  [{ts}]   💡 Hint: {hint}")
