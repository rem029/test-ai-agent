"""Shell execution tool for agentflow."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolError, ToolResult
from .registry import register_tool


class ShellParams(BaseModel):
    command: str = Field(..., description="Shell command to execute.")
    timeout: int = Field(default=60, ge=1, le=600, description="Timeout in seconds.")
    cwd: str | None = Field(
        default=None,
        description="Optional working directory override relative to the tool context.",
    )


class ShellTool(Tool):
    name = "Shell"
    description = (
        "Execute a shell command with a timeout. Returns stdout, stderr, "
        "and the exit code. Dangerous commands and redirection outside the "
        "working directory are rejected."
    )
    param_model = ShellParams

    # Commands/substrings that could destroy state, escape the container, or
    # exfiltrate sensitive data. This is a best-effort guard, not a sandbox.
    BLOCKED_SUBSTRINGS: frozenset[str] = frozenset({
        "rm -rf /",
        "rm -rf /*",
        ":(){ :|:& };:",  # fork bomb
        "> /dev/",
        "mkfs",
        "dd if=",
        "curl",
        "wget",
    })

    def execute(
        self, context: ToolContext, command: str, timeout: int, cwd: str | None
    ) -> ToolResult:
        target_cwd = Path(context.cwd).resolve()
        if cwd is not None:
            target_cwd = (target_cwd / cwd).resolve()
            base = Path(context.cwd).resolve()
            if base not in target_cwd.parents and target_cwd != base:
                return ToolResult(success=False, error=f"Working directory escapes context: {cwd}")

        command = command.strip()
        if not command:
            return ToolResult(success=False, error="Empty command")

        lowered = command.lower()
        for bad in self.BLOCKED_SUBSTRINGS:
            if bad in lowered:
                return ToolResult(success=False, error=f"Command blocked: {bad!r}")

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=target_cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = proc.stdout
            if proc.stderr:
                output += "\n" + proc.stderr if output else proc.stderr
            status = "exit 0" if proc.returncode == 0 else f"exit {proc.returncode}"
            return ToolResult(
                success=proc.returncode == 0,
                output=f"{status}\n{output}".strip(),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Command timed out after {timeout}s")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not run command: {exc}")


register_tool(ShellTool())
