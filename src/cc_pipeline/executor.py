"""CC Executor — Claude Code headless mode wrapper."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class CCResult:
    """Result of a Claude Code execution."""
    returncode: int
    stdout: str
    stderr: str


class CCExecutor:
    """Wraps `claude -p` headless execution."""

    def __init__(
        self,
        model: str = "glm-4.6",
        claude_path: str = "claude",
        default_timeout: int = 600,
    ):
        self.model = model
        self.claude_path = claude_path
        self.default_timeout = default_timeout

    def run(
        self,
        prompt: str,
        cwd: str,
        allowed_tools: list[str] | None = None,
        timeout: int | None = None,
    ) -> CCResult:
        """Run Claude Code in headless mode.

        Args:
            prompt: The instruction to send to Claude.
            cwd: Working directory for the agent.
            allowed_tools: List of allowed tool names (e.g. ["Read", "Write"]).
            timeout: Timeout in seconds.

        Returns:
            CCResult with stdout, stderr, and return code.
        """
        cmd = [
            self.claude_path,
            "-p", prompt,
        ]

        if self.model:
            cmd.extend(["--model", self.model])

        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout or self.default_timeout,
        )

        return CCResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
