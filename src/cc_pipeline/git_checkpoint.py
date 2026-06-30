"""Git Checkpoint — commit + tag + rollback for pipeline steps."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitCheckpoint:
    """Manages git checkpoints for a pipeline worktree."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self._git_env = None  # Use default env

    def _run_git(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        """Run a git command in the repo."""
        return subprocess.run(
            ["git"] + args,
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
            **kwargs,
        )

    def checkpoint(
        self,
        step: str,
        module: str,
        attempt: int,
    ) -> str:
        """Create a git checkpoint (commit + tag) for the current state.

        Args:
            step: Step ID (e.g. "scaffold", "generate").
            module: Module name.
            attempt: Attempt number (1-based).

        Returns:
            The tag name created.
        """
        # Stage all changes
        self._run_git(["add", "-A"])

        # Check if there are changes to commit
        status = self._run_git(["status", "--porcelain"])
        if status.stdout.strip():
            commit_msg = f"[pipeline:{module}:{step}:{attempt}] checkpoint"
            self._run_git(["commit", "-m", commit_msg])

        # Create tag
        tag = f"pipeline/{module}/{step}/{attempt}"
        self._run_git(["tag", "-f", tag])

        return tag

    def rollback(
        self,
        step: str,
        module: str,
        attempt: int,
    ) -> None:
        """Rollback the worktree to a checkpoint state.

        After rollback, the worktree contains all files up to and including
        the specified checkpoint, discarding any later changes.

        Args:
            step: Step ID to roll back to.
            module: Module name.
            attempt: Attempt number of the checkpoint.
        """
        tag = f"pipeline/{module}/{step}/{attempt}"

        # Hard reset to the tagged commit
        self._run_git(["reset", "--hard", tag])

        # Clean untracked files (but preserve .pipeline/)
        self._run_git(["clean", "-fd", "--exclude=.pipeline/"])
