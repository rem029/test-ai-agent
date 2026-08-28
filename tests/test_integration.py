"""End-to-end integration tests for agentflow with real tool execution."""

from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.orchestrator import run_workflow


def _backend_with_responses(responses: list[str]):
    backend = MagicMock()
    results = [
        RunResult(success=True, text=t, usage=Usage("mock", "model", 1, 1, 0.0), raw={})
        for t in responses
    ]

    def _next_result(*args, **kwargs):
        if not results:
            return RunResult(success=True, text="Done.", usage=Usage("mock", "model", 1, 1, 0.0), raw={})
        return results.pop(0)

    backend.run.side_effect = _next_result
    return backend


def test_end_to_end_run_uses_real_tools(tmp_path):
    """A full run where the agent uses ReadFile and Shell, verifies, and pushes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "math.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_math.py").write_text(
        "from src.math import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    review_tool_call = json.dumps({"name": "ReadFile", "args": {"path": "src/math.py"}})
    review = f"""
1. Read src/math.py.
<tool_call>
{review_tool_call}
</tool_call>
"""
    review_final = "Plan: add subtraction to src/math.py and test it."

    build_tool_call = json.dumps(
        {
            "name": "WriteFile",
            "args": {
                "path": "src/math.py",
                "content": "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n",
            },
        }
    )
    build_tool = f"""
<tool_call>
{build_tool_call}
</tool_call>
"""
    build_final = "Added subtraction.\nVERIFY_RESULT: PASS"

    verify_tool_call = json.dumps(
        {"name": "Shell", "args": {"command": "PYTHONPATH=src python -m pytest tests/test_math.py"}}
    )
    verify_tool = f"""
<tool_call>
{verify_tool_call}
</tool_call>
"""
    verify_final = "Ran tests.\nVERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _backend_with_responses(
            [review, review_final, build_tool, build_final, verify_tool, verify_final]
        )
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow(
                    "add subtraction",
                    config,
                    str(repo),
                    database_path=tmp_path / "integration.db",
                )

    assert state.steps[0]["role"] == "review"
    assert state.steps[1]["role"] == "build"
    assert state.steps[2]["role"] == "verify"
    assert len(state.tool_calls) >= 2
    assert any(c["tool_name"] == "ReadFile" for c in state.tool_calls)
    assert any(c["tool_name"] == "WriteFile" for c in state.tool_calls)
    assert state.pushed == {"pushed": True}

    # Verify the file was actually written by the tool.
    assert "def sub(a, b)" in (repo / "src" / "math.py").read_text()


def test_validation_confirms_cost_tracking(tmp_path):
    """Cost and token usage are accumulated across tool-augmented steps."""
    repo = tmp_path / "repo"
    repo.mkdir()

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    responses = [
        "Plan: do nothing.",
        "Done. VERIFY_RESULT: PASS",
        "VERIFY_RESULT: PASS",
    ]

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _backend_with_responses(responses)
        backend.run.side_effect = [
            RunResult(success=True, text=t, usage=Usage("mock", "model", i + 1, i + 1, float(i + 1) / 100), raw={})
            for i, t in enumerate(responses)
        ]
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow(
                    "cheap task",
                    config,
                    str(repo),
                    database_path=tmp_path / "integration.db",
                )

    totals = state.total_usage()
    assert "mock:model" in totals
    assert totals["mock:model"]["input_tokens"] == 6  # 1 + 2 + 3
    assert totals["mock:model"]["output_tokens"] == 6
    assert totals["mock:model"]["cost_usd"] == 0.06
