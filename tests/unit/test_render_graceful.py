"""TDD: render unknown variables — keep original + warn user."""
import pytest
import logging
from cc_pipeline.render import render


class TestUnknownVariableGraceful:
    """Unknown {var} should be preserved as-is, not crash."""

    def test_unknown_var_preserved(self):
        """{unknown} with no matching variable → keep {unknown} in output."""
        result = render("hello {unknown_var}", {"known": "x"})
        assert "{unknown_var}" in result

    def test_unknown_var_does_not_crash(self):
        """Unknown variable should not raise KeyError."""
        result = render("code: if (1) { printf('hi'); }", {})
        assert "printf" in result

    def test_known_var_still_replaced(self):
        """Known variables still get replaced."""
        result = render("{module} test", {"module": "auth"})
        assert result == "auth test"

    def test_c_code_braces_preserved(self):
        """C code braces in prompt are not eaten."""
        result = render(
            "write: if (x > 0) { return 1; }",
            {"module": "auth"}
        )
        assert "{ return 1; }" in result

    def test_unknown_var_logs_warning(self, caplog):
        """Unknown variable should log a warning."""
        with caplog.at_level(logging.WARNING):
            render("{unknown_var}", {"known": "x"})
        assert any("unknown_var" in r.message for r in caplog.records)

    def test_mixed_known_and_unknown(self):
        """Mix of known and unknown in same string."""
        result = render("{module} {unknown} {file}", {"module": "auth", "file": "a.c"})
        assert "auth" in result
        assert "a.c" in result
        assert "{unknown}" in result

    def test_empty_braces_preserved(self):
        """Empty braces {} should be preserved (C code style)."""
        result = render("func() {}", {})
        assert "{}" in result
