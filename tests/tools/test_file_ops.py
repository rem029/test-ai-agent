"""Tests for file operation tools."""

from __future__ import annotations

import pytest

from agentflow.tools import ToolContext
from agentflow.tools.file_ops import (
    ListDirectoryTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
)


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    ctx = ToolContext(cwd=str(path))
    ctx.cwd_path = path
    return ctx


def test_read_file(ctx):
    file = ctx.cwd_path / "hello.txt"
    file.write_text("line one\nline two\nline three\n")
    result = ReadFileTool().run({"path": "hello.txt"}, context=ctx)
    assert result.success is True
    assert "line one" in result.output


def test_read_file_line_range(ctx):
    file = ctx.cwd_path / "hello.txt"
    file.write_text("line one\nline two\nline three\n")
    result = ReadFileTool().run(
        {"path": "hello.txt", "start_line": 2, "end_line": 2}, context=ctx
    )
    assert result.success is True
    assert result.output == "line two"


def test_read_file_missing(ctx):
    result = ReadFileTool().run({"path": "missing.txt"}, context=ctx)
    assert result.success is False
    assert "not found" in result.error.lower()


def test_write_file(ctx):
    result = WriteFileTool().run(
        {"path": "nested/file.txt", "content": "hello"}, context=ctx
    )
    assert result.success is True
    assert (ctx.cwd_path / "nested" / "file.txt").read_text() == "hello"


def test_write_file_captures_previous_content(ctx):
    path = "version.txt"
    WriteFileTool().run({"path": path, "content": "old"}, context=ctx)
    result = WriteFileTool().run({"path": path, "content": "new"}, context=ctx)
    assert result.success is True
    assert result.structured is not None
    assert result.structured.get("previous") == "old"
    assert result.structured.get("current") == "new"


def test_write_file_escapes_cwd(ctx):
    result = WriteFileTool().run(
        {"path": "../outside.txt", "content": "bad"}, context=ctx
    )
    assert result.success is False
    assert "escapes" in result.error.lower()


def test_list_directory(ctx):
    (ctx.cwd_path / "a.txt").write_text("a")
    (ctx.cwd_path / "b").mkdir()
    (ctx.cwd_path / "b" / "c.txt").write_text("c")
    result = ListDirectoryTool().run({"path": "."}, context=ctx)
    assert result.success is True
    assert "[file] a.txt" in result.output
    assert "[dir] b" in result.output


def test_list_directory_recursive(ctx):
    (ctx.cwd_path / "b").mkdir()
    (ctx.cwd_path / "b" / "c.txt").write_text("c")
    result = ListDirectoryTool().run({"path": ".", "recursive": True}, context=ctx)
    assert result.success is True
    assert "[file] b/c.txt" in result.output


def test_search_files_regex(ctx):
    (ctx.cwd_path / "foo.py").write_text("def hello(): pass\n")
    (ctx.cwd_path / "bar.py").write_text("def world(): pass\n")
    result = SearchFilesTool().run(
        {"pattern": "def hello", "path": ".", "regex": True}, context=ctx
    )
    assert result.success is True
    assert "foo.py:1" in result.output
    assert "bar.py" not in result.output


def test_search_files_glob(ctx):
    (ctx.cwd_path / "foo.py").write_text("x")
    (ctx.cwd_path / "bar.txt").write_text("x")
    result = SearchFilesTool().run(
        {"pattern": "*.py", "path": ".", "regex": False}, context=ctx
    )
    assert result.success is True
    assert "foo.py" in result.output
    assert "bar.txt" not in result.output


def test_read_file_outside_cwd_rejected(ctx):
    result = ReadFileTool().run({"path": "../outside.txt"}, context=ctx)
    assert result.success is False
    assert "escapes" in result.error.lower()


def test_read_file_alias_file_path(ctx):
    file = ctx.cwd_path / "hello.txt"
    file.write_text("hello alias")
    result = ReadFileTool().run({"file_path": "hello.txt"}, context=ctx)
    assert result.success is True
    assert result.output == "hello alias"


def test_read_file_invalid_params_readable_error(ctx):
    result = ReadFileTool().run({"nonsense": "x"}, context=ctx)
    assert result.success is False
    assert "Invalid parameters for ReadFile:" in result.error
    assert "missing required argument 'path'" in result.error
    assert "Accepted:" in result.error
    assert "You provided: nonsense" in result.error
    assert "errors.pydantic.dev" not in result.error


def test_write_file_alias_file_path(ctx):
    result = WriteFileTool().run(
        {"file_path": "new.txt", "content": "hi"}, context=ctx
    )
    assert result.success is True
    assert (ctx.cwd_path / "new.txt").read_text() == "hi"


def test_list_directory_alias_dir(ctx):
    (ctx.cwd_path / "sub").mkdir()
    (ctx.cwd_path / "sub" / "file.txt").write_text("ok")
    result = ListDirectoryTool().run({"dir": "sub"}, context=ctx)
    assert result.success is True
    assert "[file] file.txt" in result.output


def test_search_files_alias_directory(ctx):
    (ctx.cwd_path / "sub").mkdir()
    (ctx.cwd_path / "sub" / "target.py").write_text("matched_text")
    result = SearchFilesTool().run(
        {"pattern": "matched_text", "directory": "sub"}, context=ctx
    )
    assert result.success is True
    assert "target.py:1: matched_text" in result.output

