"""Tests for web UI message, stop, and concurrency endpoints (src/agentflow/web/app.py)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import agentflow.web.app as web_app
from agentflow.database import (
    get_pending_messages,
    has_stop_signal,
    pop_next_queued_run,
    save_run,
)
from agentflow.orchestrator import RunState


def _make_client(tmp_path: Path) -> TestClient:
    app = web_app.create_app(
        cwd=str(tmp_path),
        config_path=str(tmp_path / "agentflow.config.yaml"),
        database_path=tmp_path / "agentflow.db",
    )
    return TestClient(app)


def _seed_run(tmp_path: Path, run_id: str = "run-seed-1", session_id: str | None = None) -> RunState:
    state = RunState(
        run_id=run_id,
        session_id=session_id,
        goal="Test seed run",
        started_at=time.time(),
        config={},
    )
    save_run(state, str(tmp_path), tmp_path / "agentflow.db")
    return state


def test_post_message_and_get_messages(tmp_path):
    _seed_run(tmp_path, run_id="run-msg-1")
    client = _make_client(tmp_path)

    # 404 for nonexistent run
    resp_404 = client.post("/api/runs/nonexistent/messages", json={"body": "hello", "kind": "steer"})
    assert resp_404.status_code == 404

    # Post steer message
    resp1 = client.post("/api/runs/run-msg-1/messages", json={"body": "steer msg 1", "kind": "steer"})
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["ok"] is True
    assert isinstance(data1["id"], int)

    # Post note message
    resp2 = client.post("/api/runs/run-msg-1/messages", json={"body": "note msg 1", "kind": "note"})
    assert resp2.status_code == 200
    assert resp2.json()["ok"] is True

    # GET messages
    get_resp = client.get("/api/runs/run-msg-1/messages")
    assert get_resp.status_code == 200
    msgs = get_resp.json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["body"] == "steer msg 1"
    assert msgs[0]["kind"] == "steer"
    assert msgs[1]["body"] == "note msg 1"
    assert msgs[1]["kind"] == "note"

    # GET messages 404 for nonexistent run
    assert client.get("/api/runs/nonexistent/messages").status_code == 404


def test_post_stop_endpoint(tmp_path):
    _seed_run(tmp_path, run_id="run-stop-1")
    client = _make_client(tmp_path)

    # 404 for nonexistent run
    resp_404 = client.post("/api/runs/nonexistent/stop")
    assert resp_404.status_code == 404

    # Stop existing run
    resp = client.post("/api/runs/run-stop-1/stop")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    assert has_stop_signal("run-stop-1", path=tmp_path / "agentflow.db") is True


def test_create_run_routing_when_active_run_exists(tmp_path):
    _seed_run(tmp_path, run_id="run-active-1", session_id="session-active")
    client = _make_client(tmp_path)

    # Mock active run in orchestrator
    with patch("agentflow.web.app.get_active_run", return_value="run-active-1"):
        # Even with same session_id, a new POST /api/runs creates a queued run
        resp_sess = client.post(
            "/api/runs",
            json={"goal": "Refine the active run", "session_id": "session-active"},
        )
        assert resp_sess.status_code == 200
        data_sess = resp_sess.json()
        assert data_sess["status"] == "queued"
        assert "queue_id" in data_sess

        # Verify queued run was stored
        queued1 = pop_next_queued_run(str(tmp_path), path=tmp_path / "agentflow.db")
        assert queued1 is not None
        assert queued1["goal"] == "Refine the active run"
        assert queued1["session_id"] == "session-active"

        # Another run with different session_id also queues
        resp_queue = client.post(
            "/api/runs",
            json={"goal": "A new unrelated task", "session_id": "session-other"},
        )
        assert resp_queue.status_code == 200
        data_queue = resp_queue.json()
        assert data_queue["status"] == "queued"
        assert "queue_id" in data_queue

        # Verify queued run was stored
        queued2 = pop_next_queued_run(str(tmp_path), path=tmp_path / "agentflow.db")
        assert queued2 is not None
        assert queued2["goal"] == "A new unrelated task"
        assert queued2["session_id"] == "session-other"


def test_api_events_404_for_unknown_run(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/runs/nonexistent-run/events")
    assert resp.status_code == 404

