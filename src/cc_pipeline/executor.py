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
        model: str | None = None,
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
            "--dangerously-skip-permissions",
        ]

        if self.model and self.model.strip():
            cmd.extend(["--model", self.model])

        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
            )
        except KeyboardInterrupt:
            # Ctrl+C: kill CC and its children
            print("  ⛔ Interrupted by user — killing CC process")
            raise
        # TimeoutExpired intentionally NOT caught — let it bubble up
        # to runner which classifies it as TIMEOUT (not CC_FAILED).

        return CCResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


@dataclass
class ShellResult:
    """Result of a deterministic shell command execution."""
    returncode: int
    stdout: str
    stderr: str


class ShellExecutor:
    """Wraps deterministic shell command execution (trusted layer)."""

    def __init__(self, default_timeout: int = 300):
        self.default_timeout = default_timeout

    def run(
        self,
        command: str,
        cwd: str,
        timeout: int | None = None,
    ) -> ShellResult:
        """Run a shell command deterministically.

        Args:
            command: Shell command string.
            cwd: Working directory.
            timeout: Timeout in seconds.

        Returns:
            ShellResult with stdout, stderr, return code.
        """
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout or self.default_timeout,
        )

        return ShellResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
