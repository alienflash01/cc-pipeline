"""TDD: Variable Renderer tests."""
import pytest
import json


class TestVariableRenderer:
    """Test prompt template variable substitution."""

    def test_replaces_simple_variable(self):
        """{module} is replaced."""
        from cc_pipeline.render import render
        assert render("hello {module}", {"module": "auth"}) == "hello auth"

    def test_replaces_multiple_variables(self):
        """Multiple variables replaced at once."""
        from cc_pipeline.render import render
        result = render(
            "Module: {module}, File: {file}, Dir: {source_dir}",
            {"module": "auth", "file": "auth_login.c", "source_dir": "src/auth/"},
        )
        assert result == "Module: auth, File: auth_login.c, Dir: src/auth/"

    def test_replaces_integer_variable(self):
        """Integer variables work."""
        from cc_pipeline.render import render
        assert render("threshold: {line_threshold}", {"line_threshold": 80}) == "threshold: 80"

    def test_injects_json_file_content(self, tmp_path):
        """{.pipeline/xxx.json} reads file content."""
        from cc_pipeline.render import render
        pipeline_dir = tmp_path / ".pipeline"
        pipeline_dir.mkdir()
        data_file = pipeline_dir / "scaffold.json"
        data_file.write_text(json.dumps({"files_created": ["test_auth.c"]}))
        
        result = render(
            "Context: {.pipeline/scaffold.json}",
            {},
            base_dir=str(tmp_path),
        )
        assert "test_auth.c" in result

    def test_injects_nested_json_path(self, tmp_path):
        """{.pipeline/verified/generate.json} resolves nested path."""
        from cc_pipeline.render import render
        nested_dir = tmp_path / ".pipeline" / "verified"
        nested_dir.mkdir(parents=True)
        (nested_dir / "generate.json").write_text(json.dumps({"line": 85}))
        
        result = render(
            "Coverage: {.pipeline/verified/generate.json}",
            {},
            base_dir=str(tmp_path),
        )
        assert "85" in result

    def test_missing_file_injects_placeholder(self, tmp_path):
        """Missing JSON file injects a clear placeholder."""
        from cc_pipeline.render import render
        result = render(
            "Data: {.pipeline/nonexistent.json}",
            {},
            base_dir=str(tmp_path),
        )
        assert "not found" in result.lower() or "missing" in result.lower() or "unavailable" in result.lower()

    def test_unknown_variable_raises(self):
        """Unknown variable raises KeyError."""
        from cc_pipeline.render import render
        with pytest.raises(KeyError):
            render("hello {unknown_var}", {})

    def test_no_variables_returns_unchanged(self):
        """Text without variables returns unchanged."""
        from cc_pipeline.render import render
        assert render("no vars here", {}) == "no vars here"

    def test_coverage_threshold_from_dict(self):
        """Coverage dict values are accessible."""
        from cc_pipeline.render import render
        result = render(
            "Line: {line_threshold}",
            {"line_threshold": 85},
        )
        assert result == "Line: 85"

    def test_module_variables_merged(self):
        """Module-level variables are available in render."""
        from cc_pipeline.render import render
        variables = {"module": "auth", "mock_strategy": "link-time"}
        result = render("Using {mock_strategy} for {module}", variables)
        assert result == "Using link-time for auth"
