"""Pytest fixtures for cc-pipeline tests."""
import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def tmp_yaml(tmp_path):
    """Create a temporary YAML config file, return its path."""
    def _make(content: str, name: str = "config.yaml") -> Path:
        p = tmp_path / name
        p.write_text(content)
        return p
    return _make


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary git repo with a simple source file."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    src_dir = repo / "src"
    src_dir.mkdir()
    (src_dir / "math.c").write_text("int add(int a, int b) { return a + b; }\n")
    (repo / "README.md").write_text("# test repo")
    
    import subprocess
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"},
    )
    return repo
