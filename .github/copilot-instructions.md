# agentflow Developer Instructions

## Project Overview
`agentflow` is a Python-based multi-agent development workflow tool with pluggable LLM backends (Claude Code, Antigravity, OpenRouter) and a local admin web UI (FastAPI + Jinja2 + htmx).

## Key Architecture Principles
- **No LLM in coordination loop**: Control flow, step iteration, and git operations are managed in deterministic Python (`orchestrator.py`).
- **Pluggable backends**: Roles (`review`, `build`, `verify`) are configured in `agentflow.config.yaml` and loaded via Pydantic models (`config.py`).
- **State persistence**: Workflow runs persist as SQLite snapshots in `~/.agentflow/agentflow.db`.
- **Web UI**: Zero-build frontend using vendored htmx (`static/htmx.min.js`), server-rendered Jinja2 templates, and vanilla CSS (`style.css`).

## Common Commands
- **Run tests**: `uv run pytest`
- **Run CLI**: `uv run agentflow "<goal>"`
- **Check environment/backends**: `uv run agentflow --check`
- **Start Web Admin UI**: `uv run agentflow --serve --host 0.0.0.0 --port 4200`
- **Sync dependencies**: `uv sync`

## Coding & Design Conventions
- Keep the web UI lightweight with no external CDN or node build runtime dependencies.
- Retain dark/light mode compatibility and accessible contrast in `style.css`.
- Ensure new tests in `tests/` mock backend calls and `run_workflow` to keep test suite fast and offline-friendly.
