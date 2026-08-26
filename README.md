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

## Run a workflow

Run from the repository you want agentflow to work on:

```bash
uv run agentflow "Add a README with setup and usage instructions"
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

## Test

```bash
uv run pytest
```
