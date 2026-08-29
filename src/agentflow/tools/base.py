"""Tool abstraction for agentflow.

Tools are deterministic, auditable operations the orchestrator can execute on
behalf of an agent. Each tool declares its name, description, input schema,
and an ``execute`` method. The orchestrator validates inputs against the
schema before execution and records the result in the run state.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


@dataclass
class ToolContext:
    """Execution context passed to every tool invocation."""

    cwd: str


class ToolError(Exception):
    """Raised when a tool cannot execute or receives invalid input."""

    pass


class ToolResult(BaseModel):
    """Normalized result returned by every tool execution."""

    success: bool
    output: str = ""
    error: str | None = None
    duration_ms: int = 0
    structured: dict[str, Any] | None = None

    def model_dump_truncated(self, max_length: int = 2000) -> dict[str, Any]:
        """Return a dict safe for persistence; long output is truncated."""
        data = self.model_dump()
        if len(data.get("output", "")) > max_length:
            data["output"] = data["output"][:max_length] + "\n... (truncated)"
        if data.get("error") and len(data["error"]) > max_length:
            data["error"] = data["error"][:max_length] + "\n... (truncated)"
        return data


def _format_validation_error(
    tool_name: str, param_model: type[BaseModel], exc: ValidationError
) -> str:
    """Format a pydantic ValidationError into a concise, model-actionable message."""
    errors = exc.errors()
    error_clauses: list[str] = []
    for err in errors[:3]:
        loc = err.get("loc", ())
        loc_name = ".".join(str(x) for x in loc) if loc else ""
        err_type = err.get("type", "")
        msg = err.get("msg", "")
        if err_type == "missing" and loc_name:
            error_clauses.append(f"missing required argument '{loc_name}'")
        elif loc_name:
            error_clauses.append(f"'{loc_name}' - {msg}")
        elif msg:
            error_clauses.append(msg)

    if len(errors) > 3:
        error_clauses.append(f"(+{len(errors) - 3} more)")

    clauses_text = ", ".join(error_clauses) if error_clauses else str(exc)

    accepted_items: list[str] = []
    if hasattr(param_model, "model_fields"):
        for fname, field in param_model.model_fields.items():
            if field.is_required():
                accepted_items.append(f"{fname} (required)")
            else:
                accepted_items.append(fname)
    accepted_text = f"Accepted: {', '.join(accepted_items)}." if accepted_items else ""

    provided_text = ""
    if errors:
        first_input = errors[0].get("input")
        if isinstance(first_input, dict) and first_input:
            keys = list(first_input.keys())
            if len(keys) > 10:
                k_str = ", ".join(str(k) for k in keys[:10]) + f" (+{len(keys) - 10} more)"
            else:
                k_str = ", ".join(str(k) for k in keys)
            provided_text = f"You provided: {k_str}."

    lead = f"Invalid parameters for {tool_name}: {clauses_text}"
    if not lead.endswith("."):
        lead += "."
    parts = [lead]
    if accepted_text:
        parts.append(accepted_text)
    if provided_text:
        parts.append(provided_text)

    return " ".join(parts)


class Tool(ABC):
    """Base class for all agentflow tools.

    Subclasses must define ``name``, ``description``, and ``param_model``.
    ``execute`` receives validated parameters as keyword arguments and must
    return a ``ToolResult``.
    """

    name: str
    description: str
    param_model: type[BaseModel]

    @abstractmethod
    def execute(self, context: ToolContext, **params: Any) -> ToolResult:
        """Run the tool with validated parameters and execution context."""
        ...

    def run(
        self, params: dict[str, Any], *, context: ToolContext | None = None
    ) -> ToolResult:
        """Validate ``params`` against ``param_model`` and execute the tool.

        This is the entrypoint used by the orchestrator. It catches
        validation errors and unexpected exceptions, converting them into
        deterministic ``ToolResult`` failures.
        """
        ctx = context or ToolContext(cwd=".")
        start = time.perf_counter_ns()
        try:
            validated = self.param_model.model_validate(params)
        except ValidationError as exc:
            return ToolResult(
                success=False,
                error=_format_validation_error(self.name, self.param_model, exc),
            )

        try:
            result = self.execute(ctx, **validated.model_dump())
        except ToolError as exc:
            result = ToolResult(success=False, error=str(exc))
        except Exception as exc:  # pragma: no cover - catch-all safety net
            result = ToolResult(success=False, error=f"Tool {self.name} failed: {exc}")

        elapsed_ms = (time.perf_counter_ns() - start) // 1_000_000
        if result.duration_ms == 0:
            result.duration_ms = elapsed_ms
        return result

    def schema(self) -> dict[str, Any]:
        """Return the JSON Schema for this tool's parameters."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.param_model.model_json_schema(),
        }
