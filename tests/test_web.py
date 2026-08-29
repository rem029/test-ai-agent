"""Tests for the agentflow web admin panel (src/agentflow/web/app.py).

Mirrors tests/test_cli.py's mocking style: run_workflow is always mocked -
these tests never make a real backend call.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import agentflow.web.app as web_app
from agentflow.config import Config, MCPServerConfig, RoleConfig, dump_config, load_config
from agentflow.database import save_run
from agentflow.orchestrator import RunState

FAKE_SERVER_PATH = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")


def _config() -> Config:
    return Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )


def _make_client(
    tmp_path: Path,
    config_path: str | None = None,
    projects: list[str] | None = None,
) -> TestClient:
    app = web_app.create_app(
        cwd=str(tmp_path),
        config_path=config_path or str(tmp_path / "agentflow.config.yaml"),
        database_path=tmp_path / "agentflow.db",
        projects=projects,
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
    dump_config(initial, str(config_path))

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
    from agentflow.config import _from_file
    assert "openrouter_api_key" not in _from_file(str(config_path))


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

    css_resp = client.get("/static/console.css")
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

    // 6. Format B (nested invoke)
    const t6 = '<｜DSML｜tool_call>\\n<｜DSML｜invoke>ReadFile</｜DSML｜invoke>\\n<｜DSML｜invoke>{"path": ".agentflow-test-todo/index.html"}</｜DSML｜invoke>\\n</｜DSML｜tool_call>';
    const r6 = app.splitToolBlocks(t6);
    if (r6.requests.length !== 1 || r6.requests[0].name !== 'ReadFile' || r6.requests[0].args.path !== '.agentflow-test-todo/index.html' || r6.prose !== '' || r6.prose.includes('<') || r6.prose.includes('DSML')) {
        process.exit(6);
    }

    // 7. Format C (invoke name="X" with parameter tags)
    const t7 = '<｜DSML｜tool_calls>\\n<｜DSML｜invoke name="ListDirectory">\\n<｜DSML｜parameter>args</｜DSML｜parameter>\\n<｜DSML｜parameter>{"path": "."}</｜DSML｜parameter>\\n</｜DSML｜invoke>\\n</｜DSML｜tool_calls>';
    const r7 = app.splitToolBlocks(t7);
    if (r7.requests.length !== 1 || r7.requests[0].name !== 'ListDirectory' || r7.requests[0].args.path !== '.' || r7.prose !== '' || r7.prose.includes('<') || r7.prose.includes('DSML')) {
        process.exit(7);
    }

    // 8. Multiple invokes in tool_calls wrapper
    const t8 = '<｜DSML｜tool_calls>\\n<｜DSML｜invoke name="ReadFile">\\n<｜DSML｜parameter>{"path": "a.txt"}</｜DSML｜parameter>\\n</｜DSML｜invoke>\\n<｜DSML｜invoke name="WriteFile">\\n<｜DSML｜parameter>{"path": "b.txt", "content": "hello"}</｜DSML｜parameter>\\n</｜DSML｜invoke>\\n</｜DSML｜tool_calls>';
    const r8 = app.splitToolBlocks(t8);
    if (r8.requests.length !== 2 || r8.requests[0].name !== 'ReadFile' || r8.requests[1].name !== 'WriteFile' || r8.prose !== '' || r8.prose.includes('<') || r8.prose.includes('DSML')) {
        process.exit(8);
    }

    // 9. Well-formed step header with role, backend, model, cost, iter, and verdict PASS
    const verifyStep = {
        role: 'verify',
        iteration: 2,
        text: 'All tests pass.\\nVERIFY_RESULT: PASS',
        usage: { backend: 'openrouter', model: 'deepseek/deepseek-chat', cost_usd: 0.0142 }
    };
    const vHtml = app.renderStep(verifyStep, 0);
    if (!vHtml.includes('step-role') || !vHtml.includes('verify') || !vHtml.includes('iter 2') || !vHtml.includes('PASS') || !vHtml.includes('openrouter · deepseek/deepseek-chat') || !vHtml.includes('$0.0142')) {
        process.exit(9);
    }

    // 10. Failed verify step
    const failStep = {
        role: 'verify',
        iteration: 1,
        text: 'Tests failed.\\nVERIFY_RESULT: FAIL',
        usage: { backend: 'claude-code', model: 'claude-3-7-sonnet', cost_usd: 0.005 }
    };
    const fHtml = app.renderStep(failStep, 1);
    if (!fHtml.includes('FAIL') || !fHtml.includes('claude-code · claude-3-7-sonnet')) {
        process.exit(10);
    }

    // 11. renderRunHeader with full goal, state, cost, duration, and role models
    const testRun = {
        run_id: 'run-xyz-12345678',
        goal: 'Add an awesome new tape header block to the Transport console with full prompt text.',
        started_at: 100.0,
        finished_at: 220.0,
        config: {
            review: { backend: 'claude-code' },
            build: { backend: 'openrouter', model: 'deepseek-chat' },
            verify: { backend: 'antigravity' }
        },
        steps: [
            { role: 'review', usage: { cost_usd: 0.01 } },
            { role: 'build', usage: { cost_usd: 0.03 } }
        ]
    };
    const hdrHtml = app.renderRunHeader(testRun);
    if (!hdrHtml.includes('GOAL') ||
        !hdrHtml.includes('Add an awesome new tape header block to the Transport console with full prompt text.') ||
        !hdrHtml.includes('run-xyz-12345678') ||
        !hdrHtml.includes('completed') ||
        !hdrHtml.includes('02:00') ||
        !hdrHtml.includes('$0.04') ||
        !hdrHtml.includes('review claude-code · build openrouter/deepseek-chat · verify antigravity')) {
        process.exit(11);
    }

    // 12. formatSessionDate helper
    const nowTs = Date.now() / 1000;
    const todayRes = app.formatSessionDate(nowTs);
    if (!todayRes || !todayRes.text || !todayRes.fullTitle || !todayRes.text.includes(':')) {
        process.exit(12);
    }
    const pastYearRes = app.formatSessionDate(1600000000); // 2020-09-13
    if (!pastYearRes || !pastYearRes.text.includes('2020') || !pastYearRes.text.includes('Sep 13')) {
        process.exit(13);
    }

    // 14. showTransportFeedback and collectMcpServersFromDOM exports
    if (typeof app.showTransportFeedback !== 'function' || typeof app.collectMcpServersFromDOM !== 'function') {
        process.exit(14);
    }
    """
    res = subprocess.run([node_bin, "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script failed with: {res.stderr}"


def test_api_runs_pagination(tmp_path):
    from agentflow.database import save_run
    from agentflow.orchestrator import RunState

    db = tmp_path / "agentflow.db"
    # Seed 30 runs
    for i in range(30):
        state = RunState(
            run_id=f"run-page-{i:02d}",
            goal=f"Goal {i}",
            started_at=float(i),
            config={},
        )
        save_run(state, str(tmp_path), db)

    client = _make_client(tmp_path)

    # 1. limit=10, offset=0 -> 10 runs, total=30
    resp1 = client.get("/api/runs?limit=10&offset=0")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1["runs"]) == 10
    assert data1["total"] == 30
    assert data1["limit"] == 10
    assert data1["offset"] == 0

    # 2. limit=10, offset=25 -> 5 runs, total=30
    resp2 = client.get("/api/runs?limit=10&offset=25")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["runs"]) == 5
    assert data2["total"] == 30
    assert data2["limit"] == 10
    assert data2["offset"] == 25

    # 3. No params -> limit=25, 25 runs, total=30
    resp3 = client.get("/api/runs")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert len(data3["runs"]) == 25
    assert data3["total"] == 30
    assert data3["limit"] == 25
    assert data3["offset"] == 0


def test_spa_catch_all_and_404(tmp_path):
    client = _make_client(tmp_path)

    # Valid SPA client-side routes should return 200 HTML with run-form / nav-logo
    for path in ["/runs", "/runs/abc", "/config", "/health", "/run", "/runs/20260101-000000-aaaaaaaa"]:
        resp = client.get(path)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert 'class="brand"' in resp.text or 'id="transport-bar"' in resp.text

    # Unknown /api routes must return 404 JSON, NOT html
    api_404 = client.get("/api/nonesuch")
    assert api_404.status_code == 404
    assert "text/html" not in api_404.headers.get("content-type", "")
    assert api_404.json()["detail"] == "Not found"

    api_root_404 = client.get("/api")
    assert api_root_404.status_code == 404
    assert "text/html" not in api_root_404.headers.get("content-type", "")


def test_api_run_detail_returns_blockers(tmp_path):
    run = {
        "run_id": "20260101-000000-cccccccc",
        "goal": "test blockers api",
        "started_at": 1.0,
        "config": {},
        "steps": [],
        "tool_calls": [],
        "finished_at": 2.0,
        "pushed": None,
        "blockers": [
            {
                "reason": "budget",
                "detail": "x",
                "fatal": True,
                "step_index": None,
                "ts": 1.0,
            }
        ],
    }
    save_run(RunState(**run), str(tmp_path), tmp_path / "agentflow.db")

    client = _make_client(tmp_path)
    resp = client.get(f"/api/runs/{run['run_id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert "blockers" in data
    assert len(data["blockers"]) == 1
    assert data["blockers"][0]["reason"] == "budget"
    assert data["blockers"][0]["fatal"] is True
    assert data["blockers"][0]["detail"] == "x"


def test_js_router_via_node():
    import shutil
    import subprocess

    node_bin = shutil.which("node")
    if not node_bin:
        return

    script = """
    const md = require('./src/agentflow/web/static/md.js');
    global.renderMarkdown = md.renderMarkdown;
    const app = require('./src/agentflow/web/static/app.js');

    // Test exported functions exist
    if (typeof app.currentRoute !== 'function' || typeof app.navigate !== 'function' || typeof app.renderRoute !== 'function') {
        process.exit(1);
    }

    // currentRoute default in node (no window)
    const route = app.currentRoute();
    if (route.view !== 'run') {
        process.exit(2);
    }

    // runStatus tests: Blocked when finished_at is set, not pushed, fatal blocker present
    const blockedRun = {
        finished_at: 100,
        pushed: null,
        blockers: [{ reason: 'budget', detail: 'exceeded', fatal: true }]
    };
    const status1 = app.runStatus(blockedRun);
    if (status1.label !== 'Blocked' || status1.cls !== 'danger') {
        process.exit(3);
    }

    // Non-fatal blocker should still be Completed if finished_at is set
    const nonFatalRun = {
        finished_at: 100,
        pushed: null,
        blockers: [{ reason: 'permission', detail: 'denied', fatal: false }]
    };
    const status2 = app.runStatus(nonFatalRun);
    if (status2.label !== 'Completed') {
        process.exit(4);
    }

    // Pushed run takes precedence
    const pushedRun = {
        finished_at: 100,
        pushed: { pushed: true },
        blockers: [{ reason: 'budget', detail: 'exceeded', fatal: true }]
    };
    const status3 = app.runStatus(pushedRun);
    if (status3.label !== 'Pushed') {
        process.exit(5);
    }

    // renderStep with no_response: true
    const noRespStep = {
        role: 'verify',
        text: '_The verify backend returned no written response._',
        success: true,
        no_response: true
    };
    const stepHtml = app.renderStep(noRespStep, 0);
    if (!stepHtml.includes('step-noresponse') || !stepHtml.includes('The verify backend returned no written response.')) {
        process.exit(6);
    }

    // renderSessionThread tests: 1 run -> empty string
    const singleRunSession = { runs: [{ run_id: 'r1', goal: 'g1' }] };
    if (app.renderSessionThread(singleRunSession, 'r1') !== '') {
        process.exit(7);
    }

    // renderSessionThread tests: >1 runs -> thread HTML
    const multiRunSession = {
        runs: [
            { run_id: 'r1', goal: 'First turn goal', finished_at: 100, pushed: { pushed: true } },
            { run_id: 'r2', goal: 'Second turn goal', finished_at: null }
        ]
    };
    const threadHtml = app.renderSessionThread(multiRunSession, 'r2');
    if (!threadHtml.includes('Session — 2 turns') || !threadHtml.includes('#1') || !threadHtml.includes('#2')) {
        process.exit(8);
    }
    if (!threadHtml.includes('class="session-turn current"') || !threadHtml.includes('data-run-id="r1"')) {
        process.exit(9);
    }
    if (!threadHtml.includes('First turn goal') || !threadHtml.includes('Second turn goal')) {
        process.exit(10);
    }
    """
    res = subprocess.run([node_bin, "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node script failed with: {res.stderr}"


def test_static_assets_contain_notify_and_blockers(tmp_path):
    client = _make_client(tmp_path)
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert 'id="notify-toggle"' in html_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "toggleNotify" in js_resp.text
    assert "maybeNotify" in js_resp.text
    assert "blocker" in js_resp.text
    assert "step-noresponse" in js_resp.text

    css_resp = client.get("/static/console.css")
    assert css_resp.status_code == 200
    assert ".blockers" in css_resp.text
    assert ".blocker" in css_resp.text
    assert ".step-noresponse" in css_resp.text


def test_api_config_get_returns_masked_openrouter_key_status(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config_path = tmp_path / "agentflow.config.yaml"
    real_key = "fake-openrouter-key-abcdefghijklmnop"
    monkeypatch.setenv("OPENROUTER_API_KEY", real_key)

    client = _make_client(tmp_path, config_path=str(config_path))
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "openrouter_key" in data
    assert data["openrouter_key"]["set"] is True
    assert data["openrouter_key"]["source"] == "env"
    assert data["openrouter_key"]["masked"] == f"{real_key[:8]}…{real_key[-4:]}"
    assert real_key not in resp.text


def test_api_config_post_updates_openrouter_key_and_preserves_on_subsequent_post(tmp_path, monkeypatch):
    from agentflow.config import _from_file
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))
    real_key = "unit-test-key-not-a-secret-01"

    # 1. POST with openrouter_api_key
    resp = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "review_model": "",
            "build_backend": "claude-code",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 3,
            "openrouter_api_key": real_key,
        },
    )
    assert resp.status_code == 200
    post_data = resp.json()
    assert post_data["ok"] is True
    assert post_data["openrouter_key"]["set"] is True
    assert post_data["openrouter_key"]["source"] == "config"
    assert post_data["openrouter_key"]["masked"] != real_key
    assert real_key not in resp.text

    assert config_path.exists()
    assert oct(config_path.stat().st_mode & 0o777) == oct(0o600)
    raw = _from_file(str(config_path))
    assert raw["credentials"]["openrouter_api_key"] == real_key

    # 2. GET /api/config verifies masked status and no raw key leak
    get_resp = client.get("/api/config")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["openrouter_key"]["set"] is True
    assert get_data["openrouter_key"]["source"] == "config"
    assert get_data["openrouter_key"]["masked"] == f"{real_key[:8]}…{real_key[-4:]}"
    assert real_key not in get_resp.text

    # 3. POST /api/config without the field preserves the existing key in config
    post2_resp = client.post(
        "/api/config",
        json={
            "review_backend": "antigravity",
            "review_model": "",
            "build_backend": "claude-code",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 4,
        },
    )
    assert post2_resp.status_code == 200
    raw2 = _from_file(str(config_path))
    assert raw2["credentials"]["openrouter_api_key"] == real_key
    assert real_key not in post2_resp.text


def test_static_assets_contain_openrouter_key_ui(tmp_path):
    client = _make_client(tmp_path)
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert 'id="config-openrouter-status"' in html_resp.text
    assert 'id="config-openrouter_api_key"' in html_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "config-openrouter-status" in js_resp.text
    assert "openrouter_api_key" in js_resp.text

    css_resp = client.get("/static/console.css")
    assert css_resp.status_code == 200
    assert ".key-status" in css_resp.text
    assert ".key-source" in css_resp.text
    assert ".form-hint" in css_resp.text


def test_api_memory_get_and_put_endpoints(tmp_path):
    client = _make_client(tmp_path)

    # 1. Initial GET returns empty strings
    get_resp = client.get("/api/memory")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["global"] == ""
    assert data["project"] == ""
    assert data["cwd"] == str(tmp_path)

    # 2. PUT with both fields
    put_resp = client.put(
        "/api/memory",
        json={"global": "Global instruction G", "project": "Project convention P"},
    )
    assert put_resp.status_code == 200
    put_data = put_resp.json()
    assert put_data["global"] == "Global instruction G"
    assert put_data["project"] == "Project convention P"
    assert put_data["cwd"] == str(tmp_path)

    # Follow-up GET confirms persistence
    get_resp2 = client.get("/api/memory")
    assert get_resp2.status_code == 200
    assert get_resp2.json()["global"] == "Global instruction G"
    assert get_resp2.json()["project"] == "Project convention P"

    # 3. Partial PUT with only project leaves global untouched
    put_proj = client.put("/api/memory", json={"project": "Project convention P2"})
    assert put_proj.status_code == 200
    assert put_proj.json()["global"] == "Global instruction G"
    assert put_proj.json()["project"] == "Project convention P2"

    # 4. Partial PUT with only global leaves project untouched
    put_glob = client.put("/api/memory", json={"global": "Global instruction G2"})
    assert put_glob.status_code == 200
    assert put_glob.json()["global"] == "Global instruction G2"
    assert put_glob.json()["project"] == "Project convention P2"


def test_static_assets_contain_memory_ui(tmp_path):
    client = _make_client(tmp_path)
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert 'id="config-memory-global"' in html_resp.text
    assert 'id="config-memory-project"' in html_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "loadMemory" in js_resp.text
    assert "config-memory-global" in js_resp.text
    assert "config-memory-project" in js_resp.text


def test_api_projects_endpoint(tmp_path):
    # Single project by default
    client = _make_client(tmp_path)
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    resolved_cwd = str(tmp_path.resolve())
    assert data == [{"path": resolved_cwd, "name": Path(resolved_cwd).name}]

    # Multiple projects configured
    dir_a = tmp_path / "proj-a"
    dir_b = tmp_path / "proj-b"
    dir_a.mkdir()
    dir_b.mkdir()
    client_multi = _make_client(tmp_path, projects=[str(dir_a), str(dir_b)])
    resp_multi = client_multi.get("/api/projects")
    assert resp_multi.status_code == 200
    data_multi = resp_multi.json()
    assert len(data_multi) == 2
    assert data_multi[0] == {"path": str(dir_a.resolve()), "name": "proj-a"}
    assert data_multi[1] == {"path": str(dir_b.resolve()), "name": "proj-b"}


def test_api_runs_scoping_with_multiple_projects(tmp_path):
    dir_a = tmp_path / "repo-a"
    dir_b = tmp_path / "repo-b"
    dir_a.mkdir()
    dir_b.mkdir()
    db_file = dir_a / "agentflow.db"

    run_a = {
        "run_id": "run-a-1",
        "goal": "goal for repo A",
        "started_at": time.time(),
        "config": {},
        "steps": [],
        "tool_calls": [],
        "finished_at": time.time(),
    }
    run_b = {
        "run_id": "run-b-1",
        "goal": "goal for repo B",
        "started_at": time.time(),
        "config": {},
        "steps": [],
        "tool_calls": [],
        "finished_at": time.time(),
    }
    save_run(RunState(**run_a), str(dir_a.resolve()), db_file)
    save_run(RunState(**run_b), str(dir_b.resolve()), db_file)

    client = _make_client(dir_a, projects=[str(dir_a), str(dir_b)])

    # Default (no project param) -> project A (first project)
    resp_def = client.get("/api/runs")
    assert resp_def.status_code == 200
    runs_def = resp_def.json()["runs"]
    assert len(runs_def) == 1
    assert runs_def[0]["run_id"] == "run-a-1"

    # Explicit project A
    resp_a = client.get(f"/api/runs?project={dir_a.resolve()}")
    assert resp_a.status_code == 200
    runs_a = resp_a.json()["runs"]
    assert len(runs_a) == 1
    assert runs_a[0]["run_id"] == "run-a-1"

    # Explicit project B
    resp_b = client.get(f"/api/runs?project={dir_b.resolve()}")
    assert resp_b.status_code == 200
    runs_b = resp_b.json()["runs"]
    assert len(runs_b) == 1
    assert runs_b[0]["run_id"] == "run-b-1"

    # Detail view scoped to project
    assert client.get(f"/api/runs/run-b-1?project={dir_b.resolve()}").status_code == 200
    assert client.get(f"/api/runs/run-b-1?project={dir_a.resolve()}").status_code == 404


def test_api_runs_unknown_project_returns_400(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/runs?project=/nope")
    assert resp.status_code == 400
    assert "Unknown project" in resp.json()["detail"]


def test_api_create_run_with_project_scoping(tmp_path):
    dir_a = tmp_path / "repo-a"
    dir_b = tmp_path / "repo-b"
    dir_a.mkdir()
    dir_b.mkdir()

    with patch("agentflow.web.app.load_config", return_value=_config()), patch(
        "agentflow.web.app.run_workflow"
    ) as mock_run:
        client = _make_client(dir_a, projects=[str(dir_a), str(dir_b)])
        resp = client.post("/api/runs", json={"goal": "scoped task", "project": str(dir_b.resolve())})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"

        import threading
        for thread in threading.enumerate():
            if thread.name.startswith("agentflow-"):
                thread.join(timeout=5)

        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["goal"] == "scoped task"
        assert kwargs["cwd"] == str(dir_b.resolve())


def test_api_memory_scoping_with_multiple_projects(tmp_path):
    dir_a = tmp_path / "repo-a"
    dir_b = tmp_path / "repo-b"
    dir_a.mkdir()
    dir_b.mkdir()

    client = _make_client(dir_a, projects=[str(dir_a), str(dir_b)])

    # Write memory to project B
    put_resp = client.put(
        f"/api/memory?project={dir_b.resolve()}",
        json={"project": "Project B conventions", "global": "Global instruction"},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["cwd"] == str(dir_b.resolve())
    assert put_resp.json()["project"] == "Project B conventions"

    # Read project A memory (should be empty for project)
    get_a = client.get(f"/api/memory?project={dir_a.resolve()}")
    assert get_a.status_code == 200
    assert get_a.json()["cwd"] == str(dir_a.resolve())
    assert get_a.json()["project"] == ""
    assert get_a.json()["global"] == "Global instruction"

    # Read project B memory
    get_b = client.get(f"/api/memory?project={dir_b.resolve()}")
    assert get_b.status_code == 200
    assert get_b.json()["cwd"] == str(dir_b.resolve())
    assert get_b.json()["project"] == "Project B conventions"
    assert get_b.json()["global"] == "Global instruction"


def test_project_resolution_by_basename(tmp_path):
    dir_a = tmp_path / "alpha-repo"
    dir_b = tmp_path / "beta-repo"
    dir_a.mkdir()
    dir_b.mkdir()

    client = _make_client(dir_a, projects=[str(dir_a), str(dir_b)])

    # Resolves by unambiguous basename
    resp_b = client.get("/api/runs?project=beta-repo")
    assert resp_b.status_code == 200

    # Ambiguous basename when two projects share name
    dir_sub = tmp_path / "sub" / "alpha-repo"
    dir_sub.mkdir(parents=True)
    client_ambig = _make_client(dir_a, projects=[str(dir_a), str(dir_sub)])
    resp_ambig = client_ambig.get("/api/runs?project=alpha-repo")
    assert resp_ambig.status_code == 400
    assert "Unknown project: alpha-repo" in resp_ambig.json()["detail"]


def test_static_assets_contain_project_selector_ui(tmp_path):
    client = _make_client(tmp_path)
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert 'id="project-select"' in html_resp.text
    assert 'class="nav-project"' in html_resp.text

    css_resp = client.get("/static/console.css")
    assert css_resp.status_code == 200
    assert ".nav-project" in css_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "loadProjects" in js_resp.text
    assert "projectQuery" in js_resp.text
    assert "project-select" in js_resp.text


def test_api_config_notifications_get_and_post(tmp_path, monkeypatch):
    from agentflow.config import _from_file
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))
    real_pw = "pw-unit-test-value-000000"

    # 1. Initial GET before any notifications config
    init_get = client.get("/api/config")
    assert init_get.status_code == 200
    assert init_get.json()["notifications"] is None
    assert init_get.json()["smtp_password"]["set"] is False

    # 2. POST with notifications dict and smtp_password
    post_resp = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "review_model": "",
            "build_backend": "claude-code",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 3,
            "notifications": {
                "enabled": True,
                "email_to": "alerts@example.com",
                "email_from": "bot@example.com",
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_username": "bot@example.com",
                "smtp_use_tls": True,
                "notify_on": ["finished", "blocked"],
                "base_url": "https://agentui.app.rem029.com",
            },
            "smtp_password": real_pw,
        },
    )
    assert post_resp.status_code == 200
    post_data = post_resp.json()
    assert post_data["ok"] is True
    assert post_data["notifications"]["enabled"] is True
    assert post_data["notifications"]["email_to"] == "alerts@example.com"
    assert post_data["smtp_password"]["set"] is True
    assert post_data["smtp_password"]["source"] == "config"
    assert post_data["smtp_password"]["masked"] != real_pw
    assert real_pw not in post_resp.text

    assert config_path.exists()
    assert oct(config_path.stat().st_mode & 0o777) == oct(0o600)
    raw = _from_file(str(config_path))
    assert raw["credentials"]["smtp_password"] == real_pw

    # 3. GET /api/config verifies masked password and no raw secret leak
    get_resp = client.get("/api/config")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["notifications"]["enabled"] is True
    assert get_data["smtp_password"]["set"] is True
    assert get_data["smtp_password"]["source"] == "config"
    assert get_data["smtp_password"]["masked"] == f"{real_pw[:8]}…{real_pw[-4:]}"
    assert real_pw not in get_resp.text

    # 4. POST /api/config without smtp_password preserves existing password in config
    post2_resp = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "review_model": "",
            "build_backend": "claude-code",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 3,
            "notifications": {
                "enabled": False,
            },
        },
    )
    assert post2_resp.status_code == 200
    raw2 = _from_file(str(config_path))
    assert raw2["credentials"]["smtp_password"] == real_pw
    assert real_pw not in post2_resp.text


def test_api_notifications_test_endpoint(tmp_path):
    client = _make_client(tmp_path)

    # 1. Disabled / unconfigured notifications
    resp = client.post("/api/notifications/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["result"].startswith("skipped:")

    # 2. Mocked send returns sent
    with patch("agentflow.notify.send_test_email", return_value="sent"):
        resp2 = client.post("/api/notifications/test")
        assert resp2.status_code == 200
        assert resp2.json()["result"] == "sent"


def test_static_assets_contain_notifications_ui(tmp_path):
    client = _make_client(tmp_path)
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert 'id="config-notify-enabled"' in html_resp.text
    assert 'id="config-notify-email_to"' in html_resp.text
    assert 'id="config-smtp-status"' in html_resp.text
    assert 'id="config-smtp_password"' in html_resp.text
    assert 'id="send-test-email"' in html_resp.text
    assert 'id="test-email-status"' in html_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "send-test-email" in js_resp.text
    assert "/api/notifications/test" in js_resp.text
    assert "config-smtp-status" in js_resp.text


def test_api_create_run_returns_session_id_and_follows_up(tmp_path):
    import threading

    with patch("agentflow.web.app.load_config", return_value=_config()), patch(
        "agentflow.web.app.run_workflow"
    ) as mock_run:
        client = _make_client(tmp_path)
        # 1. First run creates session
        resp1 = client.post("/api/runs", json={"goal": "turn 1 goal"})
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == "started"
        assert "run_id" in data1
        assert "session_id" in data1
        session_id = data1["session_id"]
        assert session_id.startswith("session-")

        for thread in threading.enumerate():
            if thread.name.startswith("agentflow-"):
                thread.join(timeout=5)

        assert mock_run.call_count == 1
        _, kwargs1 = mock_run.call_args
        assert kwargs1["session_id"] == session_id
        assert kwargs1["goal"] == "turn 1 goal"

        # 2. Follow-up run in the same session passes that session_id to run_workflow
        resp2 = client.post("/api/runs", json={"goal": "turn 2 goal", "session_id": session_id})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["status"] == "started"
        assert data2["session_id"] == session_id

        for thread in threading.enumerate():
            if thread.name.startswith("agentflow-"):
                thread.join(timeout=5)

        assert mock_run.call_count == 2
        _, kwargs2 = mock_run.call_args
        assert kwargs2["session_id"] == session_id
        assert kwargs2["goal"] == "turn 2 goal"


def test_get_session_detail_returns_runs_in_order(tmp_path):
    sid = "session-test-order-123"
    run1 = RunState(
        run_id="run-turn-1",
        session_id=sid,
        goal="First turn goal",
        started_at=1000.0,
        config={},
        finished_at=1050.0,
    )
    run2 = RunState(
        run_id="run-turn-2",
        session_id=sid,
        goal="Second turn goal",
        started_at=1100.0,
        config={},
        finished_at=1150.0,
    )
    save_run(run1, str(tmp_path), tmp_path / "agentflow.db")
    save_run(run2, str(tmp_path), tmp_path / "agentflow.db")

    client = _make_client(tmp_path)
    resp = client.get(f"/api/sessions/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == sid
    assert len(data["runs"]) == 2
    assert data["runs"][0]["run_id"] == "run-turn-1"
    assert data["runs"][0]["goal"] == "First turn goal"
    assert data["runs"][1]["run_id"] == "run-turn-2"
    assert data["runs"][1]["goal"] == "Second turn goal"
    assert data["runs"][1]["session_id"] == sid


def test_static_assets_contain_session_composer_and_thread(tmp_path):
    client = _make_client(tmp_path)
    html_resp = client.get("/")
    assert html_resp.status_code == 200
    assert 'id="session-composer"' in html_resp.text
    assert 'id="composer-send"' in html_resp.text
    assert 'id="composer-input"' in html_resp.text
    assert 'id="composer-status"' in html_resp.text

    js_resp = client.get("/static/app.js")
    assert js_resp.status_code == 200
    assert "setupComposer" in js_resp.text
    assert "session-thread" in js_resp.text
    assert "renderSessionThread" in js_resp.text

    css_resp = client.get("/static/console.css")
    assert css_resp.status_code == 200
    assert ".session-thread" in css_resp.text
    assert ".composer" in css_resp.text


def test_api_run_stream_finished_run(tmp_path):
    from agentflow.database import append_event, save_run
    from agentflow.orchestrator import RunState

    db = tmp_path / "agentflow.db"
    state = RunState(
        run_id="run-stream-1",
        goal="Test streaming finished",
        started_at=1.0,
        finished_at=2.0,
        config={},
    )
    save_run(state, str(tmp_path), db)
    append_event("run-stream-1", 1, "run_started", {"goal": "Test streaming finished"}, path=db)
    append_event("run-stream-1", 2, "step_started", {"role": "build"}, path=db)
    append_event("run-stream-1", 3, "run_finished", {"finished_at": 2.0}, path=db)

    client = _make_client(tmp_path)
    resp = client.get("/api/runs/run-stream-1/stream")
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert resp.headers["cache-control"] == "no-cache"

    text = resp.text
    assert "id: 1\nevent: run_started\ndata: {\"goal\": \"Test streaming finished\"}\n\n" in text
    assert "id: 2\nevent: step_started\ndata: {\"role\": \"build\"}\n\n" in text
    assert "id: 3\nevent: run_finished\ndata: {\"finished_at\": 2.0}\n\n" in text
    assert "event: done\ndata: {}\n\n" in text


def test_api_run_stream_reconnection_last_event_id(tmp_path):
    from agentflow.database import append_event, save_run
    from agentflow.orchestrator import RunState

    db = tmp_path / "agentflow.db"
    state = RunState(
        run_id="run-stream-2",
        goal="Test Last-Event-ID",
        started_at=1.0,
        finished_at=2.0,
        config={},
    )
    save_run(state, str(tmp_path), db)
    append_event("run-stream-2", 1, "run_started", {"goal": "Test Last-Event-ID"}, path=db)
    append_event("run-stream-2", 2, "step_started", {"role": "review"}, path=db)
    append_event("run-stream-2", 3, "run_finished", {"finished_at": 2.0}, path=db)

    client = _make_client(tmp_path)
    resp = client.get("/api/runs/run-stream-2/stream", headers={"Last-Event-ID": "2"})
    assert resp.status_code == 200
    text = resp.text

    assert "id: 1" not in text
    assert "id: 2" not in text
    assert "id: 3\nevent: run_finished\ndata: {\"finished_at\": 2.0}\n\n" in text
    assert "event: done\ndata: {}\n\n" in text


def test_api_run_stream_unknown_run_returns_404(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/runs/nonexistent-run/stream")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Run not found"


def test_api_run_stream_invalid_last_event_id(tmp_path):
    from agentflow.database import append_event, save_run
    from agentflow.orchestrator import RunState

    db = tmp_path / "agentflow.db"
    state = RunState(
        run_id="run-stream-3",
        goal="Test invalid header",
        started_at=1.0,
        finished_at=2.0,
        config={},
    )
    save_run(state, str(tmp_path), db)
    append_event("run-stream-3", 1, "run_started", {"goal": "Test invalid header"}, path=db)

    client = _make_client(tmp_path)
    resp = client.get("/api/runs/run-stream-3/stream", headers={"Last-Event-ID": "invalid-seq"})
    assert resp.status_code == 200
    text = resp.text
    assert "id: 1\nevent: run_started" in text
    assert "event: done\ndata: {}\n\n" in text


def test_api_run_stream_project_scoping(tmp_path):
    from agentflow.database import append_event, save_run
    from agentflow.orchestrator import RunState

    dir_a = tmp_path / "repo-a"
    dir_b = tmp_path / "repo-b"
    dir_a.mkdir()
    dir_b.mkdir()
    db_file = dir_a / "agentflow.db"

    state = RunState(
        run_id="run-stream-proj",
        goal="Scoped stream",
        started_at=1.0,
        finished_at=2.0,
        config={},
    )
    save_run(state, str(dir_b.resolve()), db_file)
    append_event("run-stream-proj", 1, "run_started", {"goal": "Scoped stream"}, path=db_file)

    client = _make_client(dir_a, projects=[str(dir_a), str(dir_b)])

    # With project=dir_b -> 200
    resp_b = client.get(f"/api/runs/run-stream-proj/stream?project={dir_b.resolve()}")
    assert resp_b.status_code == 200
    assert "id: 1\nevent: run_started" in resp_b.text

    # With project=dir_a -> 404
    resp_a = client.get(f"/api/runs/run-stream-proj/stream?project={dir_a.resolve()}")
    assert resp_a.status_code == 404


def test_api_run_stream_keepalive(tmp_path):
    from agentflow.database import save_run
    from agentflow.orchestrator import RunState

    db = tmp_path / "agentflow.db"
    state = RunState(
        run_id="run-stream-live",
        goal="Live stream",
        started_at=100.0,
        finished_at=None,
        config={},
    )
    save_run(state, str(tmp_path), db)

    client = _make_client(tmp_path)

    call_count = 0
    in_mock = False
    def mock_time():
        nonlocal call_count, in_mock
        if in_mock:
            return 130.0
        call_count += 1
        if call_count == 1:
            return 100.0
        elif call_count == 2:
            return 120.0
        else:
            in_mock = True
            try:
                state.finished_at = 130.0
                save_run(state, str(tmp_path), db)
            finally:
                in_mock = False
            return 130.0

    with patch("agentflow.web.app.time.time", side_effect=mock_time), patch("agentflow.web.app.get_active_run", return_value="run-stream-live"):
        resp = client.get("/api/runs/run-stream-live/stream")
        assert resp.status_code == 200
        assert ": keepalive\n\n" in resp.text
        assert "event: done\ndata: {}\n\n" in resp.text


def test_api_config_mcp_servers_and_max_cost_null_reset(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))

    # 1. Initial POST setting mcp_servers and max_cost_usd
    resp = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "review_model": "",
            "build_backend": "antigravity",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 3,
            "max_cost_usd": 5.0,
            "mcp_servers": [
                {
                    "name": "fake",
                    "command": sys.executable,
                    "args": [FAKE_SERVER_PATH],
                    "enabled": True,
                    "auto_approve": ["echo"],
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["max_cost_usd"] == 5.0
    assert len(data["config"]["mcp_servers"]) == 1
    assert data["config"]["mcp_servers"][0]["name"] == "fake"

    # Verify GET /api/config returns mcp_servers and max_cost_usd
    get_resp = client.get("/api/config")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["max_cost_usd"] == 5.0
    assert len(get_data["mcp_servers"]) == 1
    assert get_data["mcp_servers"][0]["name"] == "fake"
    assert get_data["mcp_servers"][0]["auto_approve"] == ["echo"]

    # 2. Second POST omitting mcp_servers should preserve existing mcp_servers
    resp2 = client.post(
        "/api/config",
        json={
            "review_backend": "openrouter",
            "review_model": "test-model",
            "build_backend": "antigravity",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 4,
        },
    )
    assert resp2.status_code == 200
    cfg2 = load_config(str(config_path))
    assert cfg2.review.backend == "openrouter"
    assert len(cfg2.mcp_servers) == 1
    assert cfg2.mcp_servers[0].name == "fake"
    assert cfg2.max_cost_usd == 5.0

    # 3. Third POST with max_cost_usd: None (null) clears it
    resp3 = client.post(
        "/api/config",
        json={
            "review_backend": "openrouter",
            "review_model": "test-model",
            "build_backend": "antigravity",
            "build_model": "",
            "verify_backend": "claude-code",
            "verify_model": "",
            "max_iterations": 4,
            "max_cost_usd": None,
        },
    )
    assert resp3.status_code == 200
    cfg3 = load_config(str(config_path))
    assert cfg3.max_cost_usd is None
    assert len(cfg3.mcp_servers) == 1

    # 4. Invalid MCP server config returns 400
    resp4 = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "build_backend": "antigravity",
            "verify_backend": "claude-code",
            "mcp_servers": [
                {
                    "name": "bad name with spaces!",
                    "command": "python",
                }
            ],
        },
    )
    assert resp4.status_code == 400
    assert "Invalid MCP servers config" in resp4.json()["detail"]

    # 5. Negative max_cost_usd rejected with 422
    resp5 = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "build_backend": "antigravity",
            "verify_backend": "claude-code",
            "max_cost_usd": -1.0,
        },
    )
    assert resp5.status_code == 422


def test_api_config_mcp_servers_auto_approve_roundtrip(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))

    # 1. POST with auto_approve = ["all"]
    resp1 = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "build_backend": "antigravity",
            "verify_backend": "claude-code",
            "mcp_servers": [
                {
                    "name": "server-all",
                    "command": sys.executable,
                    "args": [FAKE_SERVER_PATH],
                    "enabled": True,
                    "auto_approve": ["all"],
                }
            ],
        },
    )
    assert resp1.status_code == 200
    cfg1 = load_config(str(config_path))
    assert len(cfg1.mcp_servers) == 1
    assert cfg1.mcp_servers[0].auto_approve == ["all"]

    get_resp1 = client.get("/api/config")
    assert get_resp1.status_code == 200
    assert get_resp1.json()["mcp_servers"][0]["auto_approve"] == ["all"]

    # 2. POST with auto_approve = ["toolA", "toolB"]
    resp2 = client.post(
        "/api/config",
        json={
            "review_backend": "claude-code",
            "build_backend": "antigravity",
            "verify_backend": "claude-code",
            "mcp_servers": [
                {
                    "name": "server-tools",
                    "command": sys.executable,
                    "args": [FAKE_SERVER_PATH],
                    "enabled": True,
                    "auto_approve": ["toolA", "toolB"],
                }
            ],
        },
    )
    assert resp2.status_code == 200
    cfg2 = load_config(str(config_path))
    assert len(cfg2.mcp_servers) == 1
    assert cfg2.mcp_servers[0].auto_approve == ["toolA", "toolB"]

    get_resp2 = client.get("/api/config")
    assert get_resp2.status_code == 200
    assert get_resp2.json()["mcp_servers"][0]["auto_approve"] == ["toolA", "toolB"]


def test_api_tools_endpoint(tmp_path):
    client = _make_client(tmp_path)
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    tools = data["tools"]
    assert len(tools) > 0

    # Sorted by name
    names = [t["name"] for t in tools]
    assert names == sorted(names)

    # Check known read-only tool (ReadFile)
    read_file = next((t for t in tools if t["name"] == "ReadFile"), None)
    assert read_file is not None
    assert read_file["read_only"] is True
    assert isinstance(read_file["description"], str) and len(read_file["description"]) > 0
    assert isinstance(read_file["schema"], dict)
    assert read_file["schema"]["name"] == "ReadFile"

    # Check known mutating tool (WriteFile)
    write_file = next((t for t in tools if t["name"] == "WriteFile"), None)
    assert write_file is not None
    assert write_file["read_only"] is False
    assert isinstance(write_file["description"], str) and len(write_file["description"]) > 0
    assert isinstance(write_file["schema"], dict)
    assert write_file["schema"]["name"] == "WriteFile"


def test_api_mcp_endpoint(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"
    client = _make_client(tmp_path, config_path=str(config_path))

    # 1. No servers configured -> empty list
    resp = client.get("/api/mcp")
    assert resp.status_code == 200
    assert resp.json() == {"servers": []}

    # 2. Configured servers: connected stdio, broken command, and disabled server
    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        mcp_servers=[
            MCPServerConfig(
                name="fake",
                command=sys.executable,
                args=[FAKE_SERVER_PATH],
                enabled=True,
                auto_approve=["echo"],
            ),
            MCPServerConfig(
                name="broken",
                command="nonexistent_binary_xyz_12345",
                enabled=True,
            ),
            MCPServerConfig(
                name="disabled-one",
                command=sys.executable,
                args=[FAKE_SERVER_PATH],
                enabled=False,
            ),
        ],
    )
    dump_config(cfg, str(config_path))

    resp2 = client.get("/api/mcp")
    assert resp2.status_code == 200
    servers = resp2.json()["servers"]
    assert len(servers) == 3

    # Fake server (connected)
    s_fake = next(s for s in servers if s["name"] == "fake")
    assert s_fake["enabled"] is True
    assert s_fake["connected"] is True
    assert s_fake["error"] is None
    assert s_fake["auto_approve"] == ["echo"]
    tool_names = [t["name"] for t in s_fake["tools"]]
    assert "mcp__fake__echo" in tool_names
    assert "mcp__fake__add" in tool_names

    # Broken server (not connected, error set)
    s_broken = next(s for s in servers if s["name"] == "broken")
    assert s_broken["enabled"] is True
    assert s_broken["connected"] is False
    assert s_broken["tools"] == []
    assert s_broken["error"] is not None
    assert len(s_broken["error"]) > 0

    # Disabled server (not connected, no error, tools empty)
    s_disabled = next(s for s in servers if s["name"] == "disabled-one")
    assert s_disabled["enabled"] is False
    assert s_disabled["connected"] is False
    assert s_disabled["tools"] == []
    assert s_disabled["error"] is None


def test_api_session_detail_cost_and_run_count(tmp_path):
    from agentflow.database import create_session, save_run
    from agentflow.orchestrator import RunState

    db = tmp_path / "agentflow.db"
    create_session("sess-cost-1", str(tmp_path), title="Session With Cost", path=db)

    # Add run 1 with usage cost 0.05
    run1 = RunState(
        run_id="run-c1",
        session_id="sess-cost-1",
        goal="Run 1",
        started_at=1.0,
        finished_at=2.0,
        config={},
        steps=[
            {"role": "build", "usage": {"cost_usd": 0.05}},
        ],
    )
    save_run(run1, str(tmp_path), db)

    # Add run 2 with usage cost 0.12
    run2 = RunState(
        run_id="run-c2",
        session_id="sess-cost-1",
        goal="Run 2",
        started_at=3.0,
        finished_at=4.0,
        config={},
        steps=[
            {"role": "verify", "usage": {"cost_usd": 0.12}},
        ],
    )
    save_run(run2, str(tmp_path), db)

    client = _make_client(tmp_path)
    resp = client.get("/api/sessions/sess-cost-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-cost-1"
    assert data["run_count"] == 2
    assert data["total_cost"] == pytest.approx(0.17)

    # Empty session has 0 runs and 0.0 cost
    create_session("sess-cost-empty", str(tmp_path), title="Empty Session", path=db)
    resp_empty = client.get("/api/sessions/sess-cost-empty")
    assert resp_empty.status_code == 200
    data_empty = resp_empty.json()
    assert data_empty["run_count"] == 0
    assert data_empty["total_cost"] == 0.0


def test_api_files_endpoint(tmp_path):
    # Setup test file tree
    (tmp_path / "file1.txt").write_text("hello")
    (tmp_path / "README.md").write_text("# Readme")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "app.py").write_text("print(1)")

    client = _make_client(tmp_path)
    resp = client.get("/api/files")
    assert resp.status_code == 200
    files = resp.json()["files"]
    assert "file1.txt" in files
    assert "README.md" in files
    assert "src/app.py" in files


def test_api_run_stream_interrupted_orphan_run(tmp_path):
    """An orphaned run with finished_at None that is not the active run should emit interrupted and done."""
    from agentflow.orchestrator import RunState
    from agentflow.database import save_run, append_event

    db = tmp_path / "agentflow.db"
    run = RunState(
        run_id="run-orphan-1",
        session_id="sess-orphan-1",
        goal="Orphaned zombie task",
        config={},
        started_at=1000.0,
        finished_at=None,  # unfinished
        steps=[],
    )
    save_run(run, str(tmp_path), db)
    append_event("run-orphan-1", 1, "step_started", {"role": "review"}, path=db)

    client = _make_client(tmp_path)

    # 1. run_detail returns interrupted=True
    detail = client.get("/api/runs/run-orphan-1").json()
    assert detail["interrupted"] is True
    assert detail["is_active"] is False

    # 2. SSE stream emits events, interrupted event, and done
    with client.stream("GET", "/api/runs/run-orphan-1/stream") as response:
        assert response.status_code == 200
        content = "".join(list(response.iter_text()))
        assert "event: step_started" in content
        assert "event: interrupted" in content
        assert "event: done" in content









