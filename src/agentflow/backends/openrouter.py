"""OpenRouter API backend.

A plain HTTPS call to OpenRouter's OpenAI-compatible API, authenticated with
a standard pay-per-token OPENROUTER_API_KEY. No subscription-reuse angle —
see PLAN.md, Findings #3.
"""

from __future__ import annotations

import os

import httpx

from .base import HealthCheckResult

# Requested default: a cheap/fast DeepSeek model for the build role.
# Verified against OpenRouter's live /models and /chat/completions from a
# machine with real network access (this sandbox blocks openrouter.ai) —
# it's a real slug, 1M context, ~$0.05/$0.10 per M input/output tokens, and
# chat completion responses include usage.cost directly (see PLAN.md,
# Findings #3 and "Cost & token tracking per task").
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
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

        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            resp = httpx.post(
                f"{API_BASE}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": "Reply with exactly one word: pong"}
                    ],
                },
                timeout=30,
            )
        except httpx.HTTPError as exc:
            return HealthCheckResult(self.name, False, f"request failed: {exc}")

        if resp.status_code != 200:
            return HealthCheckResult(
                self.name, False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            )

        payload = resp.json()
        usage = payload.get("usage", {})
        return HealthCheckResult(
            self.name,
            True,
            f"model={self.model}, tokens={usage.get('total_tokens')}, "
            f"cost_usd={usage.get('cost')}",
        )

    def run(self, prompt: str, *, cwd: str) -> dict:
        raise NotImplementedError("run() lands in Phase B")
