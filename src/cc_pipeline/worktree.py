"""Worktree Manager — git worktree lifecycle for isolated module pipelines."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class WorktreeManager:
    """Manages git worktrees for parallel module pipelines."""

    def __init__(
        self,
        repo_path: str,
        base_branch: str = "main",
        worktree_root: str | None = None,
        branch_prefix: str = "ut-auto",
    ):
        self.repo_path = Path(repo_path)
        self.base_branch = base_branch
        self.worktree_root = Path(worktree_root) if worktree_root else Path(tempfile.gettempdir()) / "cc-pipeline-worktrees"
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self.branch_prefix = branch_prefix
        self._worktrees: dict[str, str] = {}  # module_name → path

    def create(self, module_name: str) -> str:
        """Create a worktree for a module.

        Args:
            module_name: Module name (used for branch + path naming).

        Returns:
            Absolute path to the worktree directory.
        """
        branch = f"{self.branch_prefix}/{module_name}"
        wt_path = self.worktree_root / module_name

        # Remove if exists
        if wt_path.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=self.repo_path, capture_output=True,
            )

        # Create worktree with a new branch from base
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, str(wt_path), self.base_branch],
            cwd=self.repo_path, capture_output=True, check=True,
        )

        self._worktrees[module_name] = str(wt_path)
        return str(wt_path)

    def cleanup(self, module_name: str) -> None:
        """Remove a worktree after successful completion."""
        wt_path = self._worktrees.get(module_name)
        if wt_path is None:
            return

        subprocess.run(
            ["git", "worktree", "remove", "--force", wt_path],
            cwd=self.repo_path, capture_output=True,
        )

        # Also delete the branch
        branch = f"{self.branch_prefix}/{module_name}"
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=self.repo_path, capture_output=True,
        )

        self._worktrees.pop(module_name, None)

    def preserve(self, module_name: str) -> None:
        """Keep the worktree for failure analysis (no cleanup)."""
        # Just don't call cleanup — leave it as-is
        pass

    def get_path(self, module_name: str) -> str | None:
        """Get the worktree path for a module."""
        return self._worktrees.get(module_name)
