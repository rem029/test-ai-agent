"""Tests for step record synthesis when backend returns no prose or only tool calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.orchestrator import (
    RunState,
    _record,
    _step_tool_summary,
    run_workflow,
)


def _make_mock_backend(results: list[RunResult]):
    backend = MagicMock()
    backend.name = "mock"
    backend.model = "mock-model"
    backend.run.side_effect = results
    return backend


def test_step_tool_summary_empty():
    state = RunState(run_id="test", goal="test", started_at=100.0, config={})
    count, names = _step_tool_summary(state, 0)
    assert count == 0
    assert names == ""


def test_step_tool_summary_dedup_and_cap():
    state = RunState(run_id="test", goal="test", started_at=100.0, config={})
    # Add tool calls for step 1
    tool_names = [
        "ReadFile",
        "Shell",
        "ReadFile",
        "ListDirectory",
        "SearchFiles",
        "CodeSearch",
        "WebFetch",
        "Lint",
    ]
    for name in tool_names:
        state.add_tool_call(
            step_index=1,
            tool_name=name,
            args={},
            result={"success": True},
        )
    # Add a tool call for step 2 (should not be counted in step 1)
    state.add_tool_call(
        step_index=2,
        tool_name="GitStatus",
        args={},
        result={"success": True},
    )

    count, names = _step_tool_summary(state, 1)
    assert count == 8
    # Distinct names in order: ReadFile, Shell, ListDirectory, SearchFiles, CodeSearch, WebFetch, Lint (7 distinct)
    # Capped at 6 then ", …"
    expected = "ReadFile, Shell, ListDirectory, SearchFiles, CodeSearch, WebFetch, …"
    assert names == expected


def test_record_helper_prose():
    res = RunResult(success=True, text="Here is the detailed plan for the task.", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    rec = _record("review", "read", 0, res)
    assert rec["no_response"] is False
    assert rec["text"] == "Here is the detailed plan for the task."


def test_record_helper_no_prose_without_tools():
    res = RunResult(success=True, text="   \n  ", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    rec = _record("verify", "verify", 1, res)
    assert rec["no_response"] is True
    assert rec["text"] == "_The verify step completed without a written summary._"


def test_record_helper_no_prose_with_tools():
    res = RunResult(success=True, text="  ", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    rec = _record("build", "write", 1, res, tool_count=2, tool_names="ReadFile, Shell")
    assert rec["no_response"] is True
    assert rec["text"] == (
        "_The build step completed without a written summary._ "
        "It ran 2 tool call(s) this step (ReadFile, Shell)."
    )


def test_workflow_verify_step_no_prose(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_iterations=1,
    )

    # Review returns plan, build returns done, verify returns blank whitespace
    review_res = RunResult(success=True, text="Plan: step 1", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    build_res = RunResult(success=True, text="Done.", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    verify_res = RunResult(success=True, text="   ", usage=Usage("m", "m", 1, 1, 0.0), raw={})

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_mock_backend([review_res, build_res, verify_res])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("verify blank goal", config, str(repo), database_path=db_path)

    assert len(state.steps) >= 3
    verify_step = state.steps[2]
    assert verify_step["role"] == "verify"
    assert verify_step["no_response"] is True
    assert "without a written summary" in verify_step["text"]


def test_workflow_verify_step_runs_tools_then_no_prose(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test.txt").write_text("content")
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_iterations=1,
    )

    review_res = RunResult(success=True, text="Plan: verify file", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    build_res = RunResult(success=True, text="Build done.", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    # Verify first runs ReadFile tool, then returns blank whitespace
    verify_tool_call = RunResult(
        success=True,
        text='<tool_call>\n{"name": "ReadFile", "args": {"path": "test.txt"}}\n</tool_call>',
        usage=Usage("m", "m", 1, 1, 0.0),
        raw={},
    )
    verify_final = RunResult(success=True, text="  ", usage=Usage("m", "m", 1, 1, 0.0), raw={})

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_mock_backend([review_res, build_res, verify_tool_call, verify_final])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("verify tool goal", config, str(repo), database_path=db_path)

    verify_step = state.steps[2]
    assert verify_step["role"] == "verify"
    assert verify_step["no_response"] is True
    assert "without a written summary" in verify_step["text"]
    assert "ReadFile" in verify_step["text"]
    assert "It ran 1 tool call(s) this step" in verify_step["text"]


def test_workflow_review_failure_records_fatal_blocker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    review_fail = RunResult(success=False, text="API connection failed", usage=Usage("m", "m", 1, 1, 0.0), raw={})

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_mock_backend([review_fail])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("review fail goal", config, str(repo), database_path=db_path)

    assert state.finished_at is not None
    assert len(state.blockers) == 1
    assert state.blockers[0]["reason"] == "backend_error"
    assert state.blockers[0]["fatal"] is True
    assert "API connection failed" in state.blockers[0]["detail"]
    assert state.blockers[0]["step_index"] == 0


def test_workflow_build_failure_records_non_fatal_blocker(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_iterations=1,
    )

    review_res = RunResult(success=True, text="Plan: step 1", usage=Usage("m", "m", 1, 1, 0.0), raw={})
    build_fail = RunResult(success=False, text="Rate limit hit", usage=Usage("m", "m", 1, 1, 0.0), raw={})

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_mock_backend([review_res, build_fail])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("build fail goal", config, str(repo), database_path=db_path)

    assert state.finished_at is not None
    assert len(state.blockers) == 1
    assert state.blockers[0]["reason"] == "backend_error"
    assert state.blockers[0]["fatal"] is False
    assert "Rate limit hit" in state.blockers[0]["detail"]
    assert state.blockers[0]["step_index"] == 1
