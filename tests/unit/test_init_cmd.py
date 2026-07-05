"""TDD: cc-pipeline init — interactive config generator.

Strict TDD: these tests are written BEFORE the implementation. They drive
the parser wiring (``init`` subcommand) and the ``_cmd_init`` handler that
walks the user through a short interactive dialog and writes a runnable
config + prompt files.

Design notes reflected here:
  * ``config.yaml`` has the *collected-value* placeholders substituted
    (``{repo_path}``, ``{concurrency}``, ``{first_module}``, ``{source_dir}``,
    ``{assert_macro}``) so the file is valid YAML that loads + dry-runs.
  * Prompt files are written verbatim — their ``{var}`` are render variables
    consumed by cc-pipeline at runtime, so they must survive as literal text.
"""
import pytest
from unittest.mock import patch


def _args(output_dir, template=None):
    """Build a lightweight args namespace matching the init subparser."""
    return type("Args", (), {"template": template, "output_dir": output_dir})()


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


class TestInitParser:
    """The ``init`` subcommand is registered with the right options."""

    def test_init_parser_exists(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["init"])
        assert args.command == "init"

    def test_init_parser_template_default_none(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["init"])
        assert args.template is None

    def test_init_parser_output_dir_default_dot(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["init"])
        assert args.output_dir == "."

    def test_init_parser_accepts_template(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["init", "--template", "ut"])
        assert args.template == "ut"

    def test_init_parser_accepts_output_dir(self):
        from cc_pipeline.cli import _build_parser

        args = _build_parser().parse_args(["init", "--output-dir", "generated/"])
        assert args.output_dir == "generated/"


# ---------------------------------------------------------------------------
# Task type 1 — UT generation
# ---------------------------------------------------------------------------


class TestInitUT:
    """``1=UT生成`` writes the scaffold/generate/evaluate pipeline."""

    def test_init_ut_generates_config_and_prompts(self, tmp_path):
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            ret = _cmd_init(_args(str(tmp_path)))
        assert ret == 0

        cfg = tmp_path / "config.yaml"
        assert cfg.exists()
        text = cfg.read_text()
        assert "repo: ." in text
        assert "concurrency: 5" in text
        assert "name: auth" in text
        assert "source_dir: src/" in text
        assert "assert_macro: CHECK" in text
        assert "id: scaffold" in text
        assert "id: generate" in text
        assert "id: evaluate" in text
        assert "on_failure: generate" in text
        # all three prompt files written
        for p in ("scaffold.md", "generate.md", "evaluate.md"):
            assert (tmp_path / "prompts" / p).exists(), f"missing {p}"

    def test_init_ut_prompt_keeps_render_vars(self, tmp_path):
        """Prompt {var} are render variables — they must stay literal."""
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))

        gen = (tmp_path / "prompts" / "generate.md").read_text()
        assert "{source_dir}" in gen
        assert "{file}" in gen
        assert "{assert_macro}" in gen

        scaff = (tmp_path / "prompts" / "scaffold.md").read_text()
        assert "{module}" in scaff

    def test_init_uses_defaults_on_empty_input(self, tmp_path):
        """Blank answers fall back to the documented defaults."""
        from cc_pipeline.cli import _cmd_init

        with patch("builtins.input", side_effect=["", "", "", "", "", ""]):
            _cmd_init(_args(str(tmp_path)))
        text = (tmp_path / "config.yaml").read_text()
        assert "repo: ." in text
        assert "concurrency: 5" in text
        assert "name: auth" in text
        assert "source_dir: src/" in text
        assert "assert_macro: CHECK" in text

    def test_init_generated_ut_config_loads_and_dry_runs(self, tmp_path):
        """The generated UT config is valid: loads + compiles cleanly."""
        from cc_pipeline.cli import _cmd_init, _do_dry_run
        from cc_pipeline.config import load_config

        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))

        cfg_path = tmp_path / "config.yaml"
        cfg = load_config(str(cfg_path))  # must not raise
        assert cfg.modules[0].name == "auth"
        # dry-run must succeed (exit 0)
        assert _do_dry_run(cfg, str(cfg_path)) == 0

    def test_init_first_module_taken_from_comma_list(self, tmp_path):
        """``first_module`` is the first entry of the comma-separated list."""
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "1", "src/", "auth,core,billing", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        text = (tmp_path / "config.yaml").read_text()
        assert "name: auth" in text
        # siblings are not turned into separate modules (template is single-module)
        assert "name: core" not in text


# ---------------------------------------------------------------------------
# Output / UX
# ---------------------------------------------------------------------------


class TestInitOutput:
    def test_init_prints_header(self, tmp_path, capsys):
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        out = capsys.readouterr().out
        assert "🧩" in out
        assert "✅" in out

    def test_init_template_flag_prints_note(self, tmp_path, capsys):
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path), template="ut"))
        out = capsys.readouterr().out
        assert "--template not yet supported" in out

    def test_init_lists_generated_files_and_run_hint(self, tmp_path, capsys):
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        out = capsys.readouterr().out
        assert "config.yaml" in out
        assert "cc-pipeline run config.yaml --dry-run" in out

    def test_init_writes_to_output_dir(self, tmp_path):
        from cc_pipeline.cli import _cmd_init

        out_dir = tmp_path / "gen"
        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(out_dir)))
        assert (out_dir / "config.yaml").exists()
        assert (out_dir / "prompts" / "scaffold.md").exists()


# ---------------------------------------------------------------------------
# Task type 2 — code review
# ---------------------------------------------------------------------------


class TestInitReview:
    def test_init_review_generates_config_and_prompt(self, tmp_path):
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "2", "src/", "auth", "安全性", "3"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        text = (tmp_path / "config.yaml").read_text()
        assert "id: review" in text
        assert "concurrency: 3" in text
        assert "name: auth" in text
        assert "source_dir: src/" in text
        assert (tmp_path / "prompts" / "review.md").exists()

    def test_init_review_prompt_keeps_render_vars(self, tmp_path):
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "2", "src/", "auth", "安全性", "3"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        rev = (tmp_path / "prompts" / "review.md").read_text()
        assert "{module}" in rev

    def test_init_review_config_is_valid(self, tmp_path):
        from cc_pipeline.cli import _cmd_init
        from cc_pipeline.config import load_config

        inputs = [".", "2", "src/", "auth", "安全性", "3"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        cfg = load_config(str(tmp_path / "config.yaml"))
        assert cfg.modules[0].name == "auth"


# ---------------------------------------------------------------------------
# Task type 3 — custom
# ---------------------------------------------------------------------------


class TestInitCustom:
    def test_init_custom_generates_config_and_prompt(self, tmp_path):
        from cc_pipeline.cli import _cmd_init

        inputs = [".", "3", "src/", "core,api", "2"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        text = (tmp_path / "config.yaml").read_text()
        assert "id: step1" in text
        assert "concurrency: 2" in text
        assert "name: core" in text
        assert (tmp_path / "prompts" / "step1.md").exists()

    def test_init_custom_config_is_valid(self, tmp_path):
        from cc_pipeline.cli import _cmd_init
        from cc_pipeline.config import load_config

        inputs = [".", "3", "src/", "core", "2"]
        with patch("builtins.input", side_effect=inputs):
            _cmd_init(_args(str(tmp_path)))
        cfg = load_config(str(tmp_path / "config.yaml"))
        assert cfg.modules[0].name == "core"


# ---------------------------------------------------------------------------
# Dispatch through main()
# ---------------------------------------------------------------------------


class TestInitDispatch:
    def test_main_init_dispatches_to_cmd_init(self, tmp_path):
        from cc_pipeline.cli import main

        inputs = [".", "1", "src/", "auth", "CHECK", "5"]
        with patch("builtins.input", side_effect=inputs):
            ret = main(["init", "--output-dir", str(tmp_path)])
        assert ret == 0
        assert (tmp_path / "config.yaml").exists()
