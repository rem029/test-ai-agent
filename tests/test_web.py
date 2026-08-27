"""Tests for the agentflow web admin panel (src/agentflow/web/app.py).

Mirrors tests/test_cli.py's mocking style: run_workflow is always mocked -
these tests never make a real backend call.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import agentflow.web.app as web_app
from agentflow.config import Config, RoleConfig
from agentflow.database import save_run
from agentflow.orchestrator import RunState


def _config() -> Config:
    return Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )


def _make_client(tmp_path: Path, config_path: str | None = None) -> TestClient:
    app = web_app.create_app(
        cwd=str(tmp_path),
        config_path=config_path or str(tmp_path / "agentflow.config.yaml"),
        database_path=tmp_path / "agentflow.db",
    )
    return TestClient(app)


def test_index_serves_static_html(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_api_runs_lists_existing_run_fixture(tmp_path):
    run = {
        "run_id": "20260101-000000-aaaaaaaa",
        "goal": "add a --version flag",
        "started_at": time.time(),
        "config": {},
        "steps": [],
        "tool_calls": [],
        "finished_at": time.time(),
        "pushed": {"branch": "dev", "commit": "deadbeef", "pushed": True},
    }
    save_run(RunState(**run), str(tmp_path), tmp_path / "agentflow.db")

    client = _make_client(tmp_path)
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["runs"]) == 1
    assert data["runs"][0]["goal"] == "add a --version flag"

    detail = client.get(f"/api/runs/{run['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["goal"] == "add a --version flag"


def test_api_run_detail_missing_returns_404(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/runs/does-not-exist")
    assert resp.status_code == 404


def test_api_create_run_spawns_background_thread(tmp_path):
    with patch("agentflow.web.app.load_config", return_value=_config()), patch(
        "agentflow.web.app.run_workflow"
    ) as mock_run:
        client = _make_client(tmp_path)
        resp = client.post("/api/runs", json={"goal": "test goal"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"
        assert "run_id" in data

        # Give the background thread time to start and call run_workflow.
        import threading
        for thread in threading.enumerate():
            if thread.name.startswith("agentflow-"):
                thread.join(timeout=5)

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["goal"] == "test goal"
        assert kwargs["run_id"] == data["run_id"]


def test_api_config_form_rejects_invalid_backend(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))

    resp = client.post(
        "/api/config",
        json={
            "review_backend": "not-a-real-backend",
            "review_model": "",
            "build_backend": "claude-code",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 3,
        },
    )
    assert resp.status_code in (400, 422)
    assert not config_path.exists()


def test_api_config_form_writes_valid_yaml_roundtrip(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))

    resp = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "review_model": "",
            "build_backend": "openrouter",
            "build_model": "deepseek/deepseek-v4-flash",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 5,
        },
    )
    assert resp.status_code == 200
    assert config_path.exists()

    from agentflow.config import load_config

    reloaded = load_config(str(config_path))
    assert reloaded.build.backend == "openrouter"
    assert reloaded.build.model == "deepseek/deepseek-v4-flash"
    assert reloaded.max_iterations == 5


def test_api_models_endpoint(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "openrouter" in data
    assert "claude-code" in data
    assert "antigravity" in data


def test_api_run_tool_calls_endpoint(tmp_path):
    run = {
        "run_id": "20260101-000000-bbbbbbbb",
        "goal": "test tool calls api",
        "started_at": time.time(),
        "config": {},
        "steps": [],
        "tool_calls": [
            {
                "tool_name": "ReadFile",
                "args": {"path": "src/main.py"},
                "result": "content",
                "success": True,
                "timestamp": time.time(),
            }
        ],
        "finished_at": time.time(),
        "pushed": None,
    }
    save_run(RunState(**run), str(tmp_path), tmp_path / "agentflow.db")

    client = _make_client(tmp_path)
    resp = client.get(f"/api/runs/{run['run_id']}/tool_calls")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["tool_name"] == "ReadFile"


def test_api_run_tool_calls_missing_run_returns_404(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/runs/does-not-exist/tool_calls")
    assert resp.status_code == 404
