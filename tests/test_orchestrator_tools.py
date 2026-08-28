"""Tests for the orchestrator tool loop."""

from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.orchestrator import run_workflow
from agentflow.tools import ToolResult


@pytest.fixture
def cwd(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return str(path)


def _make_backend_responses(texts: list[str]):
    """Return a mock backend whose run() returns texts in order."""
    backend = MagicMock()
    responses = [
        RunResult(success=True, text=t, usage=Usage("mock", "model", 1, 1, 0.0), raw={})
        for t in texts
    ]
    backend.run.side_effect = responses
    return backend


def test_tool_loop_executes_requested_tool(cwd):
    (Path(cwd) / "src").mkdir()
    (Path(cwd) / "src" / "foo.py").write_text("hello")

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    review_text = """
1. Read the file.
<tool_call>
  <ReadFile>
    <args>
      <path>src/foo.py</path>
    </args>
  </ReadFile>
</tool_call>
"""
    review_final = "Plan: read the file."
    build_text = "Built the changes. VERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text, review_final, build_text, verify_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    assert state.steps[0]["role"] == "review"
    assert len(state.tool_calls) >= 1
    assert state.tool_calls[0]["tool_name"] == "ReadFile"
    assert state.tool_calls[0]["args"] == {"path": "src/foo.py"}
    assert state.tool_calls[0]["status"] == "success"
    assert "hello" in state.tool_calls[0]["result"]["output"]


def test_tool_loop_respects_max_calls(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    # Always request a tool, never produce a final answer.
    review_text = """
<tool_call>
  <ReadFile>
    <args><path>src/foo.py</path></args>
  </ReadFile>
</tool_call>
"""

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text] * 20)
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    # The review step should fail after max tool calls.
    assert state.steps[0]["role"] == "review"
    assert state.steps[0]["success"] is False
    assert "maximum" in state.steps[0]["text"].lower()


def test_no_tool_requests_skips_loop(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    review_text = "Plan: do nothing."
    build_text = "Done. VERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text, build_text, verify_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    assert state.steps[0]["success"] is True
    assert len(state.tool_calls) == 0
