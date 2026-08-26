# Agent Guidelines for agentflow

See `.github/copilot-instructions.md` for full project conventions.

## Quick Reference
- **Package manager**: `uv`
- **Testing**: `uv run pytest` (always mock external LLM calls and network requests)
- **Web UI**: FastAPI, Jinja2, vendored htmx, vanilla CSS in `src/agentflow/web/`
- **State**: SQLite-backed run snapshots in `~/.agentflow/agentflow.db`
- **Validation**: All config modifications must be validated through Pydantic `Config` before writing to disk
