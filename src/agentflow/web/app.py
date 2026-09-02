"""FastAPI application for the agentflow web UI."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from ..backends import BACKENDS
from ..config import (
    Config,
    CredentialsConfig,
    DEFAULT_CONFIG_PATH,
    MCPServerConfig,
    NotificationConfig,
    PermissionMode,
    RoleConfig,
    dump_config,
    load_config,
)
from ..credentials import openrouter_api_key_info, smtp_password_info
from ..memory import (
    read_global_memory,
    read_project_memory,
    write_global_memory,
    write_project_memory,
)
from ..database import (
    DEFAULT_DATABASE_PATH,
    add_control_signal,
    add_pending_message,
    add_queued_run,
    count_runs,
    get_pending_messages,
    get_session,
    get_session_runs,
    get_tool_calls,
    list_events,
    list_runs,
    list_sessions,
    load_run,
)
from ..models import get_all_models
from ..orchestrator import (
    READ_ONLY_TOOLS,
    RunInProgressError,
    get_active_run,
    new_run_id,
    new_session_id,
    run_workflow,
)
from ..tools import get_tool, get_tool_schema, list_tools
from ..tui.completion import _list_project_files
from ..tui.render import session_cost

STATIC_DIR = Path(__file__).parent / "static"


# ---------- Pydantic models ----------
class ConfigUpdate(BaseModel):
    review_backend: str = Field(..., description="Backend for review role")
    review_model: Optional[str] = None
    build_backend: str = Field(..., description="Backend for build role")
    build_model: Optional[str] = None
    verify_backend: str = Field(..., description="Backend for verify role")
    verify_model: Optional[str] = None
    max_iterations: int = Field(3, ge=1, le=10)
    max_requirements_rounds: int = Field(3, ge=0, le=10)
    build_review: bool = True
    permissions: Optional[PermissionMode] = None
    max_cost_usd: Optional[float] = Field(None, ge=0)
    openrouter_api_key: Optional[str] = None
    notifications: Optional[dict] = None
    smtp_password: Optional[str] = None
    mcp_servers: Optional[list[dict[str, Any]]] = None


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    global_: Optional[str] = Field(None, alias="global")
    project: Optional[str] = None


class RunCreate(BaseModel):
    goal: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    project: Optional[str] = None
    review_backend: Optional[str] = None
    review_model: Optional[str] = None
    build_backend: Optional[str] = None
    build_model: Optional[str] = None
    verify_backend: Optional[str] = None
    verify_model: Optional[str] = None
    max_iterations: Optional[int] = Field(None, ge=1, le=10)
    max_requirements_rounds: Optional[int] = Field(None, ge=0, le=10)
    build_review: Optional[bool] = None


class MessageCreate(BaseModel):
    body: str = Field(..., min_length=1)
    kind: str = Field("steer", pattern="^(steer|note|answer)$")



# ---------- Helper functions ----------
def _build_config_from_overrides(
    base_config: Config,
    overrides: Optional[RunCreate] = None,
) -> Config:
    """Create a Config from base and optional per‑role overrides."""
    if overrides is None:
        return base_config

    def update_role(role: str, backend_override: Optional[str], model_override: Optional[str]) -> RoleConfig:
        current = getattr(base_config, role)
        return RoleConfig(
            backend=backend_override if backend_override else current.backend,
            model=model_override if model_override is not None else current.model,
        )

    new_config = Config(
        review=update_role("review", overrides.review_backend, overrides.review_model),
        build=update_role("build", overrides.build_backend, overrides.build_model),
        verify=update_role("verify", overrides.verify_backend, overrides.verify_model),
        max_iterations=overrides.max_iterations if overrides.max_iterations else base_config.max_iterations,
        max_requirements_rounds=(
            overrides.max_requirements_rounds
            if overrides.max_requirements_rounds is not None
            else base_config.max_requirements_rounds
        ),
        build_review=(
            overrides.build_review if overrides.build_review is not None else base_config.build_review
        ),
        permissions=base_config.permissions,
        max_cost_usd=base_config.max_cost_usd,
        notifications=base_config.notifications,
        credentials=base_config.credentials,
        mcp_servers=base_config.mcp_servers,
    )
    return new_config


def _health_check_all(config_path: str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Run health checks for all configured backend types (unique by name+model)."""
    results = {}
    try:
        config = load_config(config_path)
    except Exception:
        config = Config(review=RoleConfig(backend="claude-code"),
                        build=RoleConfig(backend="antigravity"),
                        verify=RoleConfig(backend="claude-code"))

    seen = set()
    for role_name, role_config in config.roles().items():
        key = f"{role_config.backend}:{role_config.model or ''}"
        if key in seen:
            continue
        seen.add(key)
        backend_class = BACKENDS[role_config.backend]
        backend = backend_class(model=role_config.model)
        try:
            result = backend.health_check()
            results[key] = {"backend": result.backend, "ok": result.ok, "detail": result.detail}
        except Exception as exc:
            results[key] = {"backend": key, "ok": False, "detail": str(exc)}
    return results


# ---------- FastAPI app ----------
def create_app(
    cwd: str,
    config_path: str = DEFAULT_CONFIG_PATH,
    database_path: Path | None = None,
    projects: list[str] | None = None,
) -> FastAPI:
    from ..dotenv import load_env

    load_env(cwd)

    app = FastAPI(title="agentflow Web UI", version="0.1.0")
    db_path = database_path or DEFAULT_DATABASE_PATH

    _projects: list[str] = []
    for p in (projects or [cwd]):
        rp = str(Path(p).resolve())
        if rp not in _projects:
            _projects.append(rp)

    def _resolve_project(q: str | None) -> str:
        if not q:
            return _projects[0]
        rp = str(Path(q).resolve())
        if rp in _projects:
            return rp
        # also allow matching by basename when unambiguous
        by_name = [p for p in _projects if Path(p).name == q]
        if len(by_name) == 1:
            return by_name[0]
        raise HTTPException(status_code=400, detail=f"Unknown project: {q}")

    # Mount static files (CSS, JS, etc.)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ---------- HTML page ----------
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    # ---------- API endpoints ----------
    @app.get("/api/projects")
    async def get_projects() -> list[dict]:
        return [{"path": p, "name": Path(p).name} for p in _projects]

    @app.get("/api/config")
    async def get_config() -> dict:
        try:
            config = load_config(config_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        resp = config.model_dump()
        resp.pop("credentials", None)
        resp["openrouter_key"] = openrouter_api_key_info(config_path)
        resp["notifications"] = config.notifications.model_dump() if config.notifications else None
        resp["smtp_password"] = smtp_password_info(config_path)
        return resp

    @app.post("/api/config")
    async def update_config(data: ConfigUpdate) -> dict:
        # Validate backend names
        for role in ["review", "build", "verify"]:
            backend_name = getattr(data, f"{role}_backend")
            if backend_name not in BACKENDS:
                raise HTTPException(status_code=400, detail=f"Invalid backend for {role}: {backend_name}")

        try:
            current_config = load_config(config_path)
        except Exception:
            current_config = None

        notif_cfg = None
        if data.notifications is not None:
            try:
                notif_cfg = NotificationConfig(**data.notifications)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid notifications config: {exc}")
        elif current_config is not None:
            notif_cfg = current_config.notifications

        existing_creds = current_config.credentials if current_config else None
        existing_or_key = existing_creds.openrouter_api_key if existing_creds else None
        existing_smtp_pw = existing_creds.smtp_password if existing_creds else None

        new_or_key = data.openrouter_api_key.strip() if (data.openrouter_api_key and data.openrouter_api_key.strip()) else existing_or_key
        new_smtp_pw = data.smtp_password.strip() if (data.smtp_password and data.smtp_password.strip()) else existing_smtp_pw

        creds_cfg = None
        if new_or_key or new_smtp_pw:
            creds_cfg = CredentialsConfig(
                openrouter_api_key=new_or_key,
                smtp_password=new_smtp_pw,
            )

        mcp_servers_cfg: list[MCPServerConfig] = []
        if data.mcp_servers is not None:
            try:
                mcp_servers_cfg = [MCPServerConfig(**s) for s in data.mcp_servers]
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid MCP servers config: {exc}")
        elif current_config is not None:
            mcp_servers_cfg = list(current_config.mcp_servers)

        max_cost = (
            data.max_cost_usd
            if "max_cost_usd" in data.model_fields_set
            else (current_config.max_cost_usd if current_config else None)
        )

        config = Config(
            review=RoleConfig(backend=data.review_backend, model=data.review_model),
            build=RoleConfig(backend=data.build_backend, model=data.build_model),
            verify=RoleConfig(backend=data.verify_backend, model=data.verify_model),
            max_iterations=data.max_iterations,
            max_requirements_rounds=data.max_requirements_rounds,
            build_review=data.build_review,
            permissions=data.permissions if data.permissions is not None else (current_config.permissions if current_config else "auto"),
            max_cost_usd=max_cost,
            notifications=notif_cfg,
            credentials=creds_cfg,
            mcp_servers=mcp_servers_cfg,
        )
        try:
            dump_config(config, config_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        resp_cfg = config.model_dump()
        resp_cfg.pop("credentials", None)
        return {
            "ok": True,
            "config": resp_cfg,
            "openrouter_key": openrouter_api_key_info(config_path),
            "notifications": config.notifications.model_dump() if config.notifications else None,
            "smtp_password": smtp_password_info(config_path),
        }

    @app.post("/api/notifications/test")
    async def test_notifications() -> dict:
        from .. import notify
        try:
            cfg = load_config(config_path)
        except Exception as exc:
            return {"result": f"error:could not load config: {exc}"}
        return {"result": notify.send_test_email(cfg)}

    @app.get("/api/memory")
    async def get_memory(project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        return {
            "global": read_global_memory(),
            "project": read_project_memory(target_cwd),
            "cwd": target_cwd,
        }

    @app.put("/api/memory")
    async def update_memory(data: MemoryUpdate, project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        if data.global_ is not None:
            write_global_memory(data.global_)
        if data.project is not None:
            write_project_memory(target_cwd, data.project)
        return {
            "global": read_global_memory(),
            "project": read_project_memory(target_cwd),
            "cwd": target_cwd,
        }

    @app.get("/api/health")
    async def health() -> dict:
        return _health_check_all(config_path)

    @app.get("/api/models")
    async def models() -> dict:
        return get_all_models()

    @app.get("/api/tools")
    async def tools() -> dict:
        tool_names = sorted(list_tools())
        result = []
        for name in tool_names:
            tool = get_tool(name)
            result.append({
                "name": tool.name,
                "description": tool.description,
                "schema": get_tool_schema(name),
                "read_only": name in READ_ONLY_TOOLS,
            })
        return {"tools": result}

    @app.get("/api/mcp")
    async def get_mcp(project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        try:
            cfg = load_config(config_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

        servers = cfg.mcp_servers or []
        if not servers:
            return {"servers": []}

        def _check_servers() -> list[dict[str, Any]]:
            enabled = [s for s in servers if s.enabled]
            tools_by_server: dict[str, list[dict[str, str]]] = {}
            errors: dict[str, str] = {}
            if enabled:
                from ..mcp import MCPManager

                manager = MCPManager(enabled, cwd=target_cwd)
                try:
                    manager.start()
                    for t in manager.list_tools():
                        tools_by_server.setdefault(t.server_name, []).append({
                            "name": t.name,
                            "description": t.description,
                        })
                    errors = manager.errors
                finally:
                    manager.close()

            results: list[dict[str, Any]] = []
            for s in servers:
                if not s.enabled:
                    results.append({
                        "name": s.name,
                        "enabled": False,
                        "connected": False,
                        "tools": [],
                        "error": None,
                        "auto_approve": s.auto_approve,
                    })
                else:
                    err = errors.get(s.name)
                    if err is None and "_manager" in errors:
                        err = errors["_manager"]
                    connected = err is None
                    srv_tools = tools_by_server.get(s.name, []) if connected else []
                    results.append({
                        "name": s.name,
                        "enabled": True,
                        "connected": connected,
                        "tools": srv_tools,
                        "error": err,
                        "auto_approve": s.auto_approve,
                    })
            return results

        server_list = await asyncio.to_thread(_check_servers)
        return {"servers": server_list}

    @app.get("/api/files")
    async def files(project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        file_list = _list_project_files(target_cwd)
        return {"files": file_list}

    @app.get("/api/runs")
    async def runs(limit: int = 25, offset: int = 0, project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        limit = max(1, min(100, limit))
        offset = max(0, offset)
        run_list = list_runs(target_cwd, path=db_path, limit=limit, offset=offset)
        total = count_runs(target_cwd, path=db_path)
        return {
            "runs": run_list,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str, project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        run = load_run(run_id, target_cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        active_id = get_active_run(target_cwd)
        run["is_active"] = (active_id == run_id)
        if run.get("finished_at") is None and active_id != run_id:
            run["interrupted"] = True
        return run

    @app.get("/api/runs/{run_id}/tool_calls")
    async def run_tool_calls(run_id: str, project: Optional[str] = None) -> dict:
        """Return the tool call history for a run."""
        target_cwd = _resolve_project(project)
        run = load_run(run_id, target_cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        calls = get_tool_calls(run_id, target_cwd, path=db_path)
        return {"tool_calls": calls}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, project: Optional[str] = None) -> dict:
        """Return the streaming events history for a run."""
        target_cwd = _resolve_project(project)
        run = load_run(run_id, target_cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        events = list_events(run_id, path=db_path)
        return {"events": events}

    @app.get("/api/runs/{run_id}/stream")
    async def run_stream(run_id: str, request: Request, project: Optional[str] = None) -> StreamingResponse:
        """Stream live run events as Server-Sent Events (SSE)."""
        target_cwd = _resolve_project(project)
        run = load_run(run_id, target_cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        async def event_generator():
            last_seq = 0
            raw_last_id = request.headers.get("last-event-id")
            if raw_last_id is not None:
                try:
                    last_seq = int(raw_last_id)
                except ValueError:
                    last_seq = 0

            # Immediate check before loop: if run is already finished or orphaned/interrupted
            initial_run = load_run(run_id, target_cwd, path=db_path)
            if initial_run is not None:
                if initial_run.get("finished_at") is not None:
                    events = list_events(run_id, after_seq=last_seq, path=db_path)
                    for ev in events:
                        yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev['payload'])}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    return
                active_id = get_active_run(target_cwd)
                if active_id != run_id:
                    events = list_events(run_id, after_seq=last_seq, path=db_path)
                    for ev in events:
                        yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev['payload'])}\n\n"
                    yield f"event: interrupted\ndata: {json.dumps({'run_id': run_id, 'interrupted': True})}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    return

            last_event_time = time.time()
            max_iterations = 7200
            iterations = 0

            while iterations < max_iterations:
                if await request.is_disconnected():
                    return

                events = list_events(run_id, after_seq=last_seq, path=db_path)
                if events:
                    for ev in events:
                        yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev['payload'])}\n\n"
                        last_seq = ev["seq"]
                    last_event_time = time.time()
                else:
                    current_run = load_run(run_id, target_cwd, path=db_path)
                    if current_run is not None:
                        if current_run.get("finished_at") is not None:
                            yield "event: done\ndata: {}\n\n"
                            return
                        active_id = get_active_run(target_cwd)
                        if active_id != run_id:
                            yield f"event: interrupted\ndata: {json.dumps({'run_id': run_id, 'interrupted': True})}\n\n"
                            yield "event: done\ndata: {}\n\n"
                            return

                    now = time.time()
                    if now - last_event_time >= 15.0:
                        yield ": keepalive\n\n"
                        last_event_time = now

                if await request.is_disconnected():
                    return

                await asyncio.sleep(0.5)
                iterations += 1

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/sessions")
    async def sessions(project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        sess_list = list_sessions(target_cwd, path=db_path)
        return {"sessions": sess_list}

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str, project: Optional[str] = None) -> dict:
        session = get_session(session_id, path=db_path)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        runs = get_session_runs(session_id, path=db_path)
        session["runs"] = runs
        session["total_cost"] = session_cost(runs)
        session["run_count"] = len(runs)
        return session

    @app.post("/api/runs")
    async def create_run(data: RunCreate) -> dict:
        target_cwd = _resolve_project(data.project)
        # Build base config (from file or defaults)
        try:
            base_config = load_config(config_path)
        except Exception:
            base_config = Config(
                review=RoleConfig(backend="claude-code"),
                build=RoleConfig(backend="antigravity"),
                verify=RoleConfig(backend="claude-code"),
            )
        config = _build_config_from_overrides(base_config, data)

        active_run_id = get_active_run(target_cwd)
        if active_run_id is not None:
            queue_id = add_queued_run(
                cwd=target_cwd,
                goal=data.goal,
                session_id=data.session_id,
                config=config.model_dump(),
                path=db_path,
            )
            return {"status": "queued", "queue_id": queue_id}

        run_id = new_run_id()
        session_id = data.session_id or new_session_id()

        def _run_worker() -> None:
            try:
                run_workflow(
                    goal=data.goal,
                    config=config,
                    cwd=target_cwd,
                    run_id=run_id,
                    session_id=session_id,
                    database_path=db_path,
                )
            except RunInProgressError:
                add_queued_run(
                    cwd=target_cwd,
                    goal=data.goal,
                    session_id=session_id,
                    config=config.model_dump(),
                    path=db_path,
                )

        # Start workflow in a background thread so the API returns immediately
        thread = threading.Thread(
            target=_run_worker,
            name=f"agentflow-{run_id}",
            daemon=True,
        )
        thread.start()
        return {"run_id": run_id, "session_id": session_id, "status": "started"}

    @app.post("/api/runs/{run_id}/messages")
    async def send_message(run_id: str, data: MessageCreate, project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        run = load_run(run_id, target_cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        msg_id = add_pending_message(run_id, data.body, kind=data.kind, path=db_path)
        return {"ok": True, "id": msg_id}

    @app.get("/api/runs/{run_id}/messages")
    async def run_messages(run_id: str, project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        run = load_run(run_id, target_cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        msgs = get_pending_messages(run_id, include_consumed=True, path=db_path)
        return {"messages": msgs}

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str, project: Optional[str] = None) -> dict:
        target_cwd = _resolve_project(project)
        run = load_run(run_id, target_cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        add_control_signal(run_id, "stop", path=db_path)
        return {"ok": True}

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def spa_catch_all(full_path: str) -> HTMLResponse:
        if full_path == "api" or full_path.startswith("api/") or full_path == "static" or full_path.startswith("static/"):
            raise HTTPException(status_code=404, detail="Not found")
        return HTMLResponse(content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    return app
