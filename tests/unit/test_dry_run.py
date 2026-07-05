"""TDD: --dry-run config preview — show what would run without executing CC.

`cc-pipeline run config.yaml --dry-run` compiles each module's pipeline and
prints the step list, per-module file table, and estimated CC call count, then
exits without creating a run_dir / worktree or invoking Claude Code.
"""
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com",
}


def _head_branch(repo) -> str:
    """Return the current branch name of a repo (master/main agnostic)."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo), capture_output=True, text=True,
    )
    return r.stdout.strip() or "master"


@pytest.fixture
def git_repo(tmp_path):
    """Minimal git repo with an initial commit, for preflight checks."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, env=GIT_ENV)
    return repo


def _write_config(git_repo, *, model="glm-4.6") -> Path:
    """Write the canonical example config (scaffold → generate(per_file) → evaluate)."""
    cfg = git_repo.parent / "config.yaml"
    cfg.write_text(f"""
repo: {git_repo}
base_branch: {_head_branch(git_repo)}
model: {model}
pipeline:
  - id: scaffold
    executor: shell
    command: "echo scaffold"
  - id: generate
    executor: claude-code
    prompt: "generate for {{file}}"
    loop: per_file
  - id: evaluate
    executor: claude-code
    prompt: "evaluate"
modules:
  - name: auth
    spec_id: SPEC-AUTH
    source_dir: src/auth/
    source_files:
      - path: auth_login
        assert_macro: CHECK
        spec_id: SPEC-001
      - path: auth_token
        assert_macro: REQUIRE
        spec_id: SPEC-002
""")
    return cfg


# --- Task 1: CLI flag --------------------------------------------------------

class TestDryRunFlag:
    """--dry-run is a store_true flag, default False."""

    def test_flag_defaults_false(self):
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["run", "config.yaml"])
        assert args.dry_run is False

    def test_flag_sets_true(self):
        from cc_pipeline.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["run", "config.yaml", "--dry-run"])
        assert args.dry_run is True


# --- Task 2-4: preview output ------------------------------------------------

class TestDryRunOutput:
    """dry-run prints steps, file table, and estimated CC calls."""

    def test_output_has_step_list(self, git_repo, capsys):
        from cc_pipeline.cli import main
        cfg = _write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            ret = main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        out = capsys.readouterr().out
        assert ret == 0
        # all three steps named
        assert "scaffold" in out
        assert "generate" in out
        assert "evaluate" in out
        # joined by arrows, per_file annotated
        assert "→" in out
        assert "per_file" in out
        assert "Preview" in out  # the title

    def test_output_has_file_table(self, git_repo, capsys):
        from cc_pipeline.cli import main
        cfg = _write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        out = capsys.readouterr().out
        # module header with file count
        assert "Module: auth" in out
        assert "2 files" in out
        # both files appear
        assert "auth_login" in out
        assert "auth_token" in out
        # dict-key columns appear (assert_macro values + spec_id values)
        assert "CHECK" in out
        assert "REQUIRE" in out
        assert "SPEC-001" in out
        assert "SPEC-002" in out
        # box-drawing table border
        assert "│" in out
        assert "File" in out

    def test_output_has_estimate(self, git_repo, capsys):
        from cc_pipeline.cli import main
        cfg = _write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        out = capsys.readouterr().out
        # estimated total + per-step breakdown
        assert "CC calls" in out
        # scaffold=1 (1 module, non-loop), generate=2 (per_file, 2 files), evaluate=1
        assert "scaffold=1" in out
        assert "generate=2" in out
        assert "evaluate=1" in out
        # grand total = 1 + 2 + 1
        assert "4 CC calls" in out

    def test_output_has_valid_marker(self, git_repo, capsys):
        from cc_pipeline.cli import main
        cfg = _write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        out = capsys.readouterr().out
        assert "Config valid" in out
        assert "--dry-run" in out  # the "run without --dry-run" hint

    def test_string_source_files_show_file_column_only(self, git_repo, tmp_path, capsys):
        """Plain string source_files → only a File column (no extra dict keys)."""
        from cc_pipeline.cli import main
        cfg = git_repo.parent / "config.yaml"
        cfg.write_text(f"""
repo: {git_repo}
base_branch: {_head_branch(git_repo)}
pipeline:
  - id: gen
    executor: shell
    command: "echo {{file}}"
    loop: per_file
modules:
  - name: core
    source_dir: src/
    source_files: [alpha.c, beta.c]
""")
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        out = capsys.readouterr().out
        assert "Module: core" in out
        assert "alpha.c" in out
        assert "beta.c" in out
        assert "File" in out


# --- Task 5: no execution ----------------------------------------------------

class TestDryRunNoExecution:
    """dry-run never creates run_dir / worktree / invokes CC."""

    def test_no_run_dir_created(self, git_repo, capsys):
        from cc_pipeline.cli import main
        cfg = _write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"):
            ret = main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        assert ret == 0
        # the run_dir must NOT be created
        assert not runs.exists()

    def test_orchestrator_not_invoked(self, git_repo, capsys):
        from cc_pipeline.cli import main
        cfg = _write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"), \
             patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            ret = main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        assert ret == 0
        MockOrch.assert_not_called()

    def test_dry_run_takes_precedence_over_verbose(self, git_repo, capsys):
        """--dry-run + --verbose coexist; dry-run wins (no execution)."""
        from cc_pipeline.cli import main
        cfg = _write_config(git_repo)
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"), \
             patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            ret = main(["run", str(cfg), "--dry-run", "--verbose", "--run-dir", str(runs)])
        assert ret == 0
        MockOrch.assert_not_called()
        assert not runs.exists()
        # still produced a preview
        assert "Preview" in capsys.readouterr().out


# --- Task 6: error handling --------------------------------------------------

class TestDryRunErrors:
    """Compile-time config errors return 1 without executing."""

    def test_compile_error_returns_1(self, git_repo, capsys):
        """A config that loads but fails to compile → return 1, no execution."""
        from cc_pipeline.cli import main
        # dangling depends_on: loads fine, fails at compile (sort_by_dependencies)
        cfg = git_repo.parent / "config.yaml"
        cfg.write_text(f"""
repo: {git_repo}
base_branch: {_head_branch(git_repo)}
pipeline:
  - id: gen
    executor: shell
    command: "echo ok"
    depends_on: ghost
modules:
  - name: auth
    source_dir: src/
    source_files: [a.c]
""")
        runs = git_repo.parent / "runs"
        with patch("cc_pipeline.cli.shutil.which", return_value="/fake/claude"), \
             patch("cc_pipeline.orchestrator.Orchestrator") as MockOrch:
            ret = main(["run", str(cfg), "--dry-run", "--run-dir", str(runs)])
        assert ret == 1
        MockOrch.assert_not_called()
        captured = capsys.readouterr()
        # friendly error message reaches stderr (or stdout)
        assert "ghost" in captured.err or "ghost" in captured.out
