"""Google Antigravity backend.

Two ways to reach Antigravity, tried in this order:

1. **CLI, headless mode** — shells out to the `antigravity` binary,
   authenticated via a cached Google account OAuth session (Google AI
   Pro/Ultra rate limits apply automatically, no separate API key). This is
   the preferred path for personal/subscription use (PLAN.md, Findings #2),
   but the CLI is distributed from antigravity.google and installed via an
   interactive browser login — it could not be installed or verified in
   this sandboxed environment, whose network egress to antigravity.google
   is blocked. Confirm this path on a machine that can actually reach it.

2. **`google-antigravity` Python SDK fallback** — pip-installable, works in
   any environment including this sandbox, but authenticates with
   GEMINI_API_KEY (or Vertex ADC) rather than the Google account
   subscription (PLAN.md, Findings #2 — "no consumer-subscription path").
   Useful for CI/sandboxed runs, or as an explicit opt-in when the user
   would rather spend Gemini API credit than subscription usage.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .base import HealthCheckResult


class AntigravityBackend:
    name = "antigravity"

    def __init__(self, model: str | None = None):
        self.model = model

    def health_check(self) -> HealthCheckResult:
        cli_result = self._check_cli()
        if cli_result is not None:
            return cli_result
        return self._check_sdk()

    def _check_cli(self) -> HealthCheckResult | None:
        """Returns None if the CLI isn't present, so callers fall back to the SDK."""
        binary = shutil.which("antigravity")
        if not binary:
            return None

        try:
            proc = subprocess.run(
                ["antigravity", "-p", "Reply with exactly one word: pong"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return HealthCheckResult(self.name, False, "antigravity -p timed out")

        if proc.returncode != 0:
            return HealthCheckResult(
                self.name,
                False,
                f"antigravity -p exited {proc.returncode}: {proc.stderr.strip()}",
            )

        return HealthCheckResult(self.name, True, f"CLI: {proc.stdout.strip()[:200]}")

    def _check_sdk(self) -> HealthCheckResult:
        try:
            from google.antigravity import Agent, LocalAgentConfig
        except ImportError as exc:
            return HealthCheckResult(
                self.name,
                False,
                f"neither `antigravity` CLI nor google-antigravity SDK usable: {exc}",
            )

        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get(
            "GOOGLE_GENAI_USE_VERTEXAI"
        ):
            return HealthCheckResult(
                self.name,
                False,
                "`antigravity` CLI not found on PATH, and neither GEMINI_API_KEY "
                "nor Vertex AI credentials are set for the SDK fallback",
            )

        return HealthCheckResult(
            self.name,
            True,
            "SDK fallback configured (GEMINI_API_KEY/Vertex set) — "
            "connectivity not exercised yet, see PLAN.md Phase B",
        )

    def run(self, prompt: str, *, cwd: str) -> dict:
        raise NotImplementedError("run() lands in Phase B")
