"""Glob source_files tests — support *.c patterns."""
import pytest
import os, tempfile
from pathlib import Path
from cc_pipeline.config import load_config


def _make_project(tmp_path, files: list[str], extra_config: str = ""):
    """Create a mini project with given files and config."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for fname in files:
        (src_dir / fname).write_text("// content")
    
    # git init
    import subprocess
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    
    config_content = f"""repo: {tmp_path}
base_branch: main
concurrency: 1
{extra_config}
pipeline:
  - id: check
    executor: shell
    prompt: echo ok
modules:
  - name: test
    source_dir: src/
    source_files: ["*.c"]
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return config_path


class TestGlobSourceFiles:
    """source_files glob pattern expansion."""
    
    def test_simple_glob(self, tmp_path):
        """source_files: ['*.c'] with 3 .c files → expands to 3 files."""
        config_path = _make_project(tmp_path, ["a.c", "b.c", "z.c"])
        config = load_config(str(config_path))
        files = config.modules[0].source_files
        assert len(files) == 3
        assert "a.c" in files
        assert "b.c" in files
        assert "z.c" in files
    
    def test_glob_sorted(self, tmp_path):
        """Glob expansion is sorted for stable ordering."""
        config_path = _make_project(tmp_path, ["c.c", "a.c", "b.c"])
        config = load_config(str(config_path))
        files = config.modules[0].source_files
        assert files == ["a.c", "b.c", "c.c"]
    
    def test_glob_no_match(self, tmp_path):
        """Glob with no matching files → empty list."""
        config_path = _make_project(tmp_path, ["a.h", "b.h"])
        config = load_config(str(config_path))
        assert config.modules[0].source_files == []
    
    def test_mixed_glob_and_dict(self, tmp_path):
        """Mixed: ['*.c', {path: special.h, assert_macro: CHECK}]."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        for fname in ["a.c", "b.c", "special.h"]:
            (src_dir / fname).write_text("// content")
        
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        
        config_content = f"""repo: {tmp_path}
base_branch: main
concurrency: 1
pipeline:
  - id: check
    executor: shell
    prompt: echo ok
modules:
  - name: test
    source_dir: src/
    source_files:
      - "*.c"
      - path: special.h
        assert_macro: CHECK
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)
        
        config = load_config(str(config_path))
        files = config.modules[0].source_files
        # 2 .c files expanded + 1 dict entry
        assert len(files) == 3
        # dict entries kept as-is
        dict_entries = [f for f in files if isinstance(f, dict)]
        assert len(dict_entries) == 1
        assert dict_entries[0]["path"] == "special.h"
        assert dict_entries[0]["assert_macro"] == "CHECK"
        # glob entries expanded
        str_entries = [f for f in files if isinstance(f, str)]
        assert set(str_entries) == {"a.c", "b.c"}
    
    def test_dict_with_glob_path(self, tmp_path):
        """Dict with pattern in path key: {path: '*.c', assert_macro: X}."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        for fname in ["a.c", "b.c", "c.c"]:
            (src_dir / fname).write_text("// content")
        
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        
        config_content = f"""repo: {tmp_path}
base_branch: main
concurrency: 1
pipeline:
  - id: generate
    executor: shell
    loop: per_file
    prompt: echo generate
modules:
  - name: test
    source_dir: src/
    source_files:
      - path: "*.c"
        assert_macro: CHECK
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)
        
        config = load_config(str(config_path))
        files = config.modules[0].source_files
        # *.c should expand into 3 dict entries, all with assert_macro: CHECK
        assert len(files) == 3
        for f in files:
            assert isinstance(f, dict)
            assert "path" in f
            assert f["assert_macro"] == "CHECK"
            assert f["path"].endswith(".c")
