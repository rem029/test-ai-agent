"""Tests for the agentflow web admin panel (src/agentflow/web/app.py).

Mirrors tests/test_cli.py's mocking style: run_workflow is always mocked -
these tests never make a real backend call.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import agentflow.web.app as web_app
from agentflow.config import Config, RoleConfig


def _config() -> Config:
    return Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )


def _reset_active_run():
    web_app._active_run = None


def _make_client(tmp_path: Path, config_path: str | None = None) -> TestClient:
    _reset_active_run()
    app = web_app.create_app(cwd=str(tmp_path), config_path=config_path or str(tmp_path / "agentflow.config.yaml"))
    return TestClient(app)


def test_dashboard_empty(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No runs yet" in resp.text


def test_dashboard_lists_existing_run_fixture(tmp_path):
    runs_dir = tmp_path / ".agentflow" / "runs"
    runs_dir.mkdir(parents=True)
    run = {
        "run_id": "20260101-000000-aaaaaaaa",
        "goal": "add a --version flag",
        "started_at": time.time(),
        "config": {},
        "steps": [],
        "finished_at": time.time(),
        "pushed": {"branch": "dev", "commit": "deadbeef", "pushed": True},
    }
    (runs_dir / f"{run['run_id']}.json").write_text(json.dumps(run))

    client = _make_client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "add a --version flag" in resp.text
    assert "pushed" in resp.text

    detail = client.get(f"/runs/{run['run_id']}")
    assert detail.status_code == 200
    assert "add a --version flag" in detail.text


def test_run_detail_missing_returns_404(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/runs/does-not-exist")
    assert resp.status_code == 404


def test_create_run_spawns_background_thread_and_redirects(tmp_path):
    _reset_active_run()
    with patch("agentflow.web.app.load_config", return_value=_config()), patch(
        "agentflow.web.app.run_workflow"
    ) as mock_run:
        app = web_app.create_app(cwd=str(tmp_path), config_path=str(tmp_path / "agentflow.config.yaml"))
        client = TestClient(app)

        resp = client.post("/runs", data={"goal": "test goal"}, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/runs/")

        thread = app.state.last_thread
        assert thread is not None
        thread.join(timeout=5)
        assert not thread.is_alive()

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["run_id"] == resp.headers["location"].split("/runs/")[1]


def test_second_run_blocked_while_active(tmp_path):
    _reset_active_run()
    started = threading.Event()
    release = threading.Event()

    def _slow_run_workflow(goal, config, cwd, run_id=None):
        started.set()
        release.wait(timeout=5)

    with patch("agentflow.web.app.load_config", return_value=_config()), patch(
        "agentflow.web.app.run_workflow", side_effect=_slow_run_workflow
    ):
        app = web_app.create_app(cwd=str(tmp_path), config_path=str(tmp_path / "agentflow.config.yaml"))
        client = TestClient(app)

        first = client.post("/runs", data={"goal": "first goal"}, follow_redirects=False)
        assert first.status_code == 303
        first_run_id = first.headers["location"].split("/runs/")[1]
        assert started.wait(timeout=5)

        second = client.post("/runs", data={"goal": "second goal"}, follow_redirects=False)
        assert second.status_code == 303
        assert second.headers["location"].split("/runs/")[1] == first_run_id

        release.set()
        app.state.last_thread.join(timeout=5)


def test_config_form_rejects_invalid_backend(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))

    resp = client.post(
        "/config",
        data={
            "review_backend": "not-a-real-backend",
            "review_model": "",
            "build_backend": "claude-code",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": "3",
        },
    )
    assert resp.status_code == 422
    assert not config_path.exists()


def test_config_form_writes_valid_yaml_roundtrip(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))

    resp = client.post(
        "/config",
        data={
            "review_backend": "claude-code",
            "review_model": "",
            "build_backend": "openrouter",
            "build_model": "deepseek/deepseek-v4-flash",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": "5",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
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

