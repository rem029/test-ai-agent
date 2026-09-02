# Agent Guidelines for agentflow

Python multi-agent workflow orchestrator (review → build → verify → iterate → push) with pluggable LLM backends and a local web UI. See `.github/copilot-instructions.md` for conventions and `DESIGN.md` for the web console design contract.

## Commands
- Setup: `uv sync`
- Run CLI from source: `uv run agentflow "<goal>"`, `uv run agentflow --check`
- Tests: `uv run pytest` — ~400 tests, offline, all backends mocked
- Single test: `uv run pytest tests/test_config.py -k some_name`
- Web UI: `uv run agentflow --serve` (default `127.0.0.1:8420`); add `--host 0.0.0.0` in containers/code-server or the port won't be reachable
- Benchmark: `uv run python benchmarks/tool_loop_bench.py`
- There is no lint/typecheck/format step and no CI — `pytest` is the only gate

## Architecture (where things actually are)
- Entry point: `[project.scripts]` → `agentflow/__init__.py` → `cli.py:main`
- `orchestrator.py` — the control loop is deterministic Python; no LLM manages control flow, step iteration, or git ops
- `backends/` — pluggable Claude Code / Antigravity / OpenRouter; roles configured in `agentflow.config.yaml`
- `web/app.py` — FastAPI JSON API under `/api/*`; `web/static/` — zero-build vanilla-JS SPA (no CDN, no node build); keep it that way
- State: SQLite snapshots in `~/.agentflow/agentflow.db`; config file is per-project (`agentflow.config.yaml` in cwd), written mode `0600`

## Conventions that differ from defaults
- All config modifications must be validated through the Pydantic `Config` model (`config.py`) before writing to disk — never write `agentflow.config.yaml` by hand-editing YAML from code
- Env var with the same name overrides config file values (dev-only convenience); `AGENTFLOW_MCP_DISABLED=1` kills all MCP servers
- MCP servers are declared in `agentflow.config.yaml` under `mcp_servers:`, exposed to agents as `mcp__<server>__<tool>`, stdio transport only; `uv run agentflow --mcp-check` verifies them

## Testing quirks
- `tests/conftest.py` has an autouse fixture that monkeypatches module-level `DEFAULT_DATABASE_PATH` and `AGENTFLOW_HOME` constants to tmp paths so tests never touch real `~/.agentflow/`. If you add such a module-level constant in a new module, add it to that patch list — otherwise tests will write to the user's real home dir
- New tests must mock backend calls and `run_workflow` — the suite is designed to stay fast and offline (integration tests in `test_integration.py` also use mocked backends, only tools run for real)
