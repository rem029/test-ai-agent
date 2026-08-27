"""Git operation tools for agentflow."""

from __future__ import annotations

import subprocess

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult
from .registry import register_tool


def _git_command(args: list[str], cwd: str, timeout: int = 30) -> ToolResult:
    """Run a git command and return a normalized ToolResult."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = proc.stdout
        if proc.stderr:
            output += "\n" + proc.stderr if output else proc.stderr
        return ToolResult(
            success=proc.returncode == 0,
            output=output.strip(),
        )
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, error=f"Git command timed out after {timeout}s")
    except FileNotFoundError:
        return ToolResult(success=False, error="git command not found")
    except OSError as exc:
        return ToolResult(success=False, error=f"Could not run git: {exc}")


class GitStatusParams(BaseModel):
    pass


class GitStatusTool(Tool):
    name = "GitStatus"
    description = "Show the current git status of the repository."
    param_model = GitStatusParams

    def execute(self, context: ToolContext) -> ToolResult:
        return _git_command(["status", "--short"], context.cwd)


class GitDiffParams(BaseModel):
    staged: bool = Field(default=False, description="Show staged changes instead of working tree.")


class GitDiffTool(Tool):
    name = "GitDiff"
    description = "Show git diff for working tree or staged changes."
    param_model = GitDiffParams

    def execute(self, context: ToolContext, staged: bool) -> ToolResult:
        args = ["diff", "--cached"] if staged else ["diff"]
        return _git_command(args, context.cwd)


class GitCommitSimulationParams(BaseModel):
    message: str = Field(..., description="Commit message to simulate.")


class GitCommitSimulationTool(Tool):
    name = "GitCommitSimulation"
    description = "Show what would be committed without actually committing (git diff --cached)."
    param_model = GitCommitSimulationParams

    def execute(self, context: ToolContext, message: str) -> ToolResult:
        status = _git_command(["status", "--short"], context.cwd)
        diff = _git_command(["diff", "--cached"], context.cwd)
        output = f"Proposed message: {message}\n\nStaged status:\n{status.output}\n\nStaged diff:\n{diff.output}"
        return ToolResult(
            success=status.success and diff.success,
            output=output,
        )


register_tool(GitStatusTool())
register_tool(GitDiffTool())
register_tool(GitCommitSimulationTool())
