"""TDD: chaos / fuzz inputs — malformed YAML must raise ValueError.

Every malformed config must be rejected with a ``ValueError`` (a friendly,
explained failure) — never a bare ``KeyError`` / ``TypeError`` / ``AttributeError``
stack trace. Where the loader currently leaks a raw exception, ``config.py``
(or the compiler) gains validation to convert it.

Each test writes YAML to a temp file and calls ``load_config``.
"""
import pytest


def _write(tmp_path, body, name="config.yaml"):
    """Write ``body`` to tmp_path/name and return the path."""
    p = tmp_path / name
    p.write_text(body)
    return p


# ---------------------------------------------------------------------------
# source_files shape
# ---------------------------------------------------------------------------


class TestChaosSourceFiles:
    def test_source_files_int_rejected(self, tmp_path):
        # 1. source_files is an integer (123) → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n"
                     "    source_files: 123\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_source_files_string_rejected(self, tmp_path):
        # 2. source_files is a scalar string ('a.c') → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n"
                     "    source_files: a.c\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))


# ---------------------------------------------------------------------------
# module name
# ---------------------------------------------------------------------------


class TestChaosModuleName:
    def test_module_name_special_chars_rejected(self, tmp_path):
        # 3. module.name contains !@# → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: a!@#\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_module_name_empty_rejected(self, tmp_path):
        # 4. module.name empty string → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: ''\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_duplicate_module_name_rejected(self, tmp_path):
        # 16. duplicate module name → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))


# ---------------------------------------------------------------------------
# numeric fields
# ---------------------------------------------------------------------------


class TestChaosNumeric:
    def test_concurrency_float_rejected(self, tmp_path):
        # 5. concurrency is 3.5 → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "concurrency: 3.5\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_max_retries_string_rejected(self, tmp_path):
        # 6. max_retries is 'abc' → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "max_retries: abc\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_timeout_float_rejected(self, tmp_path):
        # 14. timeout is 1.5 → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "    timeout: 1.5\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))


# ---------------------------------------------------------------------------
# structural emptiness
# ---------------------------------------------------------------------------


class TestChaosEmptyStructure:
    def test_empty_pipeline_rejected(self, tmp_path):
        # 7. pipeline empty [] → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline: []\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_empty_modules_rejected(self, tmp_path):
        # 8. modules empty [] → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "modules: []\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_empty_yaml_rejected(self, tmp_path):
        # 11. YAML empty file → ValueError
        cfg = _write(tmp_path, "")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_comment_only_yaml_rejected(self, tmp_path):
        # 12. YAML only comments → ValueError
        cfg = _write(tmp_path, "# just a comment\n# another\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))


# ---------------------------------------------------------------------------
# step structure
# ---------------------------------------------------------------------------


class TestChaosStepStructure:
    def test_step_missing_id_rejected(self, tmp_path):
        # 9. step missing id → ValueError (NOT a bare KeyError)
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - executor: claude-code\n"  # no id
                     "    prompt: hi\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_output_path_traversal_rejected(self, tmp_path):
        # 13. output contains '..' → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "    output: ../etc/passwd\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_on_failure_dangling_rejected(self, tmp_path):
        # 15. on_failure points at a non-existent step → ValueError
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: review\n"
                     "    executor: claude-code\n"
                     "    prompt: hi\n"
                     "    on_failure: nonexistent\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.raises(ValueError):
            load_config(str(cfg))

    def test_prompt_and_prompt_file_both_warns(self, tmp_path):
        # 10. prompt + prompt_file together → UserWarning
        (tmp_path / "p.md").write_text("body")
        cfg = _write(tmp_path,
                     "repo: .\n"
                     "pipeline:\n"
                     "  - id: s\n"
                     "    executor: claude-code\n"
                     "    prompt: inline\n"
                     "    prompt_file: p.md\n"
                     "modules:\n"
                     "  - name: auth\n"
                     "    source_dir: src/\n")
        from cc_pipeline.config import load_config
        with pytest.warns(UserWarning, match="both prompt and prompt_file"):
            load_config(str(cfg))
