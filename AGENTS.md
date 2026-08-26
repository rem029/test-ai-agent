# Agent Guidelines for agentflow

See `.github/copilot-instructions.md` for full project conventions.

## Quick Reference
- **Package manager**: `uv`
- **Testing**: `uv run pytest` (always mock external LLM calls and network requests)
- **Web UI**: FastAPI, Jinja2, vendored htmx, vanilla CSS in `src/agentflow/web/`
- **State**: Serialized per-run JSON under `.agentflow/runs/`
- **Validation**: All config modifications must be validated through Pydantic `Config` before writing to disk
