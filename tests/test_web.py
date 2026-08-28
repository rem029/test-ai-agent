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


def test_api_config_preserves_permissions_and_max_cost(tmp_path):
    from agentflow.config import dump_config, load_config
    config_path = tmp_path / "agentflow.config.yaml"
    initial = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        permissions="deny",
        max_cost_usd=1.75,
    )
    dump_config(initial, str(config_path), openrouter_api_key="sk-secret-key")

    client = _make_client(tmp_path, config_path=str(config_path))
    resp = client.post(
        "/api/config",
        json={
            "review_backend": "openrouter",
            "review_model": "deepseek/deepseek-v4-flash",
            "build_backend": "antigravity",
            "build_model": None,
            "verify_backend": "claude-code",
            "verify_model": None,
            "max_iterations": 4,
        },
    )
    assert resp.status_code == 200

    reloaded = load_config(str(config_path))
    assert reloaded.review.backend == "openrouter"
    assert reloaded.permissions == "deny"
    assert reloaded.max_cost_usd == 1.75
    # Verify openrouter_api_key in file was also preserved
    from agentflow.config import _from_file
    assert _from_file(str(config_path)).get("openrouter_api_key") == "sk-secret-key"


def test_api_create_run_preserves_permissions_and_max_cost(tmp_path):
    custom_cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
        permissions="prompt",
        max_cost_usd=0.85,
    )
    with patch("agentflow.web.app.load_config", return_value=custom_cfg), patch(
        "agentflow.web.app.run_workflow"
    ) as mock_run:
        client = _make_client(tmp_path)
        resp = client.post("/api/runs", json={"goal": "test overrides", "build_backend": "antigravity"})
        assert resp.status_code == 200

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        passed_config = kwargs["config"]
        assert passed_config.build.backend == "antigravity"
        assert passed_config.permissions == "prompt"
        assert passed_config.max_cost_usd == 0.85


def test_api_events_and_sessions_endpoints(tmp_path):
    from agentflow.database import append_event, create_session, save_run
    from agentflow.orchestrator import RunState

    db = tmp_path / "agentflow.db"
    create_session("sess-web-1", str(tmp_path), title="Web Session 1", path=db)
    state = RunState(run_id="run-web-1", session_id="sess-web-1", goal="Test Web 1", started_at=1.0, config={})
    save_run(state, str(tmp_path), db)
    append_event("run-web-1", 1, "step_started", {"role": "review"}, path=db)

    client = _make_client(tmp_path)

    # Sessions list
    sess_resp = client.get("/api/sessions")
    assert sess_resp.status_code == 200
    assert len(sess_resp.json()["sessions"]) == 1
    assert sess_resp.json()["sessions"][0]["session_id"] == "sess-web-1"

    # Session detail
    detail_resp = client.get("/api/sessions/sess-web-1")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["title"] == "Web Session 1"

    # Missing session
    assert client.get("/api/sessions/sess-missing").status_code == 404

    # Events
    events_resp = client.get("/api/runs/run-web-1/events")
    assert events_resp.status_code == 200
    assert len(events_resp.json()["events"]) == 1
    assert events_resp.json()["events"][0]["type"] == "step_started"


def test_static_assets_contain_tool_req_support(tmp_path):
    client = _make_client(tmp_path)
    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "splitToolBlocks" in js_resp.text
    assert "renderToolReq" in js_resp.text
    assert "tool-req-name" in js_resp.text

    css_resp = client.get("/static/styles.css")
    assert css_resp.status_code == 200
    assert ".tool-req" in css_resp.text
    assert ".tool-req-name" in css_resp.text


def test_js_split_tool_blocks_via_node():
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        return

    script = """
    const md = require('./src/agentflow/web/static/md.js');
    global.renderMarkdown = md.renderMarkdown;
    const app = require('./src/agentflow/web/static/app.js');

    // 1. Symmetric DSML tool call
    const t1 = '\\n\\n<｜DSML｜tool_call>\\n{"name": "ListDirectory", "args": {"path": ".", "recursive": true}}\\n</｜DSML｜tool_call>';
    const r1 = app.splitToolBlocks(t1);
    if (r1.requests.length !== 1 || r1.requests[0].name !== 'ListDirectory' || r1.prose !== '') {
        process.exit(1);
    }

    // 2. Asymmetric plain-open, DSML-close
    const t2 = '<tool_call>\\n{"name": "ReadFile", "args": {"path": "foo.py"}}\\n</｜DSML｜tool_call>';
    const r2 = app.splitToolBlocks(t2);
    if (r2.requests.length !== 1 || r2.requests[0].name !== 'ReadFile' || r2.prose !== '') {
        process.exit(2);
    }

    // 3. Bare JSON line
    const t3 = 'Intro text\\n{"name": "Shell", "args": {"command": "ls"}}\\nOutro text';
    const r3 = app.splitToolBlocks(t3);
    if (r3.requests.length !== 1 || r3.requests[0].name !== 'Shell' || !r3.prose.includes('Intro text') || !r3.prose.includes('Outro text')) {
        process.exit(3);
    }

    // 4. Chip formatting
    const chip = app.renderToolReq(r1.requests[0]);
    if (!chip.includes('tool-req-name') || !chip.includes('ListDirectory') || !chip.includes('path=&quot;.&quot;')) {
        process.exit(4);
    }

    // 5. renderStep output
    const stepHtml = app.renderStep({ role: 'build', text: t1, success: true }, 0);
    if (!stepHtml.includes('tool-req-name') || stepHtml.includes('<｜DSML｜tool_call>')) {
        process.exit(5);
    }
    """
    res = subprocess.run([node_bin, "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script failed with: {res.stderr}"
