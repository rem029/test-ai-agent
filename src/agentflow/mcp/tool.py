"""Adapter for MCP tools into agentflow Tool interface."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from agentflow.tools.base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:
    from agentflow.mcp.manager import MCPManager


class _PassthroughParams(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPTool(Tool):
    """Adapts a remote MCP tool to agentflow's Tool ABC."""

    param_model: type[BaseModel] = _PassthroughParams

    def __init__(
        self,
        manager: MCPManager,
        server_name: str,
        remote_name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> None:
        self.name = f"mcp__{server_name}__{remote_name}"
        self.description = description or f"MCP tool {remote_name} from server {server_name}"
        self.server_name = server_name
        self.remote_name = remote_name
        self.input_schema = input_schema
        self._manager = manager
        self.param_model = _PassthroughParams

    def execute(self, context: ToolContext, **params: Any) -> ToolResult:
        outcome = self._manager.call_tool(self.server_name, self.remote_name, params)
        return ToolResult(
            success=outcome.ok,
            output=outcome.text or "",
            error=outcome.error,
            structured=outcome.structured,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.input_schema,
        }


def discover_mcp_tools(manager: MCPManager) -> dict[str, MCPTool]:
    """Discover all tools from started MCP servers as a {name: tool} mapping."""
    return {tool.name: tool for tool in manager.list_tools()}
