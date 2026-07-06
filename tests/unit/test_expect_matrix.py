"""P0-3: expect branch complete coverage matrix."""
import pytest
from cc_pipeline.postcondition import evaluate, PostconditionResult
import tempfile, os


def _run_postcondition(shell_cmd, expect, cwd=None):
    """Helper: run shell command and evaluate expect."""
    if cwd is None:
        cwd = tempfile.mkdtemp()
    return evaluate(shell=shell_cmd, expect=expect, cwd=cwd)


class TestExpectTrueFalse:
    """expect: 'true' and 'false' × exit 0/1."""

    def test_true_exit0(self):
        r = _run_postcondition("true", "true")
        assert r.passed is True

    def test_true_exit1(self):
        r = _run_postcondition("false", "true")
        assert r.passed is False

    def test_false_exit0(self):
        r = _run_postcondition("true", "false")
        assert r.passed is False

    def test_false_exit1(self):
        r = _run_postcondition("false", "false")
        assert r.passed is False  # shell failed, postcondition fails regardless

    def test_true_empty_stdout(self):
        """expect 'true' with empty stdout → pass (exit 0)."""
        r = _run_postcondition("true", "true")
        assert r.passed is True


class TestExpectContains:
    """expect: contains('text') × found/not found."""

    def test_contains_found(self):
        r = _run_postcondition("echo 'hello world'", "contains('hello')")
        assert r.passed is True

    def test_contains_not_found(self):
        r = _run_postcondition("echo 'goodbye'", "contains('hello')")
        assert r.passed is False

    def test_contains_empty_stdout(self):
        r = _run_postcondition("true", "contains('anything')")
        assert r.passed is False

    def test_contains_double_quotes(self):
        r = _run_postcondition('echo "passed"', 'contains("passed")')
        assert r.passed is True


class TestExpectJson:
    """expect: $.field >= value × valid/invalid JSON."""

    def test_json_ge_pass(self):
        r = _run_postcondition("echo '{\"score\": 75}'", "$.score >= 60")
        assert r.passed is True

    def test_json_ge_fail(self):
        r = _run_postcondition("echo '{\"score\": 45}'", "$.score >= 60")
        assert r.passed is False

    def test_json_invalid_output(self):
        """Non-JSON stdout → fail."""
        r = _run_postcondition("echo 'not json'", "$.score >= 60")
        assert r.passed is False

    def test_json_empty_stdout(self):
        r = _run_postcondition("true", "$.score >= 60")
        assert r.passed is False

    def test_json_eq(self):
        r = _run_postcondition("echo '{\"status\": \"done\"}'", '$.status == "done"')
        assert r.passed is True


class TestExpectOr:
    """expect: $.a >= 70 || $.b >= 80"""

    def test_or_both_pass(self):
        r = _run_postcondition("echo '{\"a\": 80, \"b\": 90}'", "$.a >= 70 || $.b >= 80")
        assert r.passed is True

    def test_or_first_pass(self):
        r = _run_postcondition("echo '{\"a\": 75, \"b\": 50}'", "$.a >= 70 || $.b >= 80")
        assert r.passed is True

    def test_or_second_pass(self):
        r = _run_postcondition("echo '{\"a\": 50, \"b\": 85}'", "$.a >= 70 || $.b >= 80")
        assert r.passed is True

    def test_or_both_fail(self):
        r = _run_postcondition("echo '{\"a\": 50, \"b\": 50}'", "$.a >= 70 || $.b >= 80")
        assert r.passed is False


class TestExpectNone:
    """expect: None × exit 0/1."""

    def test_none_exit0(self):
        r = _run_postcondition("echo 'anything'", None)
        assert r.passed is True

    def test_none_exit1(self):
        r = _run_postcondition("false", None)
        assert r.passed is False

    def test_none_empty_stdout(self):
        r = _run_postcondition("true", None)
        assert r.passed is True


class TestShellFailure:
    """Shell exits non-zero → postcondition fails regardless of expect."""

    def test_shell_fail_with_true(self):
        r = _run_postcondition("exit 1", "true")
        assert r.passed is False

    def test_shell_fail_with_contains(self):
        r = _run_postcondition("echo 'hello' && exit 1", "contains('hello')")
        assert r.passed is False

    def test_shell_fail_with_none(self):
        r = _run_postcondition("exit 1", None)
        assert r.passed is False
