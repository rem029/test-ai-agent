"""OpenRouter API backend.

A plain HTTPS call to OpenRouter's OpenAI-compatible API, authenticated with
a standard pay-per-token OPENROUTER_API_KEY. No subscription-reuse angle —
see PLAN.md, Findings #3.
"""

from __future__ import annotations

import os

import httpx

from .base import HealthCheckResult

DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
API_BASE = "https://openrouter.ai/api/v1"


class OpenRouterBackend:
    name = "openrouter"

    def __init__(self, model: str | None = None):
        self.model = model or DEFAULT_MODEL

    def health_check(self) -> HealthCheckResult:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return HealthCheckResult(
                self.name, False, "OPENROUTER_API_KEY is not set"
            )

        try:
            resp = httpx.get(
                f"{API_BASE}/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
        except httpx.HTTPError as exc:
            return HealthCheckResult(self.name, False, f"request failed: {exc}")

        if resp.status_code != 200:
            return HealthCheckResult(
                self.name, False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            )

        return HealthCheckResult(self.name, True, f"API key valid: {resp.json()}")

    def run(self, prompt: str, *, cwd: str) -> dict:
        raise NotImplementedError("run() lands in Phase B")
