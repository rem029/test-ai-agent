"""File operation tools for agentflow."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from .base import Tool, ToolContext, ToolError, ToolResult
from .registry import register_tool


def _resolve_path(cwd: str, rel_path: str) -> Path:
    """Resolve a path under cwd, rejecting escapes above cwd."""
    base = Path(cwd).resolve()
    target = (base / rel_path).resolve()
    if base not in target.parents and target != base:
        raise ToolError(f"Path escapes working directory: {rel_path}")
    return target


class ReadFileParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(
        ...,
        validation_alias=AliasChoices("path", "file_path", "filepath", "filename"),
        description="Relative path to the file to read.",
    )
    start_line: int | None = Field(None, ge=1, description="Optional 1-based start line.")
    end_line: int | None = Field(None, ge=1, description="Optional 1-based end line.")


class ReadFileTool(Tool):
    name = "ReadFile"
    description = "Read the contents of a file, optionally constrained to a line range."
    param_model = ReadFileParams

    def execute(
        self,
        context: ToolContext,
        path: str,
        start_line: int | None,
        end_line: int | None,
    ) -> ToolResult:
        target = _resolve_path(context.cwd, path)
        if not target.exists():
            return ToolResult(success=False, error=f"File not found: {path}")
        if not target.is_file():
            return ToolResult(success=False, error=f"Not a file: {path}")

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not read {path}: {exc}")

        lines = text.splitlines()
        if start_line is not None or end_line is not None:
            start = (start_line or 1) - 1
            end = end_line if end_line is not None else len(lines)
            lines = lines[start:end]
            text = "\n".join(lines)

        return ToolResult(success=True, output=text)


class WriteFileParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(
        ...,
        validation_alias=AliasChoices("path", "file_path", "filepath", "filename"),
        description="Relative path where the file should be written.",
    )
    content: str = Field(..., description="Full content to write to the file.")


class WriteFileTool(Tool):
    name = "WriteFile"
    description = "Create or overwrite a file with the provided content."
    param_model = WriteFileParams

    def execute(self, context: ToolContext, path: str, content: str) -> ToolResult:
        target = _resolve_path(context.cwd, path)
        previous = None
        if target.exists():
            try:
                previous = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                previous = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not write {path}: {exc}")
        structured = {"path": path}
        if previous is not None:
            structured["previous"] = previous
        structured["current"] = content
        return ToolResult(success=True, output=f"Wrote {path}", structured=structured)


class ListDirectoryParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(
        default=".",
        validation_alias=AliasChoices("path", "dir", "directory"),
        description="Relative directory path to list.",
    )
    recursive: bool = Field(default=False, description="List recursively.")


class ListDirectoryTool(Tool):
    name = "ListDirectory"
    description = "List files and directories, optionally recursively."
    param_model = ListDirectoryParams

    def execute(self, context: ToolContext, path: str, recursive: bool) -> ToolResult:
        target = _resolve_path(context.cwd, path)
        if not target.exists():
            return ToolResult(success=False, error=f"Directory not found: {path}")
        if not target.is_dir():
            return ToolResult(success=False, error=f"Not a directory: {path}")

        try:
            entries: list[str] = []
            if recursive:
                for p in sorted(target.rglob("*")):
                    rel = p.relative_to(target)
                    prefix = "[dir]" if p.is_dir() else "[file]"
                    entries.append(f"{prefix} {rel}")
            else:
                for p in sorted(target.iterdir()):
                    prefix = "[dir]" if p.is_dir() else "[file]"
                    entries.append(f"{prefix} {p.name}")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not list {path}: {exc}")

        return ToolResult(success=True, output="\n".join(entries))


class SearchFilesParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pattern: str = Field(..., description="Regex or glob pattern to search for.")
    path: str = Field(
        default=".",
        validation_alias=AliasChoices("path", "dir", "directory"),
        description="Relative directory to search under.",
    )
    glob: str | None = Field(None, description="Optional glob filter for files, e.g. '*.py'.")
    regex: bool = Field(default=True, description="Treat pattern as regex if true, otherwise glob.")


class SearchFilesTool(Tool):
    name = "SearchFiles"
    description = "Search file contents by regex or filename glob under a directory."
    param_model = SearchFilesParams

    def execute(
        self,
        context: ToolContext,
        pattern: str,
        path: str,
        glob: str | None,
        regex: bool,
    ) -> ToolResult:
        target = _resolve_path(context.cwd, path)
        if not target.is_dir():
            return ToolResult(success=False, error=f"Not a directory: {path}")

        try:
            matcher: Any
            if regex:
                matcher = re.compile(pattern)
            else:
                matcher = None

            matches: list[str] = []
            for p in sorted(target.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(target)
                if glob and not fnmatch.fnmatch(str(rel), glob):
                    continue
                if not regex:
                    if fnmatch.fnmatch(str(rel), pattern) or fnmatch.fnmatch(p.name, pattern):
                        matches.append(f"{rel}")
                    continue

                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), start=1):
                    if matcher.search(line):
                        matches.append(f"{rel}:{i}: {line}")

            if not matches:
                return ToolResult(success=True, output="No matches found.")
            return ToolResult(success=True, output="\n".join(matches[:500]))
        except re.error as exc:
            return ToolResult(success=False, error=f"Invalid regex pattern: {exc}")
        except OSError as exc:
            return ToolResult(success=False, error=f"Could not search {path}: {exc}")


register_tool(ReadFileTool())
register_tool(WriteFileTool())
register_tool(ListDirectoryTool())
register_tool(SearchFilesTool())
