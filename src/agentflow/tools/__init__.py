"""Tool abstraction and built-in tools for agentflow."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolError, ToolResult
from .parser import ParsedToolRequest, parse_tool_requests
from .registry import ToolRegistry, get_tool, get_tool_schema, list_tools

# Import built-in tool modules so they self-register.
from . import code_analysis, file_ops, git, search, shell  # noqa: F401

__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolResult",
    "ToolRegistry",
    "ParsedToolRequest",
    "get_tool",
    "get_tool_schema",
    "list_tools",
    "parse_tool_requests",
]
