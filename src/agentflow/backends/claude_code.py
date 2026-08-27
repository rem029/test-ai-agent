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

from .base import MODE_ALLOWED_TOOLS, MODE_PERMISSION, HealthCheckResult, RunResult, Usage


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

    def run(
        self,
        prompt: str,
        *,
        cwd: str,
        mode: str = "read",
        timeout: int = 900,
        tools: list[dict] | None = None,
    ) -> RunResult:
        """Run prompt via `claude -p`, scoped to cwd.

        mode picks tool access per PLAN.md's role semantics: "read" (review),
        "verify" (read + Bash, for running tests), "write" (build).
        """
        allowed_tools = MODE_ALLOWED_TOOLS[mode]
        permission_mode = MODE_PERMISSION[mode]
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", allowed_tools]
        if self.model:
            cmd += ["--model", self.model]
        if permission_mode:
            cmd += ["--permission-mode", permission_mode]

        try:
            proc = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                False, f"claude -p timed out after {timeout}s", self._empty_usage(), {}
            )

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return RunResult(
                False,
                f"non-JSON output (exit {proc.returncode}): "
                f"{(proc.stdout or proc.stderr)[:500]!r}",
                self._empty_usage(),
                {},
            )

        success = proc.returncode == 0 and not payload.get("is_error", False)
        return RunResult(success, payload.get("result", ""), self._extract_usage(payload), payload)

    def _empty_usage(self) -> Usage:
        return Usage(self.name, self.model, None, None, None)

    def _extract_usage(self, payload: dict) -> Usage:
        # modelUsage covers every model actually billed for this call (e.g. a
        # haiku routing pass plus the main sonnet turn); prefer it over the
        # top-level `usage` block, which only reflects the last turn.
        model_usage = payload.get("modelUsage") or {}
        if model_usage:
            input_tokens = sum(m.get("inputTokens", 0) for m in model_usage.values())
            output_tokens = sum(m.get("outputTokens", 0) for m in model_usage.values())
            model = self.model or next(iter(model_usage), None)
        else:
            usage = payload.get("usage") or {}
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            model = self.model

        return Usage(
            backend=self.name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=payload.get("total_cost_usd"),
        )
