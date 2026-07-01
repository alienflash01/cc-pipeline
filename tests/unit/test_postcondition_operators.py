"""Tests for postcondition operator coverage: >, <, invalid expressions."""
import pytest
from cc_pipeline.postcondition import _evaluate_single


class TestPostconditionOperators:
    """Cover all comparison operators and edge cases."""

    def test_greater_than_true(self):
        assert _evaluate_single("$.score > 50", {"score": 75}) is True

    def test_greater_than_false(self):
        assert _evaluate_single("$.score > 50", {"score": 50}) is False

    def test_greater_than_float(self):
        assert _evaluate_single("$.density > 2.0", {"density": 2.5}) is True

    def test_less_than_true(self):
        assert _evaluate_single("$.errors < 5", {"errors": 3}) is True

    def test_less_than_false(self):
        assert _evaluate_single("$.errors < 5", {"errors": 5}) is False

    def test_less_than_equal_boundary(self):
        assert _evaluate_single("$.errors < 5", {"errors": 5}) is False

    def test_invalid_expression_returns_false(self):
        """Malformed expect → returns False (postcondition fails)."""
        assert _evaluate_single("not a valid expression", {"x": 1}) is False

    def test_empty_expression_returns_false(self):
        assert _evaluate_single("", {"x": 1}) is False

    def test_missing_field_returns_false(self):
        """$.nonexistent >= 80 → field missing → False."""
        assert _evaluate_single("$.nonexistent >= 80", {"other": 1}) is False

    def test_field_none_value(self):
        """$.field == null type edge."""
        data = {"field": None}
        # None compared with >= should be False
        assert _evaluate_single("$.field >= 80", data) is False

    def test_greater_equal_with_string_value(self):
        """$.name >= 'abc' with string → string comparison."""
        assert _evaluate_single('$.name >= "abc"', {"name": "bcd"}) is True

    def test_negative_number_comparison(self):
        assert _evaluate_single("$.temp > -10", {"temp": -5}) is True

    def test_zero_comparison(self):
        assert _evaluate_single("$.count > 0", {"count": 0}) is False
        assert _evaluate_single("$.count >= 0", {"count": 0}) is True
