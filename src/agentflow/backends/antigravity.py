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

import asyncio
import os
import shutil
import subprocess

from .base import FILE_BLOCK_INSTRUCTIONS, HealthCheckResult, RunResult, Usage, apply_file_blocks


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

    def run(
        self,
        prompt: str,
        *,
        cwd: str,
        mode: str = "read",
        tools: list[dict] | None = None,
    ) -> RunResult:
        if shutil.which("antigravity"):
            return self._run_cli(prompt, cwd=cwd, mode=mode)
        return self._run_sdk(prompt, cwd=cwd, mode=mode)

    def _run_cli(self, prompt: str, *, cwd: str, mode: str) -> RunResult:
        # UNVERIFIED: the real antigravity CLI's flags for scoping tool
        # access (read-only vs write) are not confirmed - see PLAN.md open
        # items. This assumes cwd alone scopes file operations, matching
        # claude -p's behavior, and does not pass a write/permission flag.
        try:
            proc = subprocess.run(
                ["antigravity", "-p", prompt],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=900,
            )
        except subprocess.TimeoutExpired:
            return RunResult(False, "antigravity -p timed out", self._empty_usage(), {})

        success = proc.returncode == 0
        text = proc.stdout.strip()
        return RunResult(success, text or proc.stderr.strip(), self._empty_usage(), {})

    def _run_sdk(self, prompt: str, *, cwd: str, mode: str) -> RunResult:
        try:
            from google.antigravity import Agent, LocalAgentConfig
        except ImportError as exc:
            return RunResult(
                False,
                f"neither `antigravity` CLI nor google-antigravity SDK usable: {exc}",
                self._empty_usage(),
                {},
            )

        # UNVERIFIED: this SDK path has not been exercised against a live
        # Gemini key (see PLAN.md, Findings #2). It uses the FILE-block
        # convention rather than a guessed built-in-tools API for writes,
        # since that part of the SDK's surface was never confirmed either.
        write = mode == "write"
        full_prompt = f"{prompt}\n\n{FILE_BLOCK_INSTRUCTIONS}" if write else prompt

        async def _call() -> str:
            config = LocalAgentConfig(model=self.model) if self.model else LocalAgentConfig()
            async with Agent(config) as agent:
                response = await agent.chat(full_prompt)
                return await response.text()

        try:
            text = asyncio.run(_call())
        except Exception as exc:  # noqa: BLE001 - surface any SDK failure as a failed run
            return RunResult(False, f"SDK call failed: {exc}", self._empty_usage(), {})

        written: list[str] = []
        if write:
            written = apply_file_blocks(text, cwd)

        summary = text if not write else f"wrote {len(written)} file(s): {', '.join(written)}"
        return RunResult(True, summary, self._empty_usage(), {})

    def _empty_usage(self) -> Usage:
        # Neither path above returns real token/cost numbers yet: the CLI's
        # output format for usage is unconfirmed, and the SDK's UsageMetadata
        # was never exercised live. See PLAN.md, "Cost & token tracking".
        return Usage(self.name, self.model, None, None, None)
