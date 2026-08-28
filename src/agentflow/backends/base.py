"""Backend interface shared by every model provider (Claude Code, Antigravity, OpenRouter)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

# Shared convention for backends with no confirmed native file-editing tool
# (OpenRouter's plain chat completion, Antigravity's SDK fallback): ask the
# model to emit full file contents in a FILE block, then write it ourselves.
# Claude Code and the Antigravity CLI have their own real file tools and
# don't need this.
FILE_BLOCK_INSTRUCTIONS = """
When you need to create or overwrite a file, output its full new content in
a block shaped exactly like this (one block per file, no other formatting
around the marker line):

```FILE: relative/path/to/file.ext
<the file's complete new content>
```

Only include files you are actually changing. Do not truncate or use "...".
""".strip()

FILE_BLOCK_RE = re.compile(r"```FILE:\s*(?P<path>\S+)\n(?P<content>.*?)\n```", re.DOTALL)


# Shared role semantics for backends with real tool permissions (Claude
# Code, Antigravity CLI): what each orchestrator step is allowed to touch.
MODE_ALLOWED_TOOLS = {
    "read": "Read,Grep,Glob",
    "verify": "Read,Bash,Grep,Glob",
    "write": "Read,Write,Edit,Bash,Grep,Glob",
}
MODE_PERMISSION = {
    "read": None,
    "verify": None,
    "write": "acceptEdits",
}


def apply_file_blocks(text: str, cwd: str) -> list[str]:
    """Parse FILE blocks out of text and write them under cwd. Returns paths written."""
    base = Path(cwd).resolve()
    written = []
    for match in FILE_BLOCK_RE.finditer(text):
        rel_path = match.group("path").strip()
        target = (base / rel_path).resolve()
        if base not in target.parents and target != base:
            continue  # refuse to write outside cwd
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(match.group("content") + "\n")
        written.append(rel_path)
    return written


@dataclass
class HealthCheckResult:
    backend: str
    ok: bool
    detail: str


@dataclass
class Usage:
    """Normalized token/cost accounting, same shape regardless of backend."""

    backend: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


@dataclass
class RunResult:
    success: bool
    text: str
    usage: Usage
    raw: dict


@dataclass
class Event:
    """Typed streaming event emitted by backends and logged by the orchestrator."""

    type: str  # "text_delta", "tool_call", "tool_result", "usage", "done", "error"
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": self.payload}

    @classmethod
    def text_delta(cls, delta: str) -> Event:
        return cls(type="text_delta", payload={"delta": delta})

    @classmethod
    def tool_call(cls, name: str, args: dict[str, Any]) -> Event:
        return cls(type="tool_call", payload={"name": name, "args": args})

    @classmethod
    def tool_result(
        cls,
        name: str,
        result: dict[str, Any] | str,
        success: bool = True,
        error: str | None = None,
    ) -> Event:
        return cls(
            type="tool_result",
            payload={"name": name, "result": result, "success": success, "error": error},
        )

    @classmethod
    def usage(cls, usage: Usage) -> Event:
        return cls(type="usage", payload=asdict(usage))

    @classmethod
    def done(
        cls,
        success: bool = True,
        text: str = "",
        raw: dict[str, Any] | None = None,
    ) -> Event:
        return cls(type="done", payload={"success": success, "text": text, "raw": raw or {}})

    @classmethod
    def error(cls, error: str) -> Event:
        return cls(type="error", payload={"error": error})


@dataclass
class Message:
    """Structured message in a conversation thread."""

    role: str  # "system", "user", "assistant", "tool"
    content: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    tool_results: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            data["tool_calls"] = self.tool_calls
        if self.tool_results is not None:
            data["tool_results"] = self.tool_results
        return data


def format_messages_to_prompt(prompt: str | list[Message]) -> str:
    """Convert a list[Message] or string prompt into a single prompt string."""
    if isinstance(prompt, str):
        return prompt
    parts: list[str] = []
    for m in prompt:
        if m.role == "system":
            parts.append(f"System:\n{m.content}")
        elif m.role == "assistant":
            parts.append(f"Assistant:\n{m.content}")
        elif m.role == "tool":
            parts.append(f"Tool Result:\n{m.content}")
        else:
            parts.append(f"User:\n{m.content}")
    return "\n\n".join(parts)


def run_sync(events: Iterable[Event] | RunResult) -> RunResult:
    """Drain an event generator into a RunResult so callers work unchanged."""
    if isinstance(events, RunResult):
        return events

    accumulated_text: list[str] = []
    usage = Usage(backend="unknown", model=None, input_tokens=None, output_tokens=None, cost_usd=None)
    success = True
    raw: dict[str, Any] = {}
    error_msg: str | None = None
    done_text: str = ""

    for event in events:
        if event.type == "text_delta":
            accumulated_text.append(event.payload.get("delta", ""))
        elif event.type == "usage":
            p = event.payload
            usage = Usage(
                backend=p.get("backend", usage.backend),
                model=p.get("model", usage.model),
                input_tokens=p.get("input_tokens", usage.input_tokens),
                output_tokens=p.get("output_tokens", usage.output_tokens),
                cost_usd=p.get("cost_usd", usage.cost_usd),
            )
        elif event.type == "error":
            success = False
            error_msg = event.payload.get("error")
        elif event.type == "done":
            if "success" in event.payload:
                success = event.payload["success"]
            if "raw" in event.payload:
                raw = event.payload["raw"]
            if "text" in event.payload:
                done_text = event.payload["text"]

    text = "".join(accumulated_text)
    if not text and done_text:
        text = done_text
    if not success and error_msg and not text:
        text = error_msg

    return RunResult(success=success, text=text, usage=usage, raw=raw)


class Backend(Protocol):
    """A model provider an agent role can run on."""

    name: str

    def health_check(self) -> HealthCheckResult:
        """Verify the backend is installed/authenticated and reachable."""
        ...

    def run(
        self,
        prompt: str | list[Message],
        *,
        cwd: str,
        mode: str = "read",
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[Event]:
        """Execute prompt against this backend, yielding typed events."""
        ...

    def run_sync(
        self,
        prompt: str | list[Message],
        *,
        cwd: str,
        mode: str = "read",
        tools: list[dict[str, Any]] | None = None,
    ) -> RunResult:
        """Execute prompt synchronously, returning a RunResult."""
        ...
