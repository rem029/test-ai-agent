# agentflow Tool Developer Guide

## Overview

agentflow includes a built-in tool layer so agents can interact with the
repository, run commands, analyze code, and fetch documentation. Tools are
deterministic Python functions invoked by the orchestrator, not by the LLM
directly. This keeps the control loop auditable and testable.

## Tool Architecture

```
Agent response
      |
      v
parse_tool_requests()  -->  ParsedToolRequest(name, args)
      |
      v
ToolRegistry.get(name)  -->  Tool instance
      |
      v
tool.run(args, context=ToolContext(cwd=...))
      |
      v
ToolResult(success, output, error, duration_ms, structured)
```

`structured` is an optional dict for machine-readable metadata (e.g. the
previous and current content of a file written by `WriteFile`). The web UI
uses it to render visual diffs.
```

The orchestrator (`orchestrator.py`) wraps each backend call in a tool loop:

1. Sends the prompt plus tool instructions and schemas.
2. Parses the response for `<tool_call>` XML blocks.
3. Executes requested tools.
4. Appends results to the conversation context.
5. Repeats until the agent provides a final answer or the max call limit is hit.

## Built-in Tools

| Tool | Purpose |
|------|---------|
| `ReadFile` | Read file contents, optionally by line range. |
| `WriteFile` | Create or overwrite a file. |
| `ListDirectory` | List files and directories. |
| `SearchFiles` | Search file contents by regex or glob. |
| `Shell` | Execute a shell command with timeout. |
| `Lint` | Run a linter (ruff when available, else py_compile). |
| `TypeCheck` | Run a type checker (pyright when available, else syntax check). |
| `ImportAnalysis` | List Python imports with `ast`. |
| `WebFetch` | Fetch a URL via `httpx`. |
| `CodeSearch` | Regex search across source files by language. |
| `DocumentationSearch` | Fetch docs from curated sources. |
| `GitStatus` | Show `git status --short`. |
| `GitDiff` | Show `git diff` or `git diff --cached`. |
| `GitCommitSimulation` | Preview what would be committed. |

## Adding a New Tool

Create a class that inherits from `Tool` and register it.

```python
# src/agentflow/tools/my_tool.py
from pydantic import BaseModel, Field

from .base import Tool, ToolContext, ToolResult
from .registry import register_tool


class GreetParams(BaseModel):
    name: str = Field(..., description="Name to greet.")


class GreetTool(Tool):
    name = "Greet"
    description = "Return a friendly greeting."
    param_model = GreetParams

    def execute(self, context: ToolContext, name: str) -> ToolResult:
        return ToolResult(success=True, output=f"Hello, {name}!")


register_tool(GreetTool())
```

Then import the module in `src/agentflow/tools/__init__.py`:

```python
from . import my_tool  # noqa: F401
```

The tool will appear in `agentflow --list-tools` and be available to agents.

## Tool Request Format

Agents request tools with XML blocks:

```xml
<tool_call>
  <ReadFile>
    <args>
      <path>src/agentflow/cli.py</path>
      <start_line>1</start_line>
      <end_line>50</end_line>
    </args>
  </ReadFile>
</tool_call>
```

The parser also supports fenced JSON blocks:

```json
{"name": "Shell", "args": {"command": "uv run pytest"}}
```

## Context and Security

- Every tool receives a `ToolContext` with the target working directory.
- File tools reject paths that escape the working directory.
- Shell commands run with a timeout and a best-effort blocklist of dangerous
  substrings. This is **not a sandbox**; run agentflow only in environments
  where arbitrary shell execution is acceptable.

## Permission Policy

Tool execution is governed by the `permissions` setting (`auto | prompt | deny`):
- **Read-only tools** (`ReadFile`, `ListDirectory`, `SearchFiles`, `CodeSearch`, `WebFetch`, `DocumentationSearch`, `Lint`, `TypeCheck`, `ImportAnalysis`, `GitStatus`, `GitDiff`, `GitCommitSimulation`) are always automatically allowed.
- **Mutating tools** (`WriteFile`, `Shell`, git mutating commands) follow the configured policy:
  - `auto` (default): automatically allowed.
  - `prompt`: prompts interactively (`[y/N]`) before executing. In non-interactive runs, acts as `deny` with a logged reason.
  - `deny`: immediately blocked with a permission error.

## Example Workflow

When given the goal:

> "Add a `--list-tools` flag to the CLI"

A tool-aware agent might produce this sequence:

```xml
<tool_call>
  <ReadFile>
    <args>
      <path>src/agentflow/cli.py</path>
    </args>
  </ReadFile>
</tool_call>
```

The orchestrator reads `cli.py`, appends the result to the prompt, and the
agent responds with the code change. The verify step then runs:

```xml
<tool_call>
  <Shell>
    <args>
      <command>uv run agentflow --list-tools</command>
    </args>
  </Shell>
</tool_call>
```

and confirms the new flag works before marking the task complete.

## Testing

Add tests under `tests/tools/` mirroring the existing suites. Tests should use
`ToolContext(cwd=str(tmp_path))` and never rely on the repository root as the
tool working directory.
