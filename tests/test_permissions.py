"""Tests for permission policy layer: read-only auto-allowed, mutating policy (auto, prompt, deny)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.cli import main as cli_main
from agentflow.config import Config, RoleConfig
from agentflow.orchestrator import _check_tool_permission, run_workflow
from agentflow.tools.base import ToolContext
from agentflow.tools.file_ops import ReadFileTool, WriteFileTool


def _make_mock_backend(texts: list[str]):
    backend = MagicMock()
    responses = [
        RunResult(success=True, text=t, usage=Usage("mock", "model", 1, 1, 0.0), raw={})
        for t in texts
    ]
    backend.run.side_effect = responses
    return backend


def test_read_only_tools_always_allowed():
    read_tools = [
        "ReadFile",
        "ListDirectory",
        "SearchFiles",
        "CodeSearch",
        "WebFetch",
        "DocumentationSearch",
        "Lint",
        "TypeCheck",
        "ImportAnalysis",
        "GitStatus",
        "GitDiff",
        "GitCommitSimulation",
    ]
    for tool_name in read_tools:
        # Even with deny policy, read-only tools are allowed
        allowed, reason = _check_tool_permission(tool_name, {}, "deny")
        assert allowed is True
        assert reason is None


def test_mutating_tools_auto_policy():
    for tool_name in ["WriteFile", "Shell"]:
        allowed, reason = _check_tool_permission(tool_name, {"path": "a.txt"}, "auto")
        assert allowed is True
        assert reason is None


def test_mutating_tools_deny_policy():
    for tool_name in ["WriteFile", "Shell"]:
        allowed, reason = _check_tool_permission(tool_name, {"path": "a.txt"}, "deny")
        assert allowed is False
        assert "blocked by permissions policy" in reason


def test_mutating_tools_prompt_non_interactive():
    with patch("sys.stdin.isatty", return_value=False):
        allowed, reason = _check_tool_permission("WriteFile", {"path": "a.txt"}, "prompt")
        assert allowed is False
        assert "non-interactive" in reason


def test_mutating_tools_prompt_interactive_allowed():
    with patch("sys.stdin.isatty", return_value=True):
        with patch("builtins.input", return_value="y"):
            allowed, reason = _check_tool_permission("WriteFile", {"path": "a.txt"}, "prompt")
            assert allowed is True
            assert reason is None


def test_mutating_tools_prompt_interactive_rejected():
    with patch("sys.stdin.isatty", return_value=True):
        with patch("builtins.input", return_value="n"):
            allowed, reason = _check_tool_permission("WriteFile", {"path": "a.txt"}, "prompt")
            assert allowed is False
            assert "denied by user" in reason


def test_workflow_execution_blocks_mutating_tool_on_deny(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "test.py").write_text("initial = 1\n")

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        permissions="deny",
    )

    review_text = "Plan: write file"
    build_text = """
<tool_call>
{"name": "WriteFile", "args": {"path": "src/test.py", "content": "changed = 2"}}
</tool_call>
"""
    build_final = "Done.\nVERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_mock_backend([review_text, build_text, build_final, verify_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow(
                    "deny write task",
                    config,
                    str(repo),
                    database_path=tmp_path / "test.db",
                )

    # The file should NOT have been modified
    assert (repo / "src" / "test.py").read_text() == "initial = 1\n"
    # Tool call should be logged with failure / permission error
    assert any("Permission denied" in str(c.get("result", {}).get("error", "")) for c in state.tool_calls)


def test_cli_permissions_flag(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    with patch("agentflow.cli.run_workflow") as mock_run:
        mock_run.return_value = MagicMock(pushed={"pushed": True})
        rc = cli_main(["--permissions", "deny", "test goal"])
        assert rc == 0
        called_config = mock_run.call_args[0][1]
        assert called_config.permissions == "deny"
