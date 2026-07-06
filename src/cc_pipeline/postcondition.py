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


def evaluate(
    shell: str,
    expect: str | None,
    cwd: str,
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
    result = subprocess.run(
        shell,
        shell=True,
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
    )

    # Decode safely (handle binary output)
    stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else (result.stdout or "")
    stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else (result.stderr or "")

    # Shell failed → postcondition fails
    if result.returncode != 0:
        return PostconditionResult(
            passed=False,
            stdout=stdout,
            stderr=stderr,
            reason=f"Shell command exited with code {result.returncode}",
        )

    # No expect → pass (shell exited 0)
    if expect is None:
        return PostconditionResult(
            passed=True,
            stdout=stdout,
            stderr=stderr,
        )

    # Parse expect expression
    return _evaluate_expect(expect, stdout, stderr)


def _evaluate_expect(expect: str, stdout: str, stderr: str) -> PostconditionResult:
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

    return PostconditionResult(
        passed=False,
        stdout=stdout,
        stderr=stderr,
        reason=f"Condition failed: {expect}",
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
