"""OpenRouter API backend.

A plain HTTPS call to OpenRouter's OpenAI-compatible API, authenticated with
a standard pay-per-token OPENROUTER_API_KEY. No subscription-reuse angle —
see PLAN.md, Findings #3.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from ..credentials import CredentialConfigError, openrouter_api_key
from .base import (
    Event,
    FILE_BLOCK_INSTRUCTIONS,
    HealthCheckResult,
    Message,
    RunResult,
    Usage,
    apply_file_blocks,
    run_sync,
)

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

    def run(
        self,
        prompt: str | list[Message],
        *,
        cwd: str,
        mode: str = "read",
        tools: list[dict] | None = None,
    ) -> Iterator[Event]:
        try:
            api_key = openrouter_api_key()
        except CredentialConfigError as exc:
            yield Event.error(str(exc))
            yield Event.done(success=False)
            return
        if not api_key:
            yield Event.error("OPENROUTER_API_KEY is not set (environment or agentflow config)")
            yield Event.done(success=False)
            return

        write = mode == "write"
        if isinstance(prompt, str):
            full_prompt = f"{prompt}\n\n{FILE_BLOCK_INSTRUCTIONS}" if write else prompt
            messages: list[dict[str, Any]] = [{"role": "user", "content": full_prompt}]
        else:
            messages = []
            for m in prompt:
                role = m.role if m.role in ("system", "user", "assistant") else "user"
                messages.append({"role": role, "content": m.content})
            if write and messages and FILE_BLOCK_INSTRUCTIONS not in messages[-1]["content"]:
                messages[-1]["content"] += f"\n\n{FILE_BLOCK_INSTRUCTIONS}"

        accumulated_text: list[str] = []
        usage_found: Usage | None = None
        last_chunk: dict[str, Any] = {}

        try:
            with httpx.stream(
                "POST",
                f"{API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
                timeout=120,
            ) as resp:
                if resp.status_code != 200:
                    err_body = resp.read().decode("utf-8", errors="replace")
                    yield Event.error(f"HTTP {resp.status_code}: {err_body[:500]}")
                    yield Event.done(success=False)
                    return

                for line in resp.iter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    last_chunk = chunk
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            accumulated_text.append(content)
                            yield Event.text_delta(content)

                    if "usage" in chunk and chunk["usage"]:
                        usage_found = self._extract_usage(chunk)
                        yield Event.usage(usage_found)
        except httpx.HTTPError as exc:
            yield Event.error(f"request failed: {exc}")
            yield Event.done(success=False)
            return

        full_text = "".join(accumulated_text)
        written: list[str] = []
        if write:
            written = apply_file_blocks(full_text, cwd)

        if not usage_found:
            yield Event.usage(self._empty_usage())

        summary = full_text if not write else f"wrote {len(written)} file(s): {', '.join(written)}"
        yield Event.done(success=True, text=summary, raw=last_chunk)

    def run_sync(
        self,
        prompt: str | list[Message],
        *,
        cwd: str,
        mode: str = "read",
        tools: list[dict] | None = None,
    ) -> RunResult:
        return run_sync(self.run(prompt, cwd=cwd, mode=mode, tools=tools))

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
