"""Tests for the Shell tool."""

from __future__ import annotations

import pytest

from agentflow.tools import ToolContext
from agentflow.tools.shell import ShellTool


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return ToolContext(cwd=str(path))


def test_shell_echo(ctx):
    result = ShellTool().run({"command": "echo hello"}, context=ctx)
    assert result.success is True
    assert "hello" in result.output


def test_shell_exit_code(ctx):
    result = ShellTool().run({"command": "exit 1"}, context=ctx)
    assert result.success is False
    assert "exit 1" in result.output


def test_shell_timeout(ctx):
    result = ShellTool().run({"command": "sleep 5", "timeout": 1}, context=ctx)
    assert result.success is False
    assert "timed out" in result.error.lower()


def test_shell_cwd_override(ctx):
    result = ShellTool().run({"command": "pwd", "cwd": "."}, context=ctx)
    assert result.success is True
    assert str(ctx.cwd) in result.output


def test_shell_cwd_escape_rejected(ctx):
    result = ShellTool().run({"command": "pwd", "cwd": "../.."}, context=ctx)
    assert result.success is False
    assert "escapes" in result.error.lower()


def test_shell_blocked_command_rejected(ctx):
    result = ShellTool().run({"command": "rm -rf /"}, context=ctx)
    assert result.success is False
    assert "blocked" in result.error.lower()
