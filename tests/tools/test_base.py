"""Tests for the tool abstraction layer."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from agentflow.tools import Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from agentflow.tools.registry import _REGISTRY, register_tool


@pytest.fixture(autouse=True)
def reset_global_registry():
    """Ensure the global registry is clean before tests that touch it."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


class AddParams(BaseModel):
    a: int
    b: int


class AddTool(Tool):
    name = "add"
    description = "Add two integers."
    param_model = AddParams

    def execute(self, context: ToolContext, a: int, b: int) -> ToolResult:
        return ToolResult(success=True, output=str(a + b))


class FailParams(BaseModel):
    message: str = Field(default="boom")


class FailTool(Tool):
    name = "fail"
    description = "Always fails."
    param_model = FailParams

    def execute(self, context: ToolContext, message: str) -> ToolResult:
        raise ToolError(message)


def test_tool_run_valid_params():
    tool = AddTool()
    result = tool.run({"a": 2, "b": 3})
    assert result.success is True
    assert result.output == "5"
    assert result.error is None
    assert result.duration_ms >= 0


def test_tool_run_invalid_params():
    tool = AddTool()
    result = tool.run({"a": "not-an-int", "b": 3})
    assert result.success is False
    assert "Invalid parameters" in result.error
    assert "add" in result.error


def test_tool_run_raises_tool_error():
    tool = FailTool()
    result = tool.run({"message": "expected failure"})
    assert result.success is False
    assert "expected failure" in result.error


def test_tool_schema_contains_name_description_and_parameters():
    tool = AddTool()
    schema = tool.schema()
    assert schema["name"] == "add"
    assert "Add two integers" in schema["description"]
    assert "properties" in schema["parameters"]
    assert set(schema["parameters"]["properties"].keys()) == {"a", "b"}


def test_tool_result_truncation():
    result = ToolResult(success=True, output="x" * 5000)
    data = result.model_dump_truncated(max_length=100)
    assert len(data["output"]) < 5000
    assert "... (truncated)" in data["output"]


def test_registry_register_and_get():
    registry = ToolRegistry()
    tool = AddTool()
    registry.register(tool)
    assert registry.get("add") is tool


def test_registry_unknown_tool_raises():
    registry = ToolRegistry()
    with pytest.raises(ToolError, match="Unknown tool"):
        registry.get("missing")


def test_registry_duplicate_registration_raises():
    registry = ToolRegistry()
    registry.register(AddTool())
    with pytest.raises(ToolError, match="already registered"):
        registry.register(AddTool())


def test_registry_list_and_schemas():
    registry = ToolRegistry()
    registry.register(AddTool())
    assert registry.list_tools() == ["add"]
    schemas = registry.schemas()
    assert len(schemas) == 1
    assert schemas[0]["name"] == "add"


def test_register_tool_global():
    tool = register_tool(AddTool())
    assert tool.name == "add"


def test_tool_run_many_invalid_params():
    class MultiParams(BaseModel):
        f1: int
        f2: int
        f3: int
        f4: int
        f5: int

    class MultiTool(Tool):
        name = "multi"
        description = "Tool with many fields"
        param_model = MultiParams

        def execute(self, context: ToolContext, **params) -> ToolResult:
            return ToolResult(success=True)

    tool = MultiTool()
    result = tool.run({})
    assert result.success is False
    assert "(+2 more)" in result.error
    assert "Accepted: f1 (required), f2 (required), f3 (required), f4 (required), f5 (required)." in result.error

