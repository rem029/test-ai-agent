"""Fake stdio MCP server for agentflow test fixtures."""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake")


@server.tool(name="echo", description="Echo the input text")
def echo(text: str) -> str:
    return text


@server.tool(name="add", description="Add two integers")
def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
