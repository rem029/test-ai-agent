"""Tests for git tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentflow.tools import ToolContext
from agentflow.tools.git import GitCommitSimulationTool, GitDiffTool, GitStatusTool


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return ToolContext(cwd=str(path))


def test_git_status_clean(ctx):
    result = GitStatusTool().run({}, context=ctx)
    assert result.success is True
    # Clean repo should have empty status output.


def test_git_diff_unstaged(ctx):
    file = Path(ctx.cwd) / "file.txt"
    file.write_text("initial")
    subprocess.run(["git", "add", "file.txt"], cwd=ctx.cwd, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=ctx.cwd, check=True, capture_output=True)
    file.write_text("hello")
    result = GitDiffTool().run({"staged": False}, context=ctx)
    assert result.success is True
    assert "hello" in result.output


def test_git_commit_simulation(ctx):
    (Path(ctx.cwd) / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "file.txt"], cwd=ctx.cwd, check=True, capture_output=True)
    result = GitCommitSimulationTool().run({"message": "test commit"}, context=ctx)
    assert result.success is True
    assert "test commit" in result.output
    assert "hello" in result.output
