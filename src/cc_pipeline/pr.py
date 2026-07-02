"""PR Creator — GitHub PR automation via gh CLI."""
from __future__ import annotations

import subprocess
from pathlib import Path


class PRCreator:
    """Creates GitHub PRs via the `gh` CLI."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)

    def create(
        self,
        branch: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
        base: str | None = None,
    ) -> str | None:
        """Create a GitHub PR.

        Args:
            branch: Source branch (head).
            title: PR title.
            body: PR body text.
            labels: Labels to apply.
            base: Target branch (defaults to repo default).

        Returns:
            PR URL string, or None if creation failed.
        """
        cmd = [
            "gh", "pr", "create",
            "--head", branch,
            "--title", title,
            "--body", body,
        ]

        if base:
            cmd.extend(["--base", base])

        if labels:
            for label in labels:
                cmd.extend(["--label", label])

        result = subprocess.run(
            cmd,
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return None

        return result.stdout.strip() or None
