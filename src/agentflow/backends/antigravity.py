"""Google Antigravity backend.

Two ways to reach Antigravity, tried in this order:

1. **CLI, headless mode** — shells out to the `agy` binary (or legacy
   `antigravity`), authenticated via the user's Google account / Antigravity
   subscription (Google AI Pro/Ultra rate limits apply automatically, no
   separate API key). This is the preferred path for personal/subscription use.

2. **`google-antigravity` Python SDK fallback** — pip-installable, works in
   any environment including this sandbox, but authenticates with
   GEMINI_API_KEY (or Vertex ADC) rather than the Google account
   subscription (PLAN.md, Findings #2 — "no consumer-subscription path").
   Useful for CI/sandboxed runs, or as an explicit opt-in when the user
   would rather spend Gemini API credit than subscription usage.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess

from typing import Any, Iterator

from .base import (
    Event,
    FILE_BLOCK_INSTRUCTIONS,
    HealthCheckResult,
    Message,
    RunResult,
    Usage,
    apply_file_blocks,
    format_messages_to_prompt,
    run_sync,
)


def _antigravity_binary() -> str | None:
    """The Antigravity CLI is `agy` on current installs; `antigravity` is the legacy name."""
    return shutil.which("agy") or shutil.which("antigravity")


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
        binary = _antigravity_binary()
        if not binary:
            return None

        try:
            proc = subprocess.run(
                [binary, "-p", "Reply with exactly one word: pong", "--output-format", "text"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return HealthCheckResult(self.name, False, "agy -p timed out")

        if proc.returncode != 0:
            return HealthCheckResult(
                self.name,
                False,
                f"agy -p exited {proc.returncode}: {proc.stderr.strip()}",
            )

        return HealthCheckResult(self.name, True, f"CLI ({Path(binary).name}): {proc.stdout.strip()[:200]}")

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
                "Antigravity CLI (`agy`) not found on PATH, and neither GEMINI_API_KEY "
                "nor Vertex AI credentials are set for the SDK fallback",
            )

        return HealthCheckResult(
            self.name,
            True,
            "SDK fallback configured (GEMINI_API_KEY/Vertex set) — "
            "connectivity not exercised yet, see PLAN.md Phase B",
        )

    def run(
        self,
        prompt: str | list[Message],
        *,
        cwd: str,
        mode: str = "read",
        tools: list[dict] | None = None,
    ) -> Iterator[Event]:
        prompt_str = format_messages_to_prompt(prompt)
        written_diffs: list[dict[str, Any]] = []
        if _antigravity_binary():
            res = self._run_cli(prompt_str, cwd=cwd, mode=mode)
        else:
            sdk_res = self._run_sdk(prompt_str, cwd=cwd, mode=mode)
            if isinstance(sdk_res, tuple):
                res, written_diffs = sdk_res
            else:
                res, written_diffs = sdk_res, []

        if res.text:
            yield Event.text_delta(res.text)
        for item in written_diffs:
            rel_path = item["path"]
            prev = item["previous"]
            curr = item["current"]
            yield Event(
                type="tool_result",
                payload={
                    "step_index": -1,
                    "tool_name": "WriteFile",
                    "args": {"path": rel_path},
                    "result": {
                        "success": True,
                        "output": f"Wrote {rel_path}",
                        "structured": {"path": rel_path, "previous": prev, "current": curr},
                    },
                    "status": "OK",
                    "execution_time_ms": 0,
                    "error": None,
                },
            )
        yield Event.usage(res.usage)
        if not res.success:
            yield Event.error(res.text)
        yield Event.done(success=res.success, text=res.text, raw=res.raw)

    def run_sync(
        self,
        prompt: str | list[Message],
        *,
        cwd: str,
        mode: str = "read",
        tools: list[dict] | None = None,
    ) -> RunResult:
        return run_sync(self.run(prompt, cwd=cwd, mode=mode, tools=tools))

    def _run_cli(self, prompt: str, *, cwd: str, mode: str) -> RunResult:
        binary = _antigravity_binary()
        if not binary:
            return RunResult(False, "Antigravity CLI (agy) not found on PATH", self._empty_usage(), {})

        # Non-interactive execution; agy lacks per-tool allowlisting, but agentflow's
        # own permission policy and role tool-gating already govern this.
        cmd = [binary, "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions"]
        if mode == "write":
            cmd += ["--mode", "accept-edits"]
        if self.model:
            cmd += ["--model", self.model]

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=900,
            )
        except subprocess.TimeoutExpired:
            return RunResult(False, "agy -p timed out", self._empty_usage(), {})

        success = proc.returncode == 0
        raw_text = proc.stdout.strip()
        usage = self._empty_usage()
        text = raw_text
        payload: dict[str, Any] = {}

        if raw_text:
            try:
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    payload = parsed
                    resp_val = payload.get("response") or payload.get("result") or payload.get("text")
                    if resp_val is not None:
                        text = resp_val if isinstance(resp_val, str) else str(resp_val)
                        text = text.strip()
                    usage = self._extract_usage(payload)
            except Exception:
                text = raw_text
                usage = self._empty_usage()

        if not text and proc.stderr:
            text = proc.stderr.strip()

        return RunResult(success, text or proc.stderr.strip(), usage, payload)

    def _run_sdk(
        self, prompt: str, *, cwd: str, mode: str
    ) -> tuple[RunResult, list[dict[str, Any]]]:
        try:
            from google.antigravity import Agent, LocalAgentConfig
        except ImportError as exc:
            return (
                RunResult(
                    False,
                    f"neither `antigravity` CLI nor google-antigravity SDK usable: {exc}",
                    self._empty_usage(),
                    {},
                ),
                [],
            )

        write = mode == "write"
        full_prompt = f"{prompt}\n\n{FILE_BLOCK_INSTRUCTIONS}" if write else prompt

        async def _call() -> str:
            config = LocalAgentConfig(model=self.model) if self.model else LocalAgentConfig()
            async with Agent(config) as agent:
                response = await agent.chat(full_prompt)
                return await response.text()

        try:
            text = asyncio.run(_call())
        except Exception as exc:  # noqa: BLE001
            return RunResult(False, f"SDK call failed: {exc}", self._empty_usage(), {}), []

        written: list[dict[str, Any]] = []
        if write:
            written = apply_file_blocks(text, cwd)

        written_paths = [w["path"] for w in written]
        summary = text if not write else f"wrote {len(written)} file(s): {', '.join(written_paths)}"
        return RunResult(True, summary, self._empty_usage(), {}), written

    def _empty_usage(self) -> Usage:
        return Usage(self.name, self.model, None, None, None)

    def _extract_usage(self, payload: dict[str, Any]) -> Usage:
        model_usage = payload.get("modelUsage") or {}
        if model_usage:
            input_tokens = sum(m.get("inputTokens", 0) or m.get("input_tokens", 0) for m in model_usage.values())
            output_tokens = sum(m.get("outputTokens", 0) or m.get("output_tokens", 0) for m in model_usage.values())
            model = payload.get("model") or self.model or next(iter(model_usage), None)
        else:
            usage = payload.get("usage") or {}
            input_tokens = usage.get("input_tokens") if usage.get("input_tokens") is not None else usage.get("inputTokens")
            output_tokens = usage.get("output_tokens") if usage.get("output_tokens") is not None else usage.get("outputTokens")
            model = payload.get("model") or usage.get("model") or self.model

        cost_val = None
        for k in ("total_cost_usd", "cost", "cost_usd"):
            if k in payload and payload[k] is not None:
                cost_val = payload[k]
                break
        if cost_val is None and isinstance(payload.get("usage"), dict):
            usage_dict = payload["usage"]
            for k in ("total_cost_usd", "cost", "cost_usd"):
                if k in usage_dict and usage_dict[k] is not None:
                    cost_val = usage_dict[k]
                    break
        cost_usd = float(cost_val) if cost_val is not None else None

        return Usage(
            backend=self.name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )
