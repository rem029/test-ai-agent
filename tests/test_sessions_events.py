"""Tests for sessions and events persistence, reconstruct_run, and follow-up turns."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.cli import main as cli_main
from agentflow.config import Config, RoleConfig
from agentflow.database import (
    append_event,
    create_session,
    get_session,
    get_session_runs,
    get_tool_calls,
    list_events,
    list_sessions,
    reconstruct_run,
    save_run,
)
from agentflow.orchestrator import RunState, run_workflow


def _make_mock_backend(texts: list[str]):
    backend = MagicMock()
    responses = [
        RunResult(success=True, text=t, usage=Usage("mock", "model", 1, 1, 0.001), raw={})
        for t in texts
    ]
    backend.run.side_effect = responses
    return backend


def test_sessions_crud(tmp_path):
    db_path = tmp_path / "test.db"
    cwd = str(tmp_path)

    create_session("sess-1", cwd=cwd, title="First Session", path=db_path)
    sess = get_session("sess-1", path=db_path)
    assert sess is not None
    assert sess["session_id"] == "sess-1"
    assert sess["title"] == "First Session"

    sessions = list_sessions(cwd=cwd, path=db_path)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sess-1"


def test_events_crud_and_reconstruct_run(tmp_path):
    db_path = tmp_path / "test.db"
    run_id = "run-123"

    append_event(run_id, 1, "run_started", {
        "run_id": run_id,
        "session_id": "sess-abc",
        "goal": "Build widget",
        "cwd": str(tmp_path),
        "config": {},
        "started_at": 1000.0,
    }, ts=1000.0, path=db_path)

    append_event(run_id, 2, "tool_result", {
        "step_index": 0,
        "tool_name": "ReadFile",
        "args": {"path": "widget.py"},
        "result": {"output": "def widget(): pass"},
        "status": "OK",
        "execution_time_ms": 10,
        "error": None,
    }, ts=1001.0, path=db_path)

    append_event(run_id, 3, "step_finished", {
        "step": {
            "role": "review",
            "mode": "read",
            "iteration": 0,
            "success": True,
            "text": "1. Build widget",
            "usage": {"backend": "mock", "model": "m", "input_tokens": 5, "output_tokens": 5, "cost_usd": 0.001},
        }
    }, ts=1002.0, path=db_path)

    append_event(run_id, 4, "run_finished", {
        "finished_at": 1005.0,
        "pushed": {"pushed": True, "commit": "sha123"},
    }, ts=1005.0, path=db_path)

    events = list_events(run_id, path=db_path)
    assert len(events) == 4
    assert [e["seq"] for e in events] == [1, 2, 3, 4]

    reconstructed = reconstruct_run(run_id, path=db_path)
    assert reconstructed is not None
    assert reconstructed["run_id"] == run_id
    assert reconstructed["session_id"] == "sess-abc"
    assert reconstructed["goal"] == "Build widget"
    assert reconstructed["started_at"] == 1000.0
    assert reconstructed["finished_at"] == 1005.0
    assert len(reconstructed["steps"]) == 1
    assert reconstructed["steps"][0]["role"] == "review"
    assert len(reconstructed["tool_calls"]) == 1
    assert reconstructed["tool_calls"][0]["tool_name"] == "ReadFile"
    assert reconstructed["pushed"] == {"pushed": True, "commit": "sha123"}


def test_tool_calls_append_only(tmp_path):
    db_path = tmp_path / "test.db"
    cwd = str(tmp_path)
    state = RunState(
        run_id="run-append-test",
        goal="Test append",
        started_at=100.0,
        config={},
    )
    state.add_tool_call(
        step_index=0,
        tool_name="ReadFile",
        args={"path": "a.txt"},
        result={"output": "a", "duration_ms": 5},
    )
    save_run(state, cwd=cwd, path=db_path)

    calls1 = get_tool_calls("run-append-test", cwd=cwd, path=db_path)
    assert len(calls1) == 1

    # Add a second tool call and save again
    state.add_tool_call(
        step_index=1,
        tool_name="WriteFile",
        args={"path": "b.txt", "content": "b"},
        result={"output": "wrote b.txt", "duration_ms": 8},
    )
    save_run(state, cwd=cwd, path=db_path)

    calls2 = get_tool_calls("run-append-test", cwd=cwd, path=db_path)
    assert len(calls2) == 2
    # Verify no duplicates
    tool_names = [c["tool_name"] for c in calls2]
    assert "ReadFile" in tool_names
    assert "WriteFile" in tool_names


def test_follow_up_turn_against_session(tmp_path):
    db_path = tmp_path / "test.db"
    cwd = str(tmp_path)
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    # First turn
    turn1_responses = [
        "Plan: create initial feature.",
        "Created feature.\nVERIFY_RESULT: PASS",
        "VERIFY_RESULT: PASS",
    ]
    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend1 = _make_mock_backend(turn1_responses)
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend1)
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state1 = run_workflow("initial feature", config, cwd, session_id="session-1", database_path=db_path)

    assert state1.session_id == "session-1"
    runs = get_session_runs("session-1", path=db_path)
    assert len(runs) == 1

    # Second turn (follow-up)
    turn2_responses = [
        "Plan: follow-up enhancement.",
        "Added enhancement.\nVERIFY_RESULT: PASS",
        "VERIFY_RESULT: PASS",
    ]
    captured_prompts = []

    def capturing_backend_run(prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        return RunResult(success=True, text="Plan: follow-up enhancement. VERIFY_RESULT: PASS", usage=Usage("mock", "m", 1, 1, 0.0), raw={})

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend2 = MagicMock()
        backend2.run.side_effect = capturing_backend_run
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend2)
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state2 = run_workflow("follow-up improvement", config, cwd, session_id="session-1", database_path=db_path)

    assert state2.session_id == "session-1"
    runs_after = get_session_runs("session-1", path=db_path)
    assert len(runs_after) == 2

    # Assert session title stays as the first goal after a second run_workflow (FIX 2)
    session = get_session("session-1", path=db_path)
    assert session is not None
    assert session["title"] == "initial feature"

    # Check that captured prompts contain context from prior run
    found_prior_context = any(
        "Prior runs in this session" in (p[0].content if isinstance(p, list) else str(p))
        for p in captured_prompts
    )
    assert found_prior_context


def test_cli_resume_validation(tmp_path, capsys):
    # Missing session
    rc = cli_main(["--resume", "nonexistent-session", "do something"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "session not found" in captured.err.lower()

    # Resume without goal enters REPL for valid session
    create_session("some-session", cwd=str(Path.cwd()), title="Some task")
    with patch("agentflow.tui.run_repl", return_value=0) as mock_repl:
        rc2 = cli_main(["--resume", "some-session"])
        assert rc2 == 0
        mock_repl.assert_called_once()



def test_cli_list_sessions(tmp_path, capsys):
    db_path = tmp_path / "test.db"
    create_session("sess-xyz", cwd=str(Path.cwd()), title="Demo Task")

    rc = cli_main(["--list-sessions"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "sess-xyz" in captured.out
