"""Tests for pending_messages, control signals, and queued_runs SQLite persistence."""

from __future__ import annotations

import pytest

from agentflow.database import (
    add_control_signal,
    add_pending_message,
    add_queued_run,
    drain_pending_messages,
    get_pending_messages,
    has_stop_signal,
    mark_messages_consumed,
    pop_next_queued_run,
)


def test_pending_messages_crud(tmp_path):
    db_path = tmp_path / "test.db"
    run_id = "run-test-1"

    # Add messages
    m1_id = add_pending_message(run_id, "Please use snake_case", kind="steer", path=db_path)
    m2_id = add_pending_message(run_id, "FYI: tests are in tests/", kind="note", path=db_path)
    assert isinstance(m1_id, int)
    assert isinstance(m2_id, int)
    assert m2_id > m1_id

    # Retrieve unconsumed
    unconsumed = get_pending_messages(run_id, path=db_path)
    assert len(unconsumed) == 2
    assert unconsumed[0]["id"] == m1_id
    assert unconsumed[0]["body"] == "Please use snake_case"
    assert unconsumed[0]["kind"] == "steer"
    assert unconsumed[0]["consumed"] is False

    # Filter by kind
    steer_only = get_pending_messages(run_id, kind="steer", path=db_path)
    assert len(steer_only) == 1
    assert steer_only[0]["id"] == m1_id

    note_only = get_pending_messages(run_id, kind="note", path=db_path)
    assert len(note_only) == 1
    assert note_only[0]["id"] == m2_id

    # Mark consumed
    mark_messages_consumed([m1_id], path=db_path)
    unconsumed_after = get_pending_messages(run_id, path=db_path)
    assert len(unconsumed_after) == 1
    assert unconsumed_after[0]["id"] == m2_id

    # include_consumed=True
    all_msgs = get_pending_messages(run_id, include_consumed=True, path=db_path)
    assert len(all_msgs) == 2
    assert all_msgs[0]["consumed"] is True
    assert all_msgs[1]["consumed"] is False


def test_drain_pending_messages(tmp_path):
    db_path = tmp_path / "test.db"
    run_id = "run-drain-test"

    add_pending_message(run_id, "steer 1", kind="steer", path=db_path)
    add_pending_message(run_id, "note 1", kind="note", path=db_path)
    add_pending_message(run_id, "control 1", kind="control", path=db_path)

    # Drain only steer and note
    drained = drain_pending_messages(run_id, kinds=("steer", "note"), path=db_path)
    assert len(drained) == 2
    assert [d["kind"] for d in drained] == ["steer", "note"]
    assert all(d["consumed"] is True for d in drained)

    # Subsequent drain of steer and note returns nothing
    drained_again = drain_pending_messages(run_id, kinds=("steer", "note"), path=db_path)
    assert drained_again == []

    # Control message remains unconsumed
    controls = get_pending_messages(run_id, kind="control", path=db_path)
    assert len(controls) == 1
    assert controls[0]["body"] == "control 1"
    assert controls[0]["consumed"] is False


def test_control_signals_and_has_stop_signal(tmp_path):
    db_path = tmp_path / "test.db"
    run_id = "run-control-test"

    # Initially no stop signal
    assert has_stop_signal(run_id, path=db_path) is False

    # Add stop signal
    sig_id = add_control_signal(run_id, "stop", path=db_path)
    assert isinstance(sig_id, int)

    # has_stop_signal returns True and does NOT consume it
    assert has_stop_signal(run_id, path=db_path) is True
    assert has_stop_signal(run_id, path=db_path) is True

    # Mark consumed
    mark_messages_consumed([sig_id], path=db_path)
    assert has_stop_signal(run_id, path=db_path) is False

    # Abort signal also works
    add_control_signal(run_id, "abort", path=db_path)
    assert has_stop_signal(run_id, path=db_path) is True


def test_queued_runs_crud(tmp_path):
    db_path = tmp_path / "test.db"
    cwd = "/repo/app"

    # Initially empty
    assert pop_next_queued_run(cwd, path=db_path) is None

    # Add queued runs
    q1 = add_queued_run(cwd, "First queued goal", session_id="sess-1", config={"max_iterations": 2}, path=db_path)
    q2 = add_queued_run(cwd, "Second queued goal", session_id="sess-2", config={"max_iterations": 4}, path=db_path)
    assert q1 > 0
    assert q2 > q1

    # Pop first
    next_run = pop_next_queued_run(cwd, path=db_path)
    assert next_run is not None
    assert next_run["id"] == q1
    assert next_run["goal"] == "First queued goal"
    assert next_run["session_id"] == "sess-1"
    assert next_run["config"] == {"max_iterations": 2}

    # Pop second
    next_run_2 = pop_next_queued_run(cwd, path=db_path)
    assert next_run_2 is not None
    assert next_run_2["id"] == q2
    assert next_run_2["goal"] == "Second queued goal"

    # Nothing left
    assert pop_next_queued_run(cwd, path=db_path) is None
