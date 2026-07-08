"""Worktree Manager — git worktree lifecycle for isolated module pipelines."""
from __future__ import annotations

import subprocess
import tempfile
import threading
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
        self._lock = threading.Lock()  # serialize git worktree operations
        self._worktrees: dict[str, str] = {}  # module_name → path

    def create(self, module_name: str, from_ref: str | None = None) -> str:
        """Create a worktree for a module.

        Args:
            module_name: Module name (used for branch + path naming).
            from_ref: Git ref (tag/commit/branch) to create worktree from.
                      If None, uses base_branch. Used by resume to restore
                      from latest checkpoint.

        Returns:
            Absolute path to the worktree directory.
        """
        branch = f"{self.branch_prefix}/{module_name}"
        wt_path = self.worktree_root / module_name

        with self._lock:
            # Prune stale worktree references first
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=self.repo_path, capture_output=True,
            )

            # Remove if worktree directory exists (in our run_dir)
            if wt_path.exists():
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(wt_path)],
                    cwd=self.repo_path, capture_output=True,
                )
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=self.repo_path, capture_output=True,
                )

            # Find and remove ANY stale worktree still holding this branch
            # (from previous failed runs in different run_dirs)
            list_result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=self.repo_path, capture_output=True, text=True,
            )
            for line in list_result.stdout.splitlines():
                if line.startswith("worktree "):
                    stale_path = line.split(" ", 1)[1]
                    # Exact match: path ends with module name (avoid auth matching auth-v2)
                    if stale_path.rstrip("/").endswith("/" + module_name):
                        subprocess.run(
                            ["git", "worktree", "remove", "--force", stale_path],
                            cwd=self.repo_path, capture_output=True,
                        )
                        subprocess.run(
                            ["git", "worktree", "prune"],
                            cwd=self.repo_path, capture_output=True,
                        )

            # Delete old branch if exists
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=self.repo_path, capture_output=True,
            )

            # Create worktree with a new branch from ref or base
            ref = from_ref or self.base_branch
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(wt_path), ref],
                cwd=self.repo_path, capture_output=True, check=True,
            )

        self._worktrees[module_name] = str(wt_path)
        return str(wt_path)

    def cleanup(self, module_name: str) -> None:
        """Remove a worktree after successful completion."""
        with self._lock:
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
