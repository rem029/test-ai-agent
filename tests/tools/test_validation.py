"""Validation and security tests for agentflow tools."""

from __future__ import annotations

import pytest

from agentflow.tools import ToolContext
from agentflow.tools.file_ops import ReadFileTool, WriteFileTool
from agentflow.tools.parser import parse_tool_requests
from agentflow.tools.shell import ShellTool


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return ToolContext(cwd=str(path))


def test_shell_blocklist_substring_anywhere(ctx):
    """Blocked commands are rejected even when embedded in a pipeline."""
    result = ShellTool().run({"command": "echo before && rm -rf /tmp/foo && echo after"}, context=ctx)
    assert result.success is False
    assert "blocked" in result.error.lower()


def test_shell_allows_safe_command(ctx):
    result = ShellTool().run({"command": "echo hello"}, context=ctx)
    assert result.success is True
    assert "hello" in result.output


def test_read_file_rejects_absolute_path(ctx, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    result = ReadFileTool().run({"path": str(outside)}, context=ctx)
    assert result.success is False
    assert "escapes" in result.error.lower()


def test_write_file_rejects_symlink_escape(tmp_path):
    """A symlink pointing above cwd must not allow reads outside the workspace."""
    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (work / "link.txt").symlink_to(secret)

    ctx = ToolContext(cwd=str(work))
    result = ReadFileTool().run({"path": "link.txt"}, context=ctx)
    assert result.success is False
    assert "escapes" in result.error.lower()


def test_parser_rejects_unclosed_tool_call():
    text = "<tool_call>{\"name\": \"ReadFile\""
    requests = parse_tool_requests(text)
    assert len(requests) == 0


def test_parser_ignores_non_json_in_tool_call():
    text = "<tool_call>not valid json</tool_call>"
    requests = parse_tool_requests(text)
    assert len(requests) == 0


def test_parser_handles_empty_tool_call():
    text = "<tool_call></tool_call>"
    requests = parse_tool_requests(text)
    assert len(requests) == 0
