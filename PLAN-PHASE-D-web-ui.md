# Phase D — Local Admin Panel (FastAPI + Jinja2 + htmx)

## Context

PLAN.md's Phase A/B/C are done and merged to `dev`. Phase D was originally scoped
as an optional *read-only* run viewer, but the user now wants a real local admin
panel: view run/task progress live, create new tasks from the browser, and edit
`agentflow.config.yaml` from the browser. Already agreed with the user: FastAPI +
Jinja2 + htmx (vendored, no CDN at runtime), uvicorn dev server, bound to
`127.0.0.1` only, no auth (personal single-user tool), no JS build step.

Verified network access works in this sandbox (PyPI and unpkg both reachable), so
new deps and the vendored htmx file were fetched directly as part of this work.

## New dependencies (`pyproject.toml`)

Added to `dependencies`: `fastapi>=0.115`, `uvicorn>=0.34`, `jinja2>=3.1`,
`python-multipart>=0.0.9` (needed for FastAPI form parsing).

## `src/agentflow/orchestrator.py`

- Factored run-id generation into `new_run_id() -> str` (same
  `time.strftime(...) + uuid...` format as before).
- `run_workflow(goal, config, cwd, run_id: str | None = None)` — generates via
  `new_run_id()` when not passed, and calls `state.save(cwd)` immediately after
  constructing `RunState`, before the review step, so a poller sees the run
  exists right away instead of only after review completes.
- `state.config["max_iterations"] = config.max_iterations` alongside the
  existing per-role config dump, so the run-detail page can show it.
- Existing callers (`cli.py`, `tests/test_cli.py`'s mocked `run_workflow`) are
  unaffected — `run_id` is optional and `test_cli.py` mocks `run_workflow`
  entirely.

## `src/agentflow/config.py`

Added a validated write-back helper, `dump_config(config: Config, path: str)`.
The web layer always constructs a validated `Config(...)` (pydantic raises on a
bad `backend` literal or non-int `max_iterations`) *before* calling this, so
invalid input never reaches disk.

## `src/agentflow/cli.py`

Added `--serve`/`--host`/`--port` flags. `--serve` starts
`uvicorn.run(create_app(cwd, config_path), host=args.host, port=args.port)` and
returns before touching `--check`/goal handling — it does not require a `goal`.
`uvicorn`/`create_app` are imported lazily inside the branch so plain CLI runs
never require the web deps.

## `src/agentflow/web/` (new package)

```
web/
  app.py
  templates/ (base, dashboard, run_detail, _run_fragment, config_edit)
  static/ (htmx.min.js vendored from unpkg, style.css)
```

Routes:

| Method | Path | Behavior |
|---|---|---|
| GET | `/` | Dashboard: list `.agentflow/runs/*.json` newest-first, new-task form, active-run banner |
| GET | `/runs/{run_id}` | Full detail page; polls via htmx only while `finished_at is None` |
| GET | `/runs/{run_id}/fragment` | Steps/status fragment, htmx poll target |
| POST | `/runs` | Create task: under a lock, redirect to the active run if one exists, else spawn a daemon thread running `run_workflow(..., run_id=...)` and redirect to it |
| GET | `/config` | Edit form pre-filled from `load_config` |
| POST | `/config` | Validate via `Config(...)`, `dump_config`, redirect with `?saved=1` |

Concurrency: one run at a time, guarded by a module-level `threading.Lock` +
`_active_run` — a second `POST /runs` while one is active redirects to the
existing run instead of erroring or queuing.

Polling stop condition: `_run_fragment.html`'s root element only carries
`hx-trigger="every 2s"` when the run is unfinished, so polling stops itself
once a poll response reflects a finished run — no JS, no stop button.

## `tests/test_web.py`

Mocks `run_workflow` the same way `tests/test_cli.py` does, via
`fastapi.testclient.TestClient`. Covers: empty/populated dashboard, task
creation redirect + background thread invocation, second-task-while-active
redirecting to the existing run, and config-form validation (rejects bad
backend, round-trips a valid write through `load_config`).

## Verification

```
uv sync
uv run pytest
uv run agentflow --version
uv run agentflow --check
```

Manual: `agentflow --serve` against a scratch copy of the two real
`.agentflow/runs/*.json` fixtures; submit a task against a monkeypatched slow
fake `run_workflow` to confirm live polling and its stop condition; confirm a
second submission while one is active redirects instead of double-starting;
exercise `/config`'s validation and round-trip.

Note: a follow-up design polish pass (Impeccable) is tracked as its own
phase — see PLAN.md, "Phase E — Design polish pass (Impeccable)" — not part
of this document's scope.

## Branch / commit

Implemented on `phase-d`, cut from `dev`. Left for the user to review/merge,
same as prior phases.

## Development environment: hosted code-server (Coolify)

This project is developed inside a code-server instance hosted on the user's
own Coolify server, alongside several other projects each with their own
Coolify domain mapping (a Traefik-style reverse proxy in front of the
container, routing `https://<subdomain>.app.rem029.com:<port>` to a port
inside the container).

- **Dev URL for the agentflow web UI**: `https://agentui.app.rem029.com/`,
  mapped in Coolify to container port **4200**.
- Because Coolify's reverse proxy reaches the container over the docker
  network rather than true localhost, the server must bind to `0.0.0.0`, not
  the `127.0.0.1`-only default described above — the default is still right
  for a genuinely local/personal run, but this specific hosted dev
  environment needs the wider bind for the proxy to reach it at all:
  ```
  agentflow --serve --host 0.0.0.0 --port 4200
  ```
- No auth is added at the app level (per the original design decision) — the
  Coolify domain is the only thing standing between this and the open
  internet, so treat it as effectively unauthenticated while it's up. Stop
  the server when not actively demoing/testing it, and don't submit tasks
  from it that you wouldn't want triggered by anyone who finds the URL.
- Port 4200 is reused as both the `--port` flag's default (picked during
  implementation, before this Coolify domain existed) and now Coolify's
  fixed mapping — keep them in sync if either ever changes; they're not
  automatically linked.
