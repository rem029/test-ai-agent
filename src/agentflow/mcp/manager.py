"""MCP manager running background event loop thread for stdio MCP clients."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
import os
import threading
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import mcp.types as types

from agentflow.config import MCPServerConfig
from agentflow.mcp.tool import MCPTool


class MCPClientError(Exception):
    """Raised for programmer errors in MCP client usage (e.g. unknown server)."""


@dataclass
class MCPCallOutcome:
    """Outcome of an MCP tool call."""

    ok: bool
    text: str = ""
    structured: dict[str, Any] | None = None
    error: str | None = None


def _flatten_content(content: list[Any]) -> str:
    """Flatten MCP content blocks into a single string."""
    parts: list[str] = []
    for block in content:
        if isinstance(block, types.TextContent):
            parts.append(block.text)
        elif hasattr(block, "text") and isinstance(block.text, str):
            parts.append(block.text)
        else:
            parts.append(repr(block))
    return "\n".join(parts)


class MCPManager:
    """Manages MCP server connections and lifecycle over a background asyncio loop."""

    def __init__(
        self,
        servers: list[MCPServerConfig],
        *,
        cwd: str,
        startup_timeout: float = 30.0,
    ) -> None:
        self.servers = servers
        self.cwd = cwd
        self.startup_timeout = startup_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, list[types.Tool]] = {}
        self._errors: dict[str, str] = {}
        self._started = False
        self._closed = False

    @property
    def errors(self) -> dict[str, str]:
        """Mapping of server names to startup error messages."""
        return dict(self._errors)

    def start(self) -> None:
        """Start the background loop and connect to all enabled MCP servers."""
        if self._started:
            return
        self._started = True

        ready_event = threading.Event()

        def _loop_thread_target() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready_event.set()
            try:
                self._loop.run_forever()
            finally:
                try:
                    pending = asyncio.all_tasks(self._loop)
                    for task in pending:
                        task.cancel()
                except Exception:
                    pass
                self._loop.close()

        self._thread = threading.Thread(
            target=_loop_thread_target,
            name="agentflow-mcp-loop",
            daemon=True,
        )
        self._thread.start()
        ready_event.wait()

        if self._loop is None:
            return

        try:
            future = asyncio.run_coroutine_threadsafe(self._startup(), self._loop)
            future.result(timeout=self.startup_timeout)
        except TimeoutError:
            self._errors["_manager"] = f"Global startup timed out after {self.startup_timeout}s"
        except Exception as exc:
            self._errors["_manager"] = f"Startup failed: {exc}"

    async def _startup(self) -> None:
        self._exit_stack = AsyncExitStack()
        for server in self.servers:
            if not server.enabled:
                continue
            if server.url is not None and not server.command:
                self._errors[server.name] = "HTTP/SSE transport not yet supported"
                continue
            if not server.command:
                self._errors[server.name] = "No command or url configured"
                continue

            try:
                await asyncio.wait_for(
                    self._start_server(server),
                    timeout=self.startup_timeout,
                )
            except asyncio.TimeoutError:
                self._errors[server.name] = f"Startup timed out after {self.startup_timeout}s"
            except Exception as exc:
                self._errors[server.name] = f"Failed to start MCP server {server.name}: {exc}"

    async def _start_server(self, server: MCPServerConfig) -> None:
        assert self._exit_stack is not None
        assert server.command is not None
        merged_env = {**os.environ, **server.env}
        params = StdioServerParameters(
            command=server.command,
            args=list(server.args) if server.args else [],
            env=merged_env,
            cwd=self.cwd,
        )
        server_stack = AsyncExitStack()
        try:
            read, write = await server_stack.enter_async_context(stdio_client(params))
            session = await server_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools_result = await session.list_tools()
            self._sessions[server.name] = session
            self._tools[server.name] = list(tools_result.tools)
            self._exit_stack.push_async_callback(server_stack.aclose)
        except Exception:
            await server_stack.aclose()
            raise

    def list_tools(self) -> list[MCPTool]:
        """Return list of MCPTool adapters for all available tools across started servers."""
        tools: list[MCPTool] = []
        for server_name, server_tools in self._tools.items():
            for t in server_tools:
                tools.append(
                    MCPTool(
                        manager=self,
                        server_name=server_name,
                        remote_name=t.name,
                        description=t.description or "",
                        input_schema=t.input_schema if isinstance(t.input_schema, dict) else {},
                    )
                )
        return tools

    def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 120.0,
    ) -> MCPCallOutcome:
        """Call a remote MCP tool and return its outcome."""
        if server not in self._sessions or self._loop is None or not self._loop.is_running():
            raise MCPClientError(f"Unknown or uninitialized MCP server: {server}")

        session = self._sessions[server]

        async def _do_call() -> types.CallToolResult:
            return await asyncio.wait_for(
                session.call_tool(tool, arguments=arguments),
                timeout=timeout,
            )

        try:
            future = asyncio.run_coroutine_threadsafe(_do_call(), self._loop)
            result = future.result(timeout=timeout + 5.0)
        except asyncio.TimeoutError:
            return MCPCallOutcome(
                ok=False,
                text="",
                error=f"Tool call {tool} on {server} timed out after {timeout}s",
            )
        except TimeoutError:
            return MCPCallOutcome(
                ok=False,
                text="",
                error=f"Tool call {tool} on {server} timed out after {timeout}s",
            )
        except Exception as exc:
            return MCPCallOutcome(
                ok=False,
                text="",
                error=f"Tool call {tool} on {server} failed: {exc}",
            )

        text = _flatten_content(result.content)
        structured = result.structured_content
        if result.is_error:
            return MCPCallOutcome(
                ok=False,
                text=text,
                structured=structured,
                error=text or f"Tool call {tool} reported an error",
            )
        return MCPCallOutcome(
            ok=True,
            text=text,
            structured=structured,
            error=None,
        )

    def close(self) -> None:
        """Cleanly close all MCP connections and stop the background loop."""
        if self._closed:
            return
        self._closed = True

        if self._loop is not None and self._loop.is_running():
            if self._exit_stack is not None:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._exit_stack.aclose(),
                        self._loop,
                    )
                    future.result(timeout=5.0)
                except Exception:
                    pass
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        self._sessions.clear()
        self._tools.clear()

    def __enter__(self) -> MCPManager:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
