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

    def find_latest_checkpoint(self, step: str, module: str) -> str | None:
        """Find the latest checkpoint tag for a step/module.

        Tags are pipeline/{module}/{step}/{attempt}. Returns the one with
        the highest attempt number, or None if no tags exist.

        Returns:
            Full tag name (e.g. "pipeline/auth/scaffold/3") or None.
        """
        prefix = f"pipeline/{module}/{step}/"
        result = self._run_git(["tag", "-l", f"{prefix}*"])
        tags = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]
        if not tags:
            return None

        # Sort by attempt number (numeric, not lexicographic)
        def attempt_num(tag: str) -> int:
            try:
                return int(tag.rsplit("/", 1)[-1])
            except ValueError:
                return 0

        tags.sort(key=attempt_num)
        return tags[-1]

    def rollback_to_latest(self, step: str, module: str) -> bool:
        """Rollback to the latest checkpoint for a step/module.

        Args:
            step: Step ID.
            module: Module name.

        Returns:
            True if rollback succeeded, False if no checkpoint found.
        """
        latest = self.find_latest_checkpoint(step, module)
        if latest is None:
            return False

        self._run_git(["reset", "--hard", latest])
        self._run_git(["clean", "-fd", "--exclude=.pipeline/"])
        return True

    def list_completed_steps(self, module: str) -> list[str]:
        """List all completed step IDs for a module from git tags.

        Scans tags matching pipeline/{module}/{step}/{attempt} and returns
        the unique step names. Used by resume to skip already-completed steps.

        Returns:
            List of step IDs that have at least one checkpoint tag.
        """
        prefix = f"pipeline/{module}/"
        result = self._run_git(["tag", "-l", f"{prefix}*"])
        tags = [t.strip() for t in result.stdout.strip().split("\n") if t.strip()]

        steps = set()
        for tag in tags:
            # tag format: pipeline/{module}/{step}/{attempt}
            parts = tag.split("/")
            if len(parts) >= 4:
                steps.add(parts[2])

        return sorted(steps)
