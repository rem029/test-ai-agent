"""Tests for search tools."""

from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest

from agentflow.tools import ToolContext, ToolResult
from agentflow.tools.search import CodeSearchTool, DocumentationSearchTool, WebFetchTool


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return ToolContext(cwd=str(path))


def test_web_fetch_success(ctx):
    with patch("agentflow.tools.search.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "hello world"
        result = WebFetchTool().run({"url": "https://example.com"}, context=ctx)
    assert result.success is True
    assert "hello world" in result.output


def test_web_fetch_failure(ctx):
    with patch("agentflow.tools.search.httpx.get") as mock_get:
        import httpx
        mock_get.side_effect = httpx.HTTPError("network down")
        result = WebFetchTool().run({"url": "https://example.com"}, context=ctx)
    assert result.success is False
    assert "network down" in result.error


def test_code_search(ctx):
    (Path(ctx.cwd) / "foo.py").write_text("def hello(): pass\n")
    (Path(ctx.cwd) / "bar.py").write_text("def world(): pass\n")
    result = CodeSearchTool().run(
        {"pattern": "def hello", "path": ".", "language": "py"}, context=ctx
    )
    assert result.success is True
    assert "foo.py:1" in result.output
    assert "bar.py" not in result.output


def test_documentation_search_supported_source(ctx):
    with patch.object(WebFetchTool, "execute", return_value=ToolResult(success=True, output="docs")):
        result = DocumentationSearchTool().run(
            {"query": "BaseModel", "source": "pydantic"}, context=ctx
        )
    assert result.success is True


def test_documentation_search_unsupported_source(ctx):
    result = DocumentationSearchTool().run(
        {"query": "BaseModel", "source": "unknown"}, context=ctx
    )
    assert result.success is False
    assert "Unsupported" in result.error
