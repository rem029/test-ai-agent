"""Local admin web UI: view run progress, create tasks, edit backend config.

Bound to 127.0.0.1 by default (see cli.py --serve) - no auth, personal
single-user tool. Runs render live progress by polling the same run-state
JSON orchestrator.py writes (see PLAN.md, "Interface: CLI first, web later").
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ..backends import BACKENDS
from ..config import Config, RoleConfig, dump_config, load_config
from ..models import get_all_models
from ..orchestrator import STATE_DIR, new_run_id, run_workflow

_WEB_DIR = Path(__file__).parent

# Only one workflow run at a time - concurrent runs against the same git
# working tree would clobber each other's file edits/commits. This module is
# imported once per running server process, so a single module-level guard
# is sufficient (no queueing - a second submission just points at the run
# already in flight).
_run_lock = threading.Lock()
_active_run: dict | None = None


def create_app(cwd: str, config_path: str) -> FastAPI:
    app = FastAPI()
    app.state.cwd = cwd
    app.state.config_path = config_path
    app.state.last_thread = None  # test hook: lets tests join() the background thread

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    templates.env.filters["fmt_time"] = (
        lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
    )
    templates.env.filters["total_cost"] = (
        lambda steps: sum((s.get("usage", {}).get("cost_usd") or 0.0) for s in (steps or []))
    )
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    def _runs_dir() -> Path:
        return Path(cwd) / STATE_DIR

    def _list_runs() -> list[dict]:
        d = _runs_dir()
        if not d.exists():
            return []
        runs = []
        for f in sorted(d.glob("*.json"), reverse=True):
            try:
                runs.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        return runs

    def _load_run(run_id: str) -> dict | None:
        f = _runs_dir() / f"{run_id}.json"
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"runs": _list_runs(), "active_run_id": _active_run["run_id"] if _active_run else None},
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str):
        run = _load_run(run_id)
        if run is None:
            return HTMLResponse("run not found", status_code=404)
        return templates.TemplateResponse(request, "run_detail.html", {"run": run})

    @app.get("/runs/{run_id}/fragment", response_class=HTMLResponse)
    def run_fragment(request: Request, run_id: str):
        run = _load_run(run_id)
        if run is None:
            return HTMLResponse("run not found", status_code=404)
        return templates.TemplateResponse(request, "_run_fragment.html", {"run": run})

    @app.post("/runs")
    def create_run(goal: str = Form(...)):
        global _active_run
        with _run_lock:
            if _active_run is not None:
                return RedirectResponse(f"/runs/{_active_run['run_id']}", status_code=303)

            run_id = new_run_id()
            config = load_config(config_path)

            def _worker():
                global _active_run
                try:
                    run_workflow(goal, config, cwd=cwd, run_id=run_id)
                finally:
                    with _run_lock:
                        _active_run = None

            thread = threading.Thread(target=_worker, daemon=True)
            _active_run = {"run_id": run_id}
            app.state.last_thread = thread
            thread.start()

        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/api/models")
    def api_models():
        return get_all_models()

    @app.get("/config", response_class=HTMLResponse)
    def config_edit_form(request: Request):
        config = load_config(config_path)
        return templates.TemplateResponse(
            request,
            "config_edit.html",
            {
                "config": config,
                "backend_names": list(BACKENDS),
                "models_by_backend": get_all_models(),
                "saved": request.query_params.get("saved") == "1",
            },
        )

    @app.post("/config")
    def config_edit_save(
        review_backend: str = Form(...),
        review_model: str = Form(""),
        build_backend: str = Form(...),
        build_model: str = Form(""),
        verify_backend: str = Form(...),
        verify_model: str = Form(""),
        max_iterations: int = Form(3),
    ):
        try:
            config = Config(
                review=RoleConfig(backend=review_backend, model=review_model or None),
                build=RoleConfig(backend=build_backend, model=build_model or None),
                verify=RoleConfig(backend=verify_backend, model=verify_model or None),
                max_iterations=max_iterations,
            )
        except ValidationError as e:
            return HTMLResponse(f"Invalid config: {e}", status_code=422)
        dump_config(config, config_path)
        return RedirectResponse("/config?saved=1", status_code=303)

    return app
