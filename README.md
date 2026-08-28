# agentflow

`agentflow` is a Python multi-agent development workflow orchestrator. It
coordinates review, build, and verification roles using configurable Claude
Code, Antigravity, or OpenRouter backends. A local FastAPI web UI is also
available.

## Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)
- Authentication for each backend configured in `agentflow.config.yaml`

## Install

```bash
uv sync
```

## Configure

The CLI reads `agentflow.config.yaml` from the current directory by default.
Each workflow role can use a different backend and model:

```yaml
review:
  backend: openrouter
  model: deepseek/deepseek-chat
build:
  backend: openrouter
  model: deepseek/deepseek-chat
verify:
  backend: openrouter
  model: deepseek/deepseek-chat
max_iterations: 3
permissions: auto  # auto | prompt | deny
max_cost_usd: 1.00  # optional budget limit in USD
```

For OpenRouter, prefer an environment variable rather than placing a key in a
config file:

```bash
export OPENROUTER_API_KEY="your-key"
```

Check that the configured backends are installed and authenticated:

```bash
uv run agentflow --check
```

List available models:

```bash
uv run agentflow --list-models
uv run agentflow --list-models openrouter
```

List available tools agents can invoke:

```bash
uv run agentflow --list-tools
```

Agentflow includes file, shell, code-analysis, search, and git tools. See
[`TOOLS.md`](TOOLS.md) for the full list and developer guide.

List saved sessions:

```bash
uv run agentflow --list-sessions
```

## Run a workflow

Run from the repository you want agentflow to work on:

```bash
uv run agentflow "Add a README with setup and usage instructions"
```

Resume an existing session with a follow-up turn:

```bash
uv run agentflow --resume <session_id> "Refactor the authentication logic"
```

Set permissions or budget limits for a run:

```bash
uv run agentflow --permissions deny "Audit the codebase without file modifications"
uv run agentflow --max-cost-usd 0.50 "Run a small bugfix"
```

Override a role's backend or model for one invocation:

```bash
uv run agentflow \
  --build-backend openrouter \
  --build-model deepseek/deepseek-chat \
  "Improve error handling in the CLI"
```

Use a different configuration file with `--config path/to/config.yaml`.

## Web UI

Start the local admin UI:

```bash
uv run agentflow --serve --host 0.0.0.0 --port 4200
```

Open `http://localhost:4200` in your browser. The default host and port are
`127.0.0.1` and `8420`.

The UI shows recent runs, a run detail view with step history, a tool call
timeline with expandable output, and a visual diff viewer for file changes.
The runs list and run detail auto-refresh while a workflow is active.

## Test

```bash
uv run pytest
```

## Benchmarks

A lightweight benchmark for the parser, registry, and `ReadFile` tool is
included in `benchmarks/tool_loop_bench.py`:

```bash
uv run python benchmarks/tool_loop_bench.py
```
