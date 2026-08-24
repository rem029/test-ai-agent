"""Backend interface shared by every model provider (Claude Code, Antigravity, OpenRouter)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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


class Backend(Protocol):
    """A model provider an agent role can run on."""

    name: str

    def health_check(self) -> HealthCheckResult:
        """Verify the backend is installed/authenticated and reachable."""
        ...

    def run(self, prompt: str, *, cwd: str, mode: str = "read") -> RunResult:
        """Execute prompt against this backend, scoped to cwd.

        mode is "read" (review/plan, no side effects), "write" (build, may
        change files), or "verify" (run tests/lint, may run commands but not
        edit files) - see MODE_ALLOWED_TOOLS.
        """
        ...
