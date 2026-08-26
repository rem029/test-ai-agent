"""OpenRouter API backend.

A plain HTTPS call to OpenRouter's OpenAI-compatible API, authenticated with
a standard pay-per-token OPENROUTER_API_KEY. No subscription-reuse angle —
see PLAN.md, Findings #3.
"""

from __future__ import annotations

import httpx

from ..credentials import CredentialConfigError, openrouter_api_key
from .base import FILE_BLOCK_INSTRUCTIONS, HealthCheckResult, RunResult, Usage, apply_file_blocks

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
        try:
            api_key = openrouter_api_key()
        except CredentialConfigError as exc:
            return HealthCheckResult(self.name, False, str(exc))
        if not api_key:
            return HealthCheckResult(
                self.name,
                False,
                "OPENROUTER_API_KEY is not set (environment or agentflow config)",
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

    def run(self, prompt: str, *, cwd: str, mode: str = "read") -> RunResult:
        try:
            api_key = openrouter_api_key()
        except CredentialConfigError as exc:
            return RunResult(False, str(exc), self._empty_usage(), {})
        if not api_key:
            return RunResult(
                False,
                "OPENROUTER_API_KEY is not set (environment or agentflow config)",
                self._empty_usage(),
                {},
            )

        write = mode == "write"
        full_prompt = f"{prompt}\n\n{FILE_BLOCK_INSTRUCTIONS}" if write else prompt

        try:
            resp = httpx.post(
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": full_prompt}],
                },
                timeout=120,
            )
        except httpx.HTTPError as exc:
            return RunResult(False, f"request failed: {exc}", self._empty_usage(), {})

        if resp.status_code != 200:
            return RunResult(
                False, f"HTTP {resp.status_code}: {resp.text[:500]}", self._empty_usage(), {}
            )

        payload = resp.json()
        text = payload["choices"][0]["message"]["content"] or ""
        usage = self._extract_usage(payload)

        written: list[str] = []
        if write:
            written = apply_file_blocks(text, cwd)

        summary = text if not write else f"wrote {len(written)} file(s): {', '.join(written)}"
        return RunResult(True, summary, usage, payload)

    def _empty_usage(self) -> Usage:
        return Usage(self.name, self.model, None, None, None)

    def _extract_usage(self, payload: dict) -> Usage:
        usage = payload.get("usage", {})
        return Usage(
            backend=self.name,
            model=self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=usage.get("cost"),
        )
