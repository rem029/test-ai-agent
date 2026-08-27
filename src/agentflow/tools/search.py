"""Search and fetch tools for agentflow."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult
from .registry import register_tool


class WebFetchParams(BaseModel):
    url: str = Field(..., description="URL to fetch.")
    max_length: int = Field(default=5000, ge=1, le=50000, description="Maximum characters to return.")


class WebFetchTool(Tool):
    name = "WebFetch"
    description = "Fetch the content of a URL and return it as text."
    param_model = WebFetchParams

    def execute(self, context: ToolContext, url: str, max_length: int) -> ToolResult:
        try:
            resp = httpx.get(url, timeout=10, follow_redirects=True)
            resp.raise_for_status()
            text = resp.text
            if len(text) > max_length:
                text = text[:max_length] + "\n... (truncated)"
            return ToolResult(success=True, output=text)
        except httpx.HTTPError as exc:
            return ToolResult(success=False, error=f"Could not fetch {url}: {exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Unexpected error fetching {url}: {exc}")


class CodeSearchParams(BaseModel):
    pattern: str = Field(..., description="Regex pattern to search for.")
    path: str = Field(default=".", description="Relative directory to search under.")
    language: str | None = Field(None, description="Optional file extension filter, e.g. 'py'.")


class CodeSearchTool(Tool):
    name = "CodeSearch"
    description = "Search source code by regex, returning file paths and matched lines."
    param_model = CodeSearchParams

    def execute(
        self, context: ToolContext, pattern: str, path: str, language: str | None
    ) -> ToolResult:
        target = Path(context.cwd) / path
        if not target.is_dir():
            return ToolResult(success=False, error=f"Not a directory: {path}")

        try:
            matcher = re.compile(pattern)
        except re.error as exc:
            return ToolResult(success=False, error=f"Invalid regex pattern: {exc}")

        glob = f"*.{language}" if language else "*.py"
        matches: list[str] = []
        for p in sorted(target.rglob(glob)):
            if not p.is_file():
                continue
            rel = p.relative_to(target)
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


class DocumentationSearchParams(BaseModel):
    query: str = Field(..., description="Topic or function name to look up.")
    source: str = Field(
        default="python",
        description="Documentation source to search. Supported: python, fastapi, pydantic.",
    )


class DocumentationSearchTool(Tool):
    name = "DocumentationSearch"
    description = "Fetch a documentation page for a topic from a curated set of sources."
    param_model = DocumentationSearchParams

    DOCS_URLS: dict[str, str] = {
        "python": "https://docs.python.org/3/search.html?q={query}",
        "fastapi": "https://fastapi.tiangolo.com/search/?q={query}",
        "pydantic": "https://docs.pydantic.dev/latest/search/?q={query}",
    }

    def execute(self, context: ToolContext, query: str, source: str) -> ToolResult:
        url_template = self.DOCS_URLS.get(source.lower())
        if not url_template:
            supported = ", ".join(self.DOCS_URLS.keys())
            return ToolResult(
                success=False,
                error=f"Unsupported documentation source: {source}. Supported: {supported}",
            )
        url = url_template.format(query=query.replace(" ", "+"))
        return WebFetchTool().execute(context, url=url, max_length=3000)


register_tool(WebFetchTool())
register_tool(CodeSearchTool())
register_tool(DocumentationSearchTool())
