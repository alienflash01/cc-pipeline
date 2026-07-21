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
        *,
        session_id: str | None = None,
        allowed_tools: list | None = None,
        resume_session: bool = False,
        timeout: int | None = None,
    ) -> CCResult:
        """Execute claude with optional session management.

        Args:
            prompt: Full resolved prompt.
            cwd: Working directory.
            session_id: UUID for CC session (None = no session).
            resume_session: If True, use --resume instead of -p.
        """
        if resume_session and session_id:
            cmd = [
                self.claude_path,
                "--resume", session_id,
                "-p", prompt,
                "--print",
                "--model", self.model,
                "--dangerously-skip-permissions",
            ]
        elif session_id:
            cmd = [
                self.claude_path,
                "-p", prompt,
                "--session-id", session_id,
                "--print",
                "--model", self.model,
                "--dangerously-skip-permissions",
            ]
        else:
            cmd = [
                self.claude_path,
                "-p", prompt,
                "--model", self.model,
                "--dangerously-skip-permissions",
            ]

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
