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

`agentflow` is a standard Python package with a console-script entry point
(`agentflow`). Install it as a tool so the command is available from any
directory, the same way the `claude` CLI works:

```bash
uv tool install /path/to/test-ai-agent
```

This puts an `agentflow` launcher in `~/.local/bin`. Make sure that directory
is on your `PATH` (`uv tool update-shell` adds it if needed). After that:

```bash
cd ~/some/other/repo
agentflow --check
agentflow "Add a CONTRIBUTING guide"
```

Like `claude`, `agentflow` operates on the current working directory as the
target repository: all file edits, test runs, and git operations happen there.
It reads `agentflow.config.yaml` from that directory (or use `--config`).

Alternatives:

```bash
uv tool install -e /path/to/test-ai-agent   # editable: code changes apply without reinstalling
pipx install /path/to/test-ai-agent         # if you prefer pipx
```

To upgrade or remove a tool install:

```bash
uv tool upgrade agentflow
uv tool uninstall agentflow
```

## Development

Work on agentflow from a clone of the repository:

```bash
git clone <repo-url> test-ai-agent
cd test-ai-agent
uv sync            # create .venv and install runtime + dev dependencies
```

Run the CLI from source without installing it globally:

```bash
uv run agentflow --check
uv run agentflow "Improve error handling in the CLI"
```

Run the test suite and benchmarks:

```bash
uv run pytest
uv run python benchmarks/tool_loop_bench.py
```

Start the web UI against your local checkout:

```bash
uv run agentflow --serve                          # http://127.0.0.1:8420
uv run agentflow --serve --host 0.0.0.0 --port 4200   # container / remote code-server
```

`--host 0.0.0.0` is required when the dev environment runs inside a container
(for example a Coolify-hosted code-server) so the port is reachable from
outside the container.

If you want a global `agentflow` command that tracks your working changes,
install it editable and it will pick up edits without reinstalling:

```bash
uv tool install -e .
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
