"""FastAPI application for the agentflow web UI."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..backends import BACKENDS
from ..config import (
    Config,
    DEFAULT_CONFIG_PATH,
    PermissionMode,
    RoleConfig,
    dump_config,
    load_config,
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
from ..orchestrator import RunInProgressError, get_active_run, new_run_id, run_workflow

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
    permissions: Optional[PermissionMode] = None
    max_cost_usd: Optional[float] = None


class RunCreate(BaseModel):
    goal: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    review_backend: Optional[str] = None
    review_model: Optional[str] = None
    build_backend: Optional[str] = None
    build_model: Optional[str] = None
    verify_backend: Optional[str] = None
    verify_model: Optional[str] = None
    max_iterations: Optional[int] = Field(None, ge=1, le=10)


class MessageCreate(BaseModel):
    body: str = Field(..., min_length=1)
    kind: str = Field("steer", pattern="^(steer|note)$")



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
        permissions=base_config.permissions,
        max_cost_usd=base_config.max_cost_usd,
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
) -> FastAPI:
    app = FastAPI(title="agentflow Web UI", version="0.1.0")
    db_path = database_path or DEFAULT_DATABASE_PATH

    # Mount static files (CSS, JS, etc.)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ---------- HTML page ----------
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    # ---------- API endpoints ----------
    @app.get("/api/config")
    async def get_config() -> dict:
        try:
            config = load_config(config_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return config.model_dump()

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

        config = Config(
            review=RoleConfig(backend=data.review_backend, model=data.review_model),
            build=RoleConfig(backend=data.build_backend, model=data.build_model),
            verify=RoleConfig(backend=data.verify_backend, model=data.verify_model),
            max_iterations=data.max_iterations,
            permissions=data.permissions if data.permissions is not None else (current_config.permissions if current_config else "auto"),
            max_cost_usd=data.max_cost_usd if data.max_cost_usd is not None else (current_config.max_cost_usd if current_config else None),
        )
        try:
            dump_config(config, config_path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"ok": True, "config": config.model_dump()}

    @app.get("/api/health")
    async def health() -> dict:
        return _health_check_all(config_path)

    @app.get("/api/models")
    async def models() -> dict:
        return get_all_models()

    @app.get("/api/runs")
    async def runs(limit: int = 25, offset: int = 0) -> dict:
        limit = max(1, min(100, limit))
        offset = max(0, offset)
        run_list = list_runs(cwd, path=db_path, limit=limit, offset=offset)
        total = count_runs(cwd, path=db_path)
        return {
            "runs": run_list,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str) -> dict:
        run = load_run(run_id, cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @app.get("/api/runs/{run_id}/tool_calls")
    async def run_tool_calls(run_id: str) -> dict:
        """Return the tool call history for a run."""
        run = load_run(run_id, cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        calls = get_tool_calls(run_id, cwd, path=db_path)
        return {"tool_calls": calls}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str) -> dict:
        """Return the streaming events history for a run."""
        run = load_run(run_id, cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        events = list_events(run_id, path=db_path)
        return {"events": events}

    @app.get("/api/sessions")
    async def sessions() -> dict:
        sess_list = list_sessions(cwd, path=db_path)
        return {"sessions": sess_list}

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str) -> dict:
        session = get_session(session_id, path=db_path)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        runs = get_session_runs(session_id, path=db_path)
        session["runs"] = runs
        return session

    @app.post("/api/runs")
    async def create_run(data: RunCreate) -> dict:
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

        active_run_id = get_active_run(cwd)
        if active_run_id is not None:
            queue_id = add_queued_run(
                cwd=cwd,
                goal=data.goal,
                session_id=data.session_id,
                config=config.model_dump(),
                path=db_path,
            )
            return {"status": "queued", "queue_id": queue_id}

        run_id = new_run_id()

        def _run_worker() -> None:
            try:
                run_workflow(
                    goal=data.goal,
                    config=config,
                    cwd=cwd,
                    run_id=run_id,
                    session_id=data.session_id,
                    database_path=db_path,
                )
            except RunInProgressError:
                add_queued_run(
                    cwd=cwd,
                    goal=data.goal,
                    session_id=data.session_id,
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
        return {"run_id": run_id, "status": "started"}

    @app.post("/api/runs/{run_id}/messages")
    async def send_message(run_id: str, data: MessageCreate) -> dict:
        run = load_run(run_id, cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        msg_id = add_pending_message(run_id, data.body, kind=data.kind, path=db_path)
        return {"ok": True, "id": msg_id}

    @app.get("/api/runs/{run_id}/messages")
    async def run_messages(run_id: str) -> dict:
        run = load_run(run_id, cwd, path=db_path)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        msgs = get_pending_messages(run_id, include_consumed=True, path=db_path)
        return {"messages": msgs}

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str) -> dict:
        run = load_run(run_id, cwd, path=db_path)
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
