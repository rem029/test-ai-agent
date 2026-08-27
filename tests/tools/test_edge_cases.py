"""Edge-case tests for agentflow tools."""

from __future__ import annotations
from pathlib import Path

import pytest

from agentflow.tools import ToolContext
from agentflow.tools.file_ops import ListDirectoryTool, ReadFileTool, SearchFilesTool
from agentflow.tools.shell import ShellTool


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return ToolContext(cwd=str(path))


def test_read_very_large_file_is_truncated(ctx):
    file = Path(ctx.cwd) / "huge.txt"
    file.write_text("x" * 100_000)
    result = ReadFileTool().run({"path": "huge.txt"}, context=ctx)
    assert result.success is True
    assert len(result.output) <= 100_000


def test_list_deeply_nested_directory(ctx):
    base = Path(ctx.cwd)
    deep = base / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("deep")
    result = ListDirectoryTool().run({"path": ".", "recursive": True}, context=ctx)
    assert result.success is True
    assert "a/b/c/d/leaf.txt" in result.output


def test_shell_empty_command_rejected(ctx):
    result = ShellTool().run({"command": "   "}, context=ctx)
    assert result.success is False
    assert "empty" in result.error.lower()


def test_search_files_invalid_regex(ctx):
    result = SearchFilesTool().run(
        {"pattern": "[invalid", "path": ".", "regex": True}, context=ctx
    )
    assert result.success is False
    assert "Invalid regex" in result.error


def test_tool_result_truncation_for_persistence():
    from agentflow.tools.base import ToolResult

    result = ToolResult(success=True, output="x" * 10_000, error="y" * 10_000)
    data = result.model_dump_truncated(max_length=100)
    assert len(data["output"]) < 10_000
    assert "truncated" in data["output"]
    assert len(data["error"]) < 10_000
    assert "truncated" in data["error"]
