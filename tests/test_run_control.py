"""Tests for run control: steer messages, stop signals, single-run locking, and session hand-off."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.database import (
    add_control_signal,
    add_pending_message,
    get_pending_messages,
    list_events,
)
from agentflow.orchestrator import RunInProgressError, RunState, run_workflow


def _make_mock_backend(run_fn):
    backend = MagicMock()
    backend.name = "mock"
    backend.model = "m"
    backend.run.side_effect = run_fn
    return backend



def test_steer_message_drained_at_checkpoint(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"
    run_id = "run-steer-chkpt"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_iterations=2,
    )

    prompts_seen = []
    call_count = 0

    def backend_run(messages, cwd, mode):
        nonlocal call_count
        call_count += 1
        prompt = messages[0].content if messages else ""
        prompts_seen.append((mode, prompt))

        # When review completes, add a steer message before build loop
        if mode == "read":
            add_pending_message(run_id, "Make sure to add docstrings", kind="steer", path=db_path)
            add_pending_message(run_id, "FYI internal note", kind="note", path=db_path)
            return RunResult(success=True, text="1. Plan breakdown", usage=Usage("mock", "m", 1, 1, 0.0), raw={})
        elif mode == "write":
            return RunResult(success=True, text="Implemented feature", usage=Usage("mock", "m", 1, 1, 0.0), raw={})
        elif mode == "verify":
            return RunResult(success=True, text="Verified\nVERIFY_RESULT: PASS", usage=Usage("mock", "m", 1, 1, 0.0), raw={})
        return RunResult(success=True, text="ok", usage=Usage("mock", "m", 1, 1, 0.0), raw={})

    mock_backend = _make_mock_backend(backend_run)

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: mock_backend)
        state = run_workflow(
            "Add docstrings goal",
            config,
            str(repo),
            run_id=run_id,
            database_path=db_path,
        )

    assert state.finished_at is not None
    assert not state.stopped

    # Check that the build prompt received the steer message but NOT the note message text
    build_prompts = [p for m, p in prompts_seen if m == "write"]
    assert len(build_prompts) >= 1
    assert "User added while running:" in build_prompts[0]
    assert "- Make sure to add docstrings" in build_prompts[0]
    assert "FYI internal note" not in build_prompts[0]

    # Verify user_message events were logged for both steer and note
    events = list_events(run_id, path=db_path)
    user_msg_events = [e for e in events if e["type"] == "user_message"]
    assert len(user_msg_events) == 2
    assert {e["payload"]["kind"] for e in user_msg_events} == {"steer", "note"}


def test_stop_signal_before_step(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"
    run_id = "run-stop-before"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    # Set stop signal before running workflow
    add_control_signal(run_id, "stop", path=db_path)

    backend_calls = []

    def backend_run(messages, cwd, mode):
        backend_calls.append(mode)
        return RunResult(success=True, text="Should not be reached", usage=Usage("mock", "m", 1, 1, 0.0), raw={})

    mock_backend = _make_mock_backend(backend_run)

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push") as mock_push:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: mock_backend)
        state = run_workflow(
            "Test stop signal",
            config,
            str(repo),
            run_id=run_id,
            database_path=db_path,
        )

    assert state.stopped is True
    assert state.finished_at is not None
    assert state.pushed is None
    mock_push.assert_not_called()
    assert len(backend_calls) == 0  # Stopped before backend was called


def test_stop_signal_during_tool_execution(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"
    run_id = "run-stop-tool"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    # Setup a backend that requests two tool calls:
    tool_call_text = (
        '<tool_call>\n{"name": "ReadFile", "args": {"path": "a.txt"}}\n</tool_call>\n'
        '<tool_call>\n{"name": "ReadFile", "args": {"path": "b.txt"}}\n</tool_call>'
    )

    def backend_run(messages, cwd, mode):
        # On first review call, request tools and simultaneously send stop signal
        add_control_signal(run_id, "stop", path=db_path)
        return RunResult(success=True, text=tool_call_text, usage=Usage("mock", "m", 1, 1, 0.0), raw={})

    mock_backend = _make_mock_backend(backend_run)

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push") as mock_push, \
         patch("agentflow.orchestrator._execute_tool_call", return_value=MagicMock(success=True, output="dummy", duration_ms=1, error=None, model_dump_truncated=lambda: {})):
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: mock_backend)
        state = run_workflow(
            "Test stop in tool loop",
            config,
            str(repo),
            run_id=run_id,
            database_path=db_path,
        )

    assert state.stopped is True
    assert state.finished_at is not None
    assert state.pushed is None
    mock_push.assert_not_called()

    # Events should include run_stopped
    events = list_events(run_id, path=db_path)
    stop_events = [e for e in events if e["type"] == "run_stopped"]
    assert len(stop_events) == 1


def test_single_active_run_lock(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    started_event = threading.Event()
    continue_event = threading.Event()

    def blocking_backend_run(messages, cwd, mode):
        started_event.set()
        continue_event.wait(timeout=5)
        return RunResult(success=True, text="1. Plan\nVERIFY_RESULT: PASS", usage=Usage("mock", "m", 1, 1, 0.0), raw={})

    mock_backend = _make_mock_backend(blocking_backend_run)

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: mock_backend)

        thread = threading.Thread(
            target=run_workflow,
            kwargs={
                "goal": "First concurrent goal",
                "config": config,
                "cwd": str(repo),
                "run_id": "run-conc-1",
                "database_path": db_path,
            },
        )
        thread.start()

        # Wait until first run starts and acquires the lock
        assert started_event.wait(timeout=5)

        # Attempt second run on the same repo
        with pytest.raises(RunInProgressError) as exc_info:
            run_workflow(
                "Second concurrent goal",
                config,
                str(repo),
                run_id="run-conc-2",
                database_path=db_path,
            )
        assert str(repo.resolve()) in str(exc_info.value)

        # Allow first run to complete
        continue_event.set()
        thread.join(timeout=5)


def test_session_handoff_unconsumed_messages(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"
    session_id = "sess-handoff-1"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    # First turn completes normally
    turn1_responses = [
        RunResult(success=True, text="1. First plan", usage=Usage("mock", "m", 1, 1, 0.0), raw={}),
        RunResult(success=True, text="Implemented first turn", usage=Usage("mock", "m", 1, 1, 0.0), raw={}),
        RunResult(success=True, text="VERIFY_RESULT: PASS", usage=Usage("mock", "m", 1, 1, 0.0), raw={}),
    ]
    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
        b1 = _make_mock_backend(turn1_responses)
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: b1)
        state1 = run_workflow(
            "Initial goal",
            config,
            str(repo),
            run_id="run-h-1",
            session_id=session_id,
            database_path=db_path,
        )

    # Now add an unconsumed steer message targeting run-h-1 after it finished
    add_pending_message("run-h-1", "Also add integration tests", kind="steer", path=db_path)

    # Start a second turn in the same session
    captured_prompts = []

    def capturing_backend_run(messages, cwd, mode):
        prompt = messages[0].content if messages else ""
        captured_prompts.append((mode, prompt))
        return RunResult(success=True, text="1. Second plan\nVERIFY_RESULT: PASS", usage=Usage("mock", "m", 1, 1, 0.0), raw={})

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
        b2 = _make_mock_backend(capturing_backend_run)
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: b2)
        state2 = run_workflow(
            "Follow-up goal",
            config,
            str(repo),
            run_id="run-h-2",
            session_id=session_id,
            database_path=db_path,
        )

    # Check that the unconsumed steer message from run-h-1 seeded run-h-2's first review prompt
    review_prompts = [p for m, p in captured_prompts if m == "read"]
    assert len(review_prompts) >= 1
    assert "User added while running:" in review_prompts[0]
    assert "- Also add integration tests" in review_prompts[0]

    # Verify that the message on run-h-1 is now marked consumed
    remaining = get_pending_messages("run-h-1", path=db_path)
    assert len(remaining) == 0


def test_stop_signal_during_verify_halts_before_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"
    run_id = "run-stop-verify"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    def backend_run(messages, cwd, mode):
        if mode == "read":
            return RunResult(success=True, text="1. Plan", usage=Usage("mock", "m", 1, 1, 0.0), raw={})
        elif mode == "write":
            return RunResult(success=True, text="Code done", usage=Usage("mock", "m", 1, 1, 0.0), raw={})
        elif mode == "verify":
            # Add stop signal during verify step (returns PASS with no tool calls)
            add_control_signal(run_id, "stop", path=db_path)
            return RunResult(success=True, text="Tests passed\nVERIFY_RESULT: PASS", usage=Usage("mock", "m", 1, 1, 0.0), raw={})
        return RunResult(success=True, text="ok", usage=Usage("mock", "m", 1, 1, 0.0), raw={})

    mock_backend = _make_mock_backend(backend_run)

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push") as mock_push:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: mock_backend)
        state = run_workflow(
            "Test stop during verify",
            config,
            str(repo),
            run_id=run_id,
            database_path=db_path,
        )

    assert state.stopped is True
    assert state.finished_at is not None
    assert state.pushed is None
    mock_push.assert_not_called()

    # Control signal must be consumed
    unconsumed = get_pending_messages(run_id, path=db_path)
    assert len(unconsumed) == 0


def test_backend_exception_finalizes_run_state(tmp_path):
    from agentflow.database import load_run

    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"
    run_id = "run-crash-test"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    def failing_backend_run(messages, cwd, mode):
        raise RuntimeError("Simulated API failure")

    mock_backend = _make_mock_backend(failing_backend_run)

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: mock_backend)
        with pytest.raises(RuntimeError, match="Simulated API failure"):
            run_workflow(
                "Failing goal",
                config,
                str(repo),
                run_id=run_id,
                database_path=db_path,
            )

    # Verify that run was saved and finalized with finished_at set despite the crash
    loaded = load_run(run_id, str(repo), path=db_path)
    assert loaded is not None
    assert loaded["finished_at"] is not None
    assert loaded["pushed"] is None


def test_cross_process_flock_contention(tmp_path, monkeypatch):
    import hashlib
    import fcntl

    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"
    locks_dir = tmp_path / "locks"
    monkeypatch.setattr("agentflow.orchestrator.AGENTFLOW_HOME", tmp_path)

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    resolved = str(repo.resolve())
    locks_dir.mkdir(parents=True, exist_ok=True)
    cwd_hash = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    lock_file = locks_dir / f"{cwd_hash}.lock"

    # Simulate another process holding the lock
    f = open(lock_file, "a+")
    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        with pytest.raises(RunInProgressError):
            run_workflow(
                "Goal with held flock",
                config,
                str(repo),
                run_id="run-flock-test",
                database_path=db_path,
            )
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()

