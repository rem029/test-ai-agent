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


def test_toolset_unit():
    server_cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
        auto_approve=["echo"],
    )
    from unittest.mock import MagicMock
    from agentflow.orchestrator import Toolset

    fake_tool = MCPTool(
        manager=MagicMock(),
        server_name="fake",
        remote_name="echo",
        description="Echo the input text",
        input_schema={"type": "object", "properties": {"text": {"type": "string", "description": "text to echo"}}},
    )
    ts = Toolset(mcp_tools={"mcp__fake__echo": fake_tool}, mcp_servers=[server_cfg])

    # has / get / names
    assert ts.has("ReadFile") is True
    assert ts.has("mcp__fake__echo") is True
    assert ts.has("nonexistent_xyz") is False
    assert ts.get("mcp__fake__echo") == fake_tool
    assert ts.get("ReadFile") is not None
    assert "ReadFile" in ts.names()
    assert "mcp__fake__echo" in ts.names()

    # schema_text
    st = ts.schema_text()
    assert "- ReadFile:" in st
    assert "- mcp__fake__echo: Echo the input text" in st
    assert "• text: text to echo" in st

    # is_read_only
    assert ts.is_read_only("ReadFile") is True
    assert ts.is_read_only("WriteFile") is False
    assert ts.is_read_only("mcp__fake__echo") is False

    # is_mcp
    assert ts.is_mcp("mcp__fake__echo") is True
    assert ts.is_mcp("ReadFile") is False

    # mcp_auto_approved
    assert ts.mcp_auto_approved("mcp__fake__echo") is True
    assert ts.mcp_auto_approved("mcp__fake__add") is False

    # auto_approve=["all"]
    server_all = MCPServerConfig(name="fake", command=sys.executable, auto_approve=["all"])
    ts_all = Toolset(mcp_tools={"mcp__fake__echo": fake_tool}, mcp_servers=[server_all])
    assert ts_all.mcp_auto_approved("mcp__fake__echo") is True

    # capability_hints
    assert ts.capability_hints() == ""
    assert Toolset().capability_hints() == ""


def test_toolset_capability_hints_playwright():
    from unittest.mock import MagicMock
    from agentflow.orchestrator import Toolset

    playwright_tool = MCPTool(
        manager=MagicMock(),
        server_name="playwright",
        remote_name="browser_navigate",
        description="Navigate to URL",
        input_schema={},
    )
    assert Toolset().capability_hints() == ""
    assert Toolset(mcp_tools={"mcp__fake__echo": MagicMock()}).capability_hints() == ""

    ts_playwright = Toolset(
        mcp_tools={"mcp__playwright__browser_navigate": playwright_tool}
    )
    hints = ts_playwright.capability_hints()
    assert "Browser automation tools are available" in hints
    assert "Environment capabilities:" in hints


def test_check_tool_permission_with_toolset():
    from unittest.mock import MagicMock, patch
    from agentflow.orchestrator import Toolset, _check_tool_permission

    server_cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
        auto_approve=["echo"],
    )
    echo_tool = MCPTool(
        manager=MagicMock(),
        server_name="fake",
        remote_name="echo",
        description="Echo the input text",
        input_schema={},
    )
    add_tool = MCPTool(
        manager=MagicMock(),
        server_name="fake",
        remote_name="add",
        description="Add two integers",
        input_schema={},
    )
    ts = Toolset(
        mcp_tools={"mcp__fake__echo": echo_tool, "mcp__fake__add": add_tool},
        mcp_servers=[server_cfg],
    )

    # 1. Unapproved MCP tool under "auto" policy + no handler (non-interactive in tests) -> denied with reason mentioning approval
    with patch("sys.stdin.isatty", return_value=False):
        allowed, reason = _check_tool_permission(
            "mcp__fake__add", {"a": 1, "b": 2}, "auto", toolset=ts
        )
        assert allowed is False
        assert reason is not None
        assert "needs approval" in reason or "approval" in reason

    # 2. Auto-approved MCP tool under "auto" policy -> allowed
    allowed, reason = _check_tool_permission(
        "mcp__fake__echo", {"text": "hello"}, "auto", toolset=ts
    )
    assert allowed is True
    assert reason is None

    # 3. Unapproved MCP tool with permission_handler returning "allow" -> allowed
    allowed, reason = _check_tool_permission(
        "mcp__fake__add",
        {"a": 1, "b": 2},
        "auto",
        permission_handler=lambda tool, args: "allow",
        toolset=ts,
    )
    assert allowed is True
    assert reason is None

    # 4. Unapproved MCP tool with permission_handler returning "deny" -> denied
    allowed, reason = _check_tool_permission(
        "mcp__fake__add",
        {"a": 1, "b": 2},
        "auto",
        permission_handler=lambda tool, args: "deny",
        toolset=ts,
    )
    assert allowed is False
    assert "Permission denied by user" in reason


def test_mcp_tool_execution_in_run_with_tools(tmp_path):
    from unittest.mock import MagicMock
    from agentflow.backends.base import RunResult, Usage
    from agentflow.orchestrator import RunState, Toolset, _run_with_tools

    server_cfg = MCPServerConfig(
        name="fake",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
        auto_approve=["echo"],
    )
    db_path = tmp_path / "test.db"
    run_id = "test-mcp-e2e"
    state = RunState(
        run_id=run_id,
        goal="Test MCP execution",
        started_at=1000.0,
        config={},
    )

    with MCPManager([server_cfg], cwd=str(tmp_path)) as manager:
        mcp_tools = discover_mcp_tools(manager)
        toolset = Toolset(mcp_tools, [server_cfg])

        backend = MagicMock()
        backend.name = "mock"
        backend.model = "mock-model"
        backend.run.side_effect = [
            RunResult(
                success=True,
                text='<tool_call>{"name": "mcp__fake__echo", "args": {"text": "hello mcp"}}</tool_call>',
                usage=Usage("mock", "mock-model", 10, 10, 0.0),
                raw={},
            ),
            RunResult(
                success=True,
                text="The echo tool replied successfully.",
                usage=Usage("mock", "mock-model", 10, 10, 0.0),
                raw={},
            ),
        ]

        result = _run_with_tools(
            backend,
            "Please echo 'hello mcp'",
            cwd=str(tmp_path),
            mode="write",
            state=state,
            step_index=1,
            toolset=toolset,
            database_path=db_path,
        )

        assert result.success is True
        assert "echo tool replied successfully" in result.text
        assert len(state.tool_calls) == 1
        call = state.tool_calls[0]
        assert call["tool_name"] == "mcp__fake__echo"
        assert call["args"] == {"text": "hello mcp"}
        assert call["status"] == "success"
        assert "hello mcp" in call["result"]["output"]

        # Check conversation history received the tool result
        second_call_messages = backend.run.call_args_list[1][0][0]
        user_reply = [m for m in second_call_messages if m.role == "user"][-1]
        assert "hello mcp" in user_reply.content


def test_run_with_tools_includes_playwright_capability_hints(tmp_path):
    from unittest.mock import MagicMock
    from agentflow.backends.base import RunResult, Usage
    from agentflow.orchestrator import RunState, Toolset, _run_with_tools

    server_cfg = MCPServerConfig(
        name="playwright",
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
    )
    playwright_tool = MCPTool(
        manager=MagicMock(),
        server_name="playwright",
        remote_name="browser_navigate",
        description="Navigate to URL",
        input_schema={},
    )
    toolset = Toolset(
        {"mcp__playwright__browser_navigate": playwright_tool}, [server_cfg]
    )

    backend = MagicMock()
    backend.name = "mock"
    backend.model = "mock-model"
    backend.run.return_value = RunResult(
        success=True,
        text="All done.",
        usage=Usage("mock", "mock-model", 10, 10, 0.0),
        raw={},
    )
    state = RunState(
        run_id="test-hints",
        goal="Test hints",
        started_at=1000.0,
        config={},
    )
    _run_with_tools(
        backend,
        "Verify web UI",
        cwd=str(tmp_path),
        mode="verify",
        state=state,
        step_index=1,
        toolset=toolset,
    )
    first_call_messages = backend.run.call_args_list[0][0][0]
    initial_prompt = first_call_messages[0].content
    assert "Browser automation tools are available (mcp__playwright__*)" in initial_prompt
    assert "Environment capabilities:" in initial_prompt


def test_mcp_workflow_lifecycle(tmp_path):
    from unittest.mock import MagicMock, patch
    from agentflow.backends.base import RunResult, Usage
    from agentflow.orchestrator import run_workflow

    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        mcp_servers=[
            MCPServerConfig(
                name="fake",
                command=sys.executable,
                args=[FAKE_SERVER_PATH],
                auto_approve=["echo"],
            )
        ],
    )

    review_text = '<tool_call>{"name": "mcp__fake__echo", "args": {"text": "review echo"}}</tool_call>'
    review_final = "Plan: done"
    build_text = "Built"
    verify_text = "VERIFY_RESULT: PASS"

    backend = MagicMock()
    backend.name = "mock"
    backend.model = "m"
    backend.run.side_effect = [
        RunResult(success=True, text=review_text, usage=Usage("mock", "m", 1, 1, 0.0), raw={}),
        RunResult(success=True, text=review_final, usage=Usage("mock", "m", 1, 1, 0.0), raw={}),
        RunResult(success=True, text=build_text, usage=Usage("mock", "m", 1, 1, 0.0), raw={}),
        RunResult(success=True, text=verify_text, usage=Usage("mock", "m", 1, 1, 0.0), raw={}),
    ]

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends, \
         patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        state = run_workflow(
            "MCP workflow goal",
            config,
            str(repo),
            database_path=db_path,
        )

    assert state.finished_at is not None
    assert any(c["tool_name"] == "mcp__fake__echo" for c in state.tool_calls)
    from agentflow.database import list_events
    events = list_events(state.run_id, path=db_path)
    ready_events = [e for e in events if e["type"] == "mcp_ready"]
    assert len(ready_events) == 1
    assert ready_events[0]["payload"]["servers"] == ["fake"]


def test_cli_mcp_check_and_list_tools(tmp_path, capsys):
    from agentflow.cli import main as cli_main

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
            )
        ],
    )
    dump_config(cfg, str(config_file))

    # 1. --mcp-check with working server
    ret = cli_main(["--config", str(config_file), "--mcp-check"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "[OK] fake: 2 tool(s)" in out
    assert "echo" in out
    assert "add" in out

    # 2. --list-tools with MCP servers
    ret = cli_main(["--config", str(config_file), "--list-tools"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "=== Available Tools ===" in out
    assert "=== MCP Tools ===" in out
    assert "mcp__fake__echo" in out
    assert "mcp__fake__add" in out

    # 3. --mcp-check with failing server
    bad_config_file = tmp_path / "bad.yaml"
    bad_cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        mcp_servers=[
            MCPServerConfig(
                name="bad_binary",
                command="nonexistent_binary_xyz_99999",
                args=[],
            )
        ],
    )
    dump_config(bad_cfg, str(bad_config_file))
    ret_bad = cli_main(["--config", str(bad_config_file), "--mcp-check"])
    assert ret_bad == 1
    out_bad = capsys.readouterr().out
    assert "[ERROR] bad_binary:" in out_bad

    # 4. --mcp-check with no servers
    no_mcp_config = tmp_path / "none.yaml"
    dump_config(
        Config(
            review=RoleConfig(backend="claude-code"),
            build=RoleConfig(backend="antigravity"),
            verify=RoleConfig(backend="claude-code"),
        ),
        str(no_mcp_config),
    )
    ret_none = cli_main(["--config", str(no_mcp_config), "--mcp-check"])
    assert ret_none == 0
    out_none = capsys.readouterr().out
    assert "No MCP servers configured." in out_none


def test_tui_tools_command_with_mcp():
    from agentflow.tui.commands import dispatch as tui_dispatch

    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        mcp_servers=[
            MCPServerConfig(
                name="fake",
                command=sys.executable,
                args=[FAKE_SERVER_PATH],
            )
        ],
    )
    res = tui_dispatch("/tools", [], cfg, cwd=".", session_id="s1")
    assert "Available Tools:" in res.output
    assert "(+ MCP tools from 1 configured server(s) - see 'agentflow --mcp-check')" in res.output

