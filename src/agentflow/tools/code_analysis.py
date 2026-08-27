"""Code analysis tools for agentflow."""

from __future__ import annotations

import ast
import importlib.util
import py_compile
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult
from .registry import register_tool


def _run_command(command: list[str], cwd: str, timeout: int = 60) -> ToolResult:
    """Run an external command and return a normalized ToolResult."""
    try:
        proc = subprocess.run(
            command,
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
        return ToolResult(success=False, error=f"Command timed out after {timeout}s")
    except FileNotFoundError:
        return ToolResult(success=False, error=f"Command not found: {command[0]}")
    except OSError as exc:
        return ToolResult(success=False, error=f"Could not run command: {exc}")


class LintParams(BaseModel):
    path: str = Field(default=".", description="Relative path to file or directory to lint.")


class LintTool(Tool):
    name = "Lint"
    description = "Run a linter on a file or directory. Prefers ruff, falls back to py_compile."
    param_model = LintParams

    def execute(self, context: ToolContext, path: str) -> ToolResult:
        target = Path(context.cwd) / path
        if not target.exists():
            return ToolResult(success=False, error=f"Path not found: {path}")

        if importlib.util.find_spec("ruff") is not None:
            return _run_command(["python", "-m", "ruff", "check", str(target)], context.cwd)

        # Fallback: compile every .py file under the target.
        files = [target] if target.is_file() else list(target.rglob("*.py"))
        errors: list[str] = []
        for f in files:
            try:
                py_compile.compile(str(f), doraise=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f}: {exc}")
        if errors:
            return ToolResult(success=False, output="\n".join(errors))
        return ToolResult(success=True, output="No syntax errors found.")


class TypeCheckParams(BaseModel):
    path: str = Field(default=".", description="Relative path to file or directory to type check.")


class TypeCheckTool(Tool):
    name = "TypeCheck"
    description = "Run a type checker. Prefers pyright, falls back to a syntax check."
    param_model = TypeCheckParams

    def execute(self, context: ToolContext, path: str) -> ToolResult:
        target = Path(context.cwd) / path
        if not target.exists():
            return ToolResult(success=False, error=f"Path not found: {path}")

        if importlib.util.find_spec("pyright") is not None:
            return _run_command(["python", "-m", "pyright", str(target)], context.cwd)

        # Fallback: compile every .py file under the target to catch syntax errors.
        files = [target] if target.is_file() else list(target.rglob("*.py"))
        errors: list[str] = []
        for f in files:
            try:
                py_compile.compile(str(f), doraise=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{f}: {exc}")
        if errors:
            return ToolResult(success=False, output="\n".join(errors))
        return ToolResult(
            success=True,
            output="No standalone type checker found; syntax check passed.",
        )


class ImportAnalysisParams(BaseModel):
    path: str = Field(default=".", description="Relative path to file or directory to analyze.")


class ImportAnalysisTool(Tool):
    name = "ImportAnalysis"
    description = "List imports used in Python files under a path."
    param_model = ImportAnalysisParams

    def execute(self, context: ToolContext, path: str) -> ToolResult:
        target = Path(context.cwd) / path
        if not target.exists():
            return ToolResult(success=False, error=f"Path not found: {path}")

        files = [target] if target.is_file() else list(target.rglob("*.py"))
        imports: list[str] = []
        for f in sorted(files):
            try:
                tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            except SyntaxError as exc:
                imports.append(f"{f}: syntax error {exc}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(f"{f}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    names = ", ".join(a.name for a in node.names)
                    imports.append(f"{f}: from {module} import {names}")

        if not imports:
            return ToolResult(success=True, output="No imports found.")
        return ToolResult(success=True, output="\n".join(imports))


register_tool(LintTool())
register_tool(TypeCheckTool())
register_tool(ImportAnalysisTool())
