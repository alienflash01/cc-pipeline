"""Postcondition Evaluator — shell + expect expression evaluation."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


@dataclass
class PostconditionResult:
    """Result of a postcondition evaluation."""
    passed: bool
    stdout: str = ""
    stderr: str = ""
    reason: str = ""
    shell_command: str = ""  # the shell command that was evaluated


def evaluate(
    shell: str,
    cwd: str,
    expect: str | bool | None = None,
    timeout: int = 300,
) -> PostconditionResult:
    """Evaluate a postcondition by running a shell command and checking expect.

    Args:
        shell: Shell command to run. stdout should be JSON (if expect uses $.).
        expect: Expression to evaluate against JSON output, or None.
        cwd: Working directory for the command.
        timeout: Timeout in seconds.

    Returns:
        PostconditionResult with passed=True if conditions are met.
    """
    try:
        result = subprocess.run(
            shell,
            shell=True,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return PostconditionResult(
            passed=False, stdout="", stderr="",
            reason=f"Shell timed out after {timeout}s",
            shell_command=shell,
        )

    # Decode safely (handle binary output)
    stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else (result.stdout or "")
    stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")

    # bool expect (YAML false/true) — checked before returncode
    if isinstance(expect, bool):
        passed = (result.returncode == 0) == expect
        return PostconditionResult(
            passed=passed,
            stdout=stdout, stderr=stderr,
            reason="pass" if passed else f"expected exit {'0' if expect else 'non-0'}, got {result.returncode}",
        )
    # Shell failed → postcondition fails
    if result.returncode != 0:
        pc_result = PostconditionResult(
            passed=False,
            stdout=stdout,
            stderr=stderr,
            reason=f"Shell command exited with code {result.returncode}",
        )
    elif expect is None:
        # No expect → pass (shell exited 0)
        pc_result = PostconditionResult(
            passed=True,
            stdout=stdout,
            stderr=stderr,
        )
    else:
        # Parse expect expression
        pc_result = _evaluate_expect(expect, stdout, stderr)

    # Record the shell command that produced this result (for diagnostics)
    pc_result.shell_command = shell
    return pc_result


def _evaluate_expect(expect: str | bool, stdout: str, stderr: str) -> PostconditionResult:
    """Evaluate an expect expression against stdout."""
    expect = expect.strip()

    # contains('text') — check if stdout contains literal text
    contains_match = re.match(r"contains\(['\"](.+?)['\"]\)", expect)
    if contains_match:
        text = contains_match.group(1)
        passed = text in stdout
        return PostconditionResult(
            passed=passed,
            stdout=stdout,
            stderr=stderr,
            reason=f"contains('{text}'): {'found' if passed else 'not found'}",
        )

    # "true" / "false" / "null" — literal exit-code-based result
    # (shell exited 0 = true, non-zero handled earlier)
    if expect.lower() == "true":
        return PostconditionResult(passed=True, stdout=stdout, stderr=stderr,
                                   reason="Shell exited 0")
    if expect.lower() == "false":
        return PostconditionResult(passed=False, stdout=stdout, stderr=stderr,
                                   reason="Shell exited 0 but expected failure")

    # JSON path comparisons: $.field >= value, $.field == value, etc.
    # Try to parse stdout as JSON
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return PostconditionResult(
            passed=False,
            stdout=stdout,
            stderr=stderr,
            reason="stdout is not valid JSON",
        )

    # Split by || for OR expressions, then && for AND within each OR group
    or_parts = [p.strip() for p in expect.split("||")]

    for or_part in or_parts:
        and_conditions = [c.strip() for c in or_part.split("&&")]
        if all(_evaluate_single(cond, data) for cond in and_conditions):
            return PostconditionResult(
                passed=True,
                stdout=stdout,
                stderr=stderr,
                reason="All conditions passed",
            )

    # Build actual value summary for failure message
    actual_summary = ""
    try:
        import json as _json
        actual_summary = " (actual: " + _json.dumps(data, ensure_ascii=False)[:200] + ")"
    except Exception:
        pass

    return PostconditionResult(
        passed=False,
        stdout=stdout,
        stderr=stderr,
        reason=f"Condition failed: {expect}{actual_summary}",
    )


def _evaluate_single(cond: str, data: dict) -> bool:
    """Evaluate a single comparison condition like $.line >= 80."""
    # Match: $.field OP value
    match = re.match(r"\$\.(\w+)\s*(>=|<=|==|!=|>|<)\s*(.+)", cond.strip())
    if not match:
        return False

    field_name = match.group(1)
    operator = match.group(2)
    raw_value = match.group(3).strip()

    # Get field value from data
    if field_name not in data:
        return False
    actual = data[field_name]

    # Parse expected value (int or float or bool/null or string)
    if raw_value.lower() == "true":
        expected = True
    elif raw_value.lower() == "false":
        expected = False
    elif raw_value.lower() == "null" or raw_value.lower() == "none":
        expected = None
    else:
        try:
            expected = int(raw_value)
        except ValueError:
            try:
                expected = float(raw_value)
            except ValueError:
                expected = raw_value.strip("'\"")

    # Compare — guard against type mismatch (None, mixed types)
    try:
        if operator == ">=":
            return actual >= expected
        elif operator == "<=":
            return actual <= expected
        elif operator == "==":
            return actual == expected
        elif operator == "!=":
            return actual != expected
        elif operator == ">":
            return actual > expected
        elif operator == "<":
            return actual < expected
    except TypeError:
        return False

    return False
