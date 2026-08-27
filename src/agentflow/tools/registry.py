"""Tool registry for discovering, registering, and retrieving tools."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError, ToolResult


class ToolRegistry:
    """Central registry of available tools.

    The registry is intentionally module-level and populated at import time.
    New tools register themselves via :func:`register_tool`.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        """Register a tool instance."""
        if not tool.name:
            raise ToolError("Tool name must not be empty")
        if tool.name in self._tools:
            raise ToolError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        """Retrieve a tool by name."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"Unknown tool: {name!r}") from exc

    def list_tools(self) -> list[str]:
        """Return sorted list of registered tool names."""
        return sorted(self._tools.keys())

    def schemas(self) -> list[dict[str, Any]]:
        """Return JSON schemas for all registered tools."""
        return [tool.schema() for _, tool in sorted(self._tools.items())]

    def run(
        self, name: str, params: dict[str, Any], *, context: ToolContext | None = None
    ) -> ToolResult:
        """Validate and execute a tool by name."""
        return self.get(name).run(params, context=context)

    def clear(self) -> None:
        """Remove all registered tools. Intended for tests only."""
        self._tools.clear()


# Module-level singleton used by the orchestrator and CLI.
_REGISTRY = ToolRegistry()


def register_tool(tool: Tool) -> Tool:
    """Register a tool in the global registry."""
    return _REGISTRY.register(tool)


def get_tool(name: str) -> Tool:
    """Get a tool from the global registry."""
    return _REGISTRY.get(name)


def get_tool_schema(name: str) -> dict[str, Any]:
    """Get the JSON schema for a single tool."""
    return _REGISTRY.get(name).schema()


def list_tools() -> list[str]:
    """Return sorted list of registered tool names."""
    return _REGISTRY.list_tools()
