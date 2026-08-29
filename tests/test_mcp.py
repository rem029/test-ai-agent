"""Tests for MCP configuration, manager, and tool adapters."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import pytest

from agentflow.config import (
    Config,
    MCPServerConfig,
    RoleConfig,
    dump_config,
    load_config,
)
from agentflow.mcp import (
    MCPClientError,
    MCPManager,
    MCPTool,
    discover_mcp_tools,
)
from agentflow.tools.base import ToolResult

FAKE_SERVER_PATH = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")


# Module-level skip guard if mcp is unavailable
try:
    import mcp
    import mcp.server.mcpserver  # noqa: F401
    HAS_MCP_SERVER = True
except Exception:  # pragma: no cover
    HAS_MCP_SERVER = False

pytestmark = pytest.mark.skipif(not HAS_MCP_SERVER, reason="mcp server SDK not available")


def test_mcp_server_config_validation():
    # Valid stdio command server
    s1 = MCPServerConfig(
        name="my-server_1",
        command="python",
        args=["fake.py"],
        env={"FOO": "BAR"},
        auto_approve=["echo"],
    )
    assert s1.name == "my-server_1"
    assert s1.command == "python"
    assert s1.args == ["fake.py"]
    assert s1.env == {"FOO": "BAR"}
    assert s1.enabled is True
    assert s1.auto_approve == ["echo"]

    # Valid url server
    s2 = MCPServerConfig(name="remote-server", url="http://localhost:8000/sse")
    assert s2.url == "http://localhost:8000/sse"

    # Invalid: both command and url
    with pytest.raises(ValueError, match="exactly one of 'command' or 'url'"):
        MCPServerConfig(name="bad", command="python", url="http://localhost:8000")

    # Invalid: neither command nor url
    with pytest.raises(ValueError, match="exactly one of 'command' or 'url'"):
        MCPServerConfig(name="bad")

    # Invalid: empty name
    with pytest.raises(ValueError, match="name must be non-empty"):
        MCPServerConfig(name="", command="python")

    # Invalid: characters outside ^[A-Za-z0-9_-]+$
    with pytest.raises(ValueError, match="match"):
        MCPServerConfig(name="invalid.name!", command="python")

    with pytest.raises(ValueError, match="match"):
        MCPServerConfig(name="invalid name", command="python")


def test_mcp_config_roundtrip(tmp_path):
    config_file = tmp_path / "agentflow.config.yaml"
    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        mcp_servers=[
            MCPServerConfig(
                name="fake",
                command=sys.executable,
                args=[FAKE_SERVER_PATH],
                env={"TEST_VAR": "1"},
                auto_approve=["echo"],
            ),
            MCPServerConfig(
                name="remote",
                url="http://localhost:9000",
                enabled=False,
            ),
        ],
    )

    dump_config(cfg, str(config_file))
    loaded = load_config(str(config_file))

    assert len(loaded.mcp_servers) == 2
    s1, s2 = loaded.mcp_servers
    assert s1.name == "fake"
    assert s1.command == sys.executable
    assert s1.args == [FAKE_SERVER_PATH]
    assert s1.env == {"TEST_VAR": "1"}
    assert s1.auto_approve == ["echo"]
    assert s1.enabled is True

    assert s2.name == "remote"
    assert s2.url == "http://localhost:9000"
    assert s2.enabled is False


def test_mcp_disabled_env_override(tmp_path, monkeypatch):
    config_file = tmp_path / "agentflow.config.yaml"
    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        mcp_servers=[
            MCPServerConfig(
                name="fake",
                command=sys.executable,
                args=[FAKE_SERVER_PATH],
                enabled=True,
            ),
        ],
    )
    dump_config(cfg, str(config_file))

    monkeypatch.setenv("AGENTFLOW_MCP_DISABLED", "1")
    loaded = load_config(str(config_file))
    assert len(loaded.mcp_servers) == 1
    assert loaded.mcp_servers[0].enabled is False

    monkeypatch.setenv("AGENTFLOW_MCP_DISABLED", "true")
    loaded = load_config(str(config_file))
    assert loaded.mcp_servers[0].enabled is False


def test_mcp_manager_lifecycle_and_tools():
    server_cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
    )

    manager = MCPManager([server_cfg], cwd=".")
    try:
        manager.start()
        assert not manager.errors

        tools = manager.list_tools()
        assert len(tools) == 2
        tool_names = {t.name for t in tools}
        assert tool_names == {"mcp__fake__echo", "mcp__fake__add"}

        # Check schema
        discovered = discover_mcp_tools(manager)
        assert "mcp__fake__echo" in discovered
        echo_tool = discovered["mcp__fake__echo"]
        assert echo_tool.server_name == "fake"
        assert echo_tool.remote_name == "echo"
        schema = echo_tool.schema()
        assert schema["name"] == "mcp__fake__echo"
        assert "parameters" in schema
        assert "properties" in schema["parameters"]

        # Call tool directly via manager
        outcome_echo = manager.call_tool("fake", "echo", {"text": "hi"})
        assert outcome_echo.ok is True
        assert "hi" in outcome_echo.text
        assert outcome_echo.error is None

        outcome_add = manager.call_tool("fake", "add", {"a": 10, "b": 25})
        assert outcome_add.ok is True
        assert "35" in outcome_echo.text or "35" in outcome_add.text
        assert outcome_add.structured == {"result": 35}

        # Execute tool via Tool.run() interface
        result = echo_tool.run({"text": "hi from run"})
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert "hi from run" in result.output
        assert result.error is None

        add_tool = discovered["mcp__fake__add"]
        result_add = add_tool.run({"a": 4, "b": 6})
        assert result_add.success is True
        assert result_add.structured == {"result": 10}

    finally:
        manager.close()


def test_mcp_manager_context_manager():
    server_cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
    )

    with MCPManager([server_cfg], cwd=".") as manager:
        tools = manager.list_tools()
        assert len(tools) == 2
        outcome = manager.call_tool("fake", "echo", {"text": "context mgr"})
        assert outcome.ok is True
        assert "context mgr" in outcome.text


def test_mcp_manager_unknown_server():
    server_cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
    )

    with MCPManager([server_cfg], cwd=".") as manager:
        with pytest.raises(MCPClientError, match="Unknown or uninitialized MCP server"):
            manager.call_tool("unknown_server", "echo", {"text": "hello"})


def test_mcp_manager_nonexistent_binary_and_url_server():
    bad_server = MCPServerConfig(
        name="bad_binary",
        command="nonexistent_binary_xyz_12345",
        args=["--arg"],
    )
    url_server = MCPServerConfig(
        name="url_server",
        url="http://localhost:9999",
    )
    good_server = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
    )
    disabled_server = MCPServerConfig(
        name="disabled_server",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
        enabled=False,
    )

    manager = MCPManager([bad_server, url_server, good_server, disabled_server], cwd=".")
    try:
        # start() should NOT raise despite bad servers
        manager.start()

        # Check errors recorded
        errors = manager.errors
        assert "bad_binary" in errors
        assert "url_server" in errors
        assert errors["url_server"] == "HTTP/SSE transport not yet supported"
        assert "disabled_server" not in errors

        # Good server should still function properly
        tools = manager.list_tools()
        assert len(tools) == 2
        assert {t.name for t in tools} == {"mcp__fake__echo", "mcp__fake__add"}

        outcome = manager.call_tool("fake", "echo", {"text": "survived"})
        assert outcome.ok is True
        assert "survived" in outcome.text
    finally:
        manager.close()


def test_mcp_manager_close_idempotent():
    server_cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
    )

    manager = MCPManager([server_cfg], cwd=".")
    manager.start()
    manager.close()
    # Calling close again should not raise or error
    manager.close()
