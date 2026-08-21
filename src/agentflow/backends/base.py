"""Backend interface shared by every model provider (Claude Code, Antigravity, OpenRouter)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class HealthCheckResult:
    backend: str
    ok: bool
    detail: str


class Backend(Protocol):
    """A model provider an agent role can run on.

    Phase A only implements health_check(); run() lands in Phase B once the
    review/build/verify loop is wired up.
    """

    name: str

    def health_check(self) -> HealthCheckResult:
        """Verify the backend is installed/authenticated and reachable."""
        ...

    def run(self, prompt: str, *, cwd: str) -> dict:
        """Execute prompt against this backend, scoped to cwd. Phase B."""
        raise NotImplementedError("run() lands in Phase B")
