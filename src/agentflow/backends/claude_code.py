"""Claude Code CLI backend.

Shells out to the `claude` CLI in non-bare, non-interactive mode. As long as
ANTHROPIC_API_KEY is unset and --bare is not passed, `claude -p` reads the
existing `claude login` OAuth session, billing against the user's Claude
subscription rather than pay-per-token API usage. See PLAN.md, Findings #1.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import HealthCheckResult


class ClaudeCodeBackend:
    name = "claude-code"

    def __init__(self, model: str | None = None):
        self.model = model

    def health_check(self) -> HealthCheckResult:
        binary = shutil.which("claude")
        if not binary:
            return HealthCheckResult(
                self.name, False, "`claude` CLI not found on PATH"
            )

        try:
            proc = subprocess.run(
                [
                    "claude",
                    "-p",
                    "Reply with exactly one word: pong",
                    "--output-format",
                    "json",
                    "--allowedTools",
                    "",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return HealthCheckResult(self.name, False, "claude -p timed out")

        if proc.returncode != 0:
            return HealthCheckResult(
                self.name, False, f"claude -p exited {proc.returncode}: {proc.stderr.strip()}"
            )

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return HealthCheckResult(
                self.name, False, f"non-JSON output: {proc.stdout[:200]!r}"
            )

        if payload.get("is_error"):
            return HealthCheckResult(
                self.name, False, f"error result: {payload.get('result')!r}"
            )

        return HealthCheckResult(
            self.name, True, f"authenticated, session_id={payload.get('session_id')}"
        )

    def run(self, prompt: str, *, cwd: str) -> dict:
        raise NotImplementedError("run() lands in Phase B")
