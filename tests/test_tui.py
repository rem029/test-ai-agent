"""Comprehensive unit and integration tests for Terminal UI (Phase J)."""

from __future__ import annotations

import contextlib
import io
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.database import append_event, create_session, save_run
from agentflow.orchestrator import RunState, _check_tool_permission
from agentflow.tui.commands import CommandResult, dispatch, parse_command
from agentflow.tui.permissions import PermissionRequest, SessionPermissionBroker
from agentflow.tui.render import (
    format_diff,
    format_event,
    format_footer,
    session_cost,
    truncate_output,
)
from agentflow.tui.repl import (
    _handle_mid_run_input,
    run_repl,
)


# ============================================================================
# 1. render.py Unit Tests
# ============================================================================


def test_truncate_output():
    assert truncate_output("") == ""
    assert truncate_output("line 1\nline 2", max_lines=5) == "line 1\nline 2"

    lines = [f"line {i}" for i in range(10)]
    truncated = truncate_output("\n".join(lines), max_lines=4)
    assert "line 0" in truncated
    assert "line 3" in truncated
    assert "… (6 more lines)" in truncated


def test_format_diff_basic():
    structured = {
        "path": "test.py",
        "previous": "def foo():\n    return 1\n",
        "current": "def foo():\n    return 2\n",
    }
    diff = format_diff(structured)
    assert "a/test.py" in diff
    assert "b/test.py" in diff
    assert "[red]" in diff or "-    return 1" in diff
    assert "[green]" in diff or "+    return 2" in diff


def test_format_diff_no_changes():
    structured = {
        "path": "test.py",
        "previous": "same content\n",
        "current": "same content\n",
    }
    diff = format_diff(structured)
    assert "(no diff)" in diff


def test_format_diff_max_lines_cap():
    prev = "\n".join(f"old line {i}" for i in range(100))
    curr = "\n".join(f"new line {i}" for i in range(100))
    diff = format_diff({"path": "large.txt", "previous": prev, "current": curr}, max_lines=10)
    assert "… (" in diff
    assert "more lines)" in diff


def test_format_event_run_started():
    ev = {
        "seq": 1,
        "type": "run_started",
        "payload": {"session_id": "sess-abc", "goal": "Fix authentication bug"},
        "ts": 1000.0,
    }
    rendered = format_event(ev)
    assert rendered is not None
    assert "Run started" in rendered
    assert "sess-abc" in rendered
    assert "Fix authentication bug" in rendered


def test_format_event_step_started():
    ev = {
        "seq": 2,
        "type": "step_started",
        "payload": {"role": "review", "iteration": 0},
        "ts": 1001.0,
    }
    rendered = format_event(ev)
    assert rendered is not None
    assert "review" in rendered
    assert "iteration" not in rendered

    ev_iter1 = {
        "seq": 3,
        "type": "step_started",
        "payload": {"role": "build", "iteration": 1},
        "ts": 1002.0,
    }
    rendered_iter1 = format_event(ev_iter1)
    assert rendered_iter1 is not None
    assert "build" in rendered_iter1
    assert "iteration" not in rendered_iter1

    ev_iter2 = {
        "seq": 4,
        "type": "step_started",
        "payload": {"role": "build", "iteration": 2},
        "ts": 1003.0,
    }
    rendered_iter2 = format_event(ev_iter2)
    assert rendered_iter2 is not None
    assert "build" in rendered_iter2
    assert "iteration 2" in rendered_iter2


def test_format_event_text_delta():
    ev = {
        "seq": 4,
        "type": "text_delta",
        "payload": {"delta": "Thinking about the fix..."},
        "ts": 1003.0,
    }
    assert format_event(ev) == "Thinking about the fix..."


def test_format_event_tool_call():
    ev = {
        "seq": 5,
        "type": "tool_call",
        "payload": {"tool_name": "ReadFile", "args": {"path": "src/auth.py"}},
        "ts": 1004.0,
    }
    rendered = format_event(ev)
    assert rendered is not None
    assert "⚙ ReadFile" in rendered
    assert "src/auth.py" in rendered


def test_format_event_tool_result_plain():
    ev_ok = {
        "seq": 6,
        "type": "tool_result",
        "payload": {
            "tool_name": "ReadFile",
            "args": {"path": "src/auth.py"},
            "status": "OK",
            "execution_time_ms": 42,
            "result": {"output": "def login(): pass\nreturn True\n"},
        },
        "ts": 1005.0,
    }
    rendered_ok = format_event(ev_ok)
    assert rendered_ok is not None
    assert "✓" in rendered_ok
    assert "ReadFile" in rendered_ok
    assert "src/auth.py" in rendered_ok
    assert "(3 lines)" in rendered_ok

    ev_err = {
        "seq": 7,
        "type": "tool_result",
        "payload": {
            "tool_name": "ReadFile",
            "status": "ERROR",
            "execution_time_ms": 10,
            "error": "File not found",
        },
        "ts": 1006.0,
    }
    rendered_err = format_event(ev_err)
    assert rendered_err is not None
    assert "✗" in rendered_err
    assert "File not found" in rendered_err

    # Generic tool output
    ev_gen = {
        "type": "tool_result",
        "payload": {
            "tool_name": "CodeSearch",
            "status": "OK",
            "execution_time_ms": 25,
            "result": {"output": "found auth in src/auth.py"},
        },
    }
    rendered_gen = format_event(ev_gen)
    assert "CodeSearch" in rendered_gen
    assert "25ms" in rendered_gen
    assert "found auth in src/auth.py" in rendered_gen


def test_format_event_tool_result_list_directory():
    ev = {
        "type": "tool_result",
        "payload": {
            "tool_name": "ListDirectory",
            "status": "OK",
            "execution_time_ms": 12,
            "result": {
                "output": "\n".join([
                    "[dir] .git",
                    "[file] .git/config",
                    "[file] a.py",
                    "[file] b.py",
                    "[file] c.py",
                    "[file] d.py",
                    "[file] e.py",
                    "[file] f.py",
                    "[file] g.py",
                    "[file] h.py",
                    "[file] i.py",
                ])
            },
        },
    }
    rendered = format_event(ev)
    assert rendered is not None
    assert ".git" not in rendered
    assert "a.py" in rendered
    assert "h.py" in rendered
    assert "… (+1 more)" in rendered


def test_format_event_tool_result_diff():
    ev = {
        "seq": 8,
        "type": "tool_result",
        "payload": {
            "tool_name": "WriteFile",
            "status": "OK",
            "execution_time_ms": 50,
            "result": {
                "structured": {
                    "path": "app.py",
                    "previous": "version = 1",
                    "current": "version = 2",
                }
            },
        },
        "ts": 1007.0,
    }
    rendered = format_event(ev)
    assert rendered is not None
    assert "✓" in rendered
    assert "WriteFile" in rendered
    assert "app.py" in rendered
    assert "version = 2" in rendered


def test_format_event_step_finished():
    ev = {
        "seq": 9,
        "type": "step_finished",
        "payload": {"step": {"role": "review"}},
        "ts": 1008.0,
    }
    rendered = format_event(ev)
    assert rendered is not None
    assert "review finished" in rendered


def test_format_event_blocker():
    ev_fatal = {
        "seq": 10,
        "type": "blocker",
        "payload": {"reason": "budget", "detail": "Exceeded $5.00 limit", "fatal": True},
        "ts": 1009.0,
    }
    rendered_fatal = format_event(ev_fatal)
    assert rendered_fatal is not None
    assert "⚠ budget" in rendered_fatal
    assert "Exceeded $5.00 limit" in rendered_fatal

    ev_warn = {
        "seq": 11,
        "type": "blocker",
        "payload": {"reason": "permission", "detail": "Denied tool WriteFile", "fatal": False},
        "ts": 1010.0,
    }
    rendered_warn = format_event(ev_warn)
    assert rendered_warn is not None
    assert "⚠ permission" in rendered_warn
    assert "Denied tool WriteFile" in rendered_warn


def test_format_event_user_message():
    ev = {
        "seq": 12,
        "type": "user_message",
        "payload": {"kind": "steer", "body": "Please also update tests"},
        "ts": 1011.0,
    }
    rendered = format_event(ev)
    assert rendered is not None
    assert "💬 User (steer):" in rendered
    assert "Please also update tests" in rendered


def test_format_event_run_stopped_and_finished():
    ev_stop = {
        "seq": 13,
        "type": "run_stopped",
        "payload": {"reason": "user stop signal"},
        "ts": 1012.0,
    }
    assert "Run stopped" in (format_event(ev_stop) or "")

    ev_fin_pushed = {
        "seq": 14,
        "type": "run_finished",
        "payload": {"pushed": {"pushed": True}},
        "ts": 1013.0,
    }
    assert "committed and pushed" in (format_event(ev_fin_pushed) or "")

    ev_fin_err = {
        "seq": 15,
        "type": "run_finished",
        "payload": {"error": "API error 500"},
        "ts": 1014.0,
    }
    assert "✗ Run ended with error: API error 500" in (format_event(ev_fin_err) or "")

    ev_fin_ok = {
        "seq": 16,
        "type": "run_finished",
        "payload": {"pushed": None},
        "ts": 1015.0,
    }
    assert format_event(ev_fin_ok) is None


def test_format_event_error_and_skipped():
    ev_err = {
        "seq": 16,
        "type": "error",
        "payload": {"error": "Connection timed out"},
        "ts": 1015.0,
    }
    assert "Error:" in (format_event(ev_err) or "")
    assert "Connection timed out" in (format_event(ev_err) or "")

    # Noise events are skipped (return None)
    assert format_event({"type": "notification", "payload": {}}) is None
    assert format_event({"type": "usage", "payload": {}}) is None
    assert format_event({"type": "unknown_event_type", "payload": {}}) is None


def test_session_cost():
    runs = [
        {
            "steps": [
                {"usage": {"cost_usd": 0.05}},
                {"usage": {"cost_usd": 0.10}},
            ]
        },
        {
            "steps": [
                {"usage": {"cost_usd": 0.20}},
            ]
        },
    ]
    cost = session_cost(runs)
    assert pytest.approx(cost, 0.0001) == 0.35


def test_format_footer():
    state = {
        "started_at": 100.0,
        "finished_at": 115.5,
        "stopped": False,
        "pushed": {"pushed": True},
        "blockers": [],
        "steps": [
            {
                "usage": {
                    "backend": "openrouter",
                    "model": "deepseek/deepseek-chat",
                    "input_tokens": 1500,
                    "output_tokens": 500,
                    "cost_usd": 0.0035,
                }
            },
            {
                "usage": {
                    "backend": "claude-code",
                    "model": "claude-3-7-sonnet",
                    "input_tokens": 800,
                    "output_tokens": 200,
                    "cost_usd": 0.0120,
                }
            },
        ],
    }
    footer = format_footer(state)
    assert "PUSHED" in footer
    assert "15.5s" in footer
    assert "$0.0155" in footer
    assert "openrouter:deepseek/deepseek-chat" in footer
    assert "claude-code:claude-3-7-sonnet" in footer
    assert "Session:" not in footer


def test_format_footer_with_session():
    state = {
        "started_at": 100.0,
        "finished_at": 115.5,
        "stopped": False,
        "pushed": {"pushed": True},
        "blockers": [],
        "steps": [],
    }
    # 1. With title
    sess_with_title = {"session_id": "sess-42-abcd", "title": "Add authentication"}
    footer1 = format_footer(state, session=sess_with_title)
    assert 'Session: sess-42-abcd — "Add authentication"' in footer1
    assert "PUSHED" in footer1

    # 2. With title=None -> (untitled)
    sess_no_title = {"session_id": "sess-42-abcd", "title": None}
    footer2 = format_footer(state, session=sess_no_title)
    assert 'Session: sess-42-abcd — "(untitled)"' in footer2

    # 3. With session=None -> no Session line
    footer3 = format_footer(state, session=None)
    assert "Session:" not in footer3


def test_session_tag_helper():
    from agentflow.tui.repl import _session_tag

    assert _session_tag("sess-1234-abcd") == "abcd"
    assert _session_tag("sess-5678") == "5678"
    assert _session_tag("1234567890") == "34567890"
    assert _session_tag("short") == "short"
    assert _session_tag("") == ""


def test_build_toolbar(isolate_database):
    from agentflow.database import create_session, save_run
    from agentflow.orchestrator import RunState
    from agentflow.tui.repl import _build_toolbar

    # Create session with runs and cost
    create_session("sess-test-9999-wxyz", cwd="/tmp", title="Optimize query", path=isolate_database)
    state = RunState(
        run_id="run-t1",
        goal="Optimize query",
        started_at=time.time(),
        config={},
        session_id="sess-test-9999-wxyz",
    )
    state.steps.append(
        {
            "role": "build",
            "usage": {"cost_usd": 0.0345},
        }
    )
    save_run(state, "/tmp", path=isolate_database)

    toolbar_str = _build_toolbar("sess-test-9999-wxyz", database_path=isolate_database)
    assert 'agentflow · wxyz · "Optimize query" · $0.0345' == toolbar_str

    # Test with untitled session
    create_session("sess-test-8888-none", cwd="/tmp", title=None, path=isolate_database)
    toolbar_untitled = _build_toolbar("sess-test-8888-none", database_path=isolate_database)
    assert 'agentflow · none · "untitled" · $0.0000' == toolbar_untitled

    # Test with non-existent session
    toolbar_missing = _build_toolbar("sess-nonexistent-1234", database_path=isolate_database)
    assert 'agentflow · 1234 · "untitled" · $0.0000' == toolbar_missing


def test_build_toolbar_error_fallback():
    from agentflow.tui.repl import _build_toolbar

    with patch("agentflow.database.get_session", side_effect=RuntimeError("DB exploded")):
        res = _build_toolbar("sess-1")
        assert res == ""



# ============================================================================
# 2. permissions.py Unit Tests
# ============================================================================


def test_broker_allow_set_short_circuit():
    broker = SessionPermissionBroker(allowed_tools={"ReadFile", "WriteFile"})
    # Handler should return "allow" immediately for allowed tools
    assert broker.handler("ReadFile", {"path": "a.txt"}) == "allow"
    assert broker.handler("WriteFile", {"path": "b.txt", "content": "x"}) == "allow"
    assert broker.poll() is None


def test_broker_queue_round_trip():
    broker = SessionPermissionBroker()
    results: list[str] = []

    def _worker():
        ans = broker.handler("Shell", {"command": "pytest"})
        results.append(ans)

    t = threading.Thread(target=_worker)
    t.start()

    time.sleep(0.05)
    req = broker.poll()
    assert req is not None
    assert req.tool_name == "Shell"
    assert req.args == {"command": "pytest"}

    # Main thread responds "allow"
    broker.respond(req, "allow")
    t.join(timeout=2.0)
    assert results == ["allow"]
    assert "Shell" not in broker.allowed_tools


def test_broker_allow_session_adds_to_set():
    broker = SessionPermissionBroker()
    results: list[str] = []

    def _worker():
        ans = broker.handler("WriteFile", {"path": "foo.py", "content": "1"})
        results.append(ans)

    t = threading.Thread(target=_worker)
    t.start()

    time.sleep(0.05)
    req = broker.poll()
    assert req is not None
    broker.respond(req, "allow_session")
    t.join(timeout=2.0)

    assert results == ["allow_session"]
    assert "WriteFile" in broker.allowed_tools

    # Subsequent call immediately auto-allows
    assert broker.handler("WriteFile", {"path": "bar.py", "content": "2"}) == "allow"


def test_broker_cancel_all():
    broker = SessionPermissionBroker()
    results: list[str] = []

    def _worker():
        ans = broker.handler("Edit", {"path": "foo.py"})
        results.append(ans)

    t = threading.Thread(target=_worker)
    t.start()

    time.sleep(0.05)
    broker.cancel_all()
    t.join(timeout=2.0)
    assert results == ["deny"]


# ============================================================================
# 3. commands.py Unit Tests
# ============================================================================


def test_parse_command():
    assert parse_command("not a command") is None
    assert parse_command("/help") == ("/help", [])
    assert parse_command("  /model build deepseek/deepseek-chat  ") == (
        "/model",
        ["build", "deepseek/deepseek-chat"],
    )
    assert parse_command("/config max-cost 1.50") == ("/config", ["max-cost", "1.50"])


def test_dispatch_help():
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    res = dispatch("/help", [], config, cwd="/tmp", session_id="s1")
    assert "/model" in res.output
    assert "/config" in res.output
    assert "/resume" in res.output


def test_dispatch_model(capsys):
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    # 1. No args -> list models
    res_list = dispatch("/model", [], config, cwd="/tmp", session_id="s1")
    assert "claude-3-7-sonnet" in res_list.output

    # 2. Update model
    res_set = dispatch(
        "/model", ["build", "deepseek/deepseek-chat"], config, cwd="/tmp", session_id="s1"
    )
    assert "deepseek/deepseek-chat" in res_set.output
    assert config.build.model == "deepseek/deepseek-chat"

    # 3. Invalid role
    res_err = dispatch("/model", ["invalid_role", "m1"], config, cwd="/tmp", session_id="s1")
    assert "unknown role" in res_err.output


def test_dispatch_config():
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    # 1. Show config
    res_show = dispatch("/config", [], config, cwd="/tmp", session_id="s1")
    assert "review: backend=claude-code" in res_show.output

    # 2. Permissions
    dispatch("/config", ["permissions", "deny"], config, cwd="/tmp", session_id="s1")
    assert config.permissions == "deny"

    # 3. Max cost
    dispatch("/config", ["max-cost", "2.5"], config, cwd="/tmp", session_id="s1")
    assert config.max_cost_usd == 2.5
    dispatch("/config", ["max-cost", "none"], config, cwd="/tmp", session_id="s1")
    assert config.max_cost_usd is None

    # 4. Role backend / model
    dispatch(
        "/config",
        ["build", "openrouter", "deepseek/deepseek-chat"],
        config,
        cwd="/tmp",
        session_id="s1",
    )
    assert config.build.backend == "openrouter"
    assert config.build.model == "deepseek/deepseek-chat"


def test_dispatch_tools():
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    res = dispatch("/tools", [], config, cwd="/tmp", session_id="s1")
    assert "ReadFile" in res.output
    assert "WriteFile" in res.output


def test_dispatch_resume_and_clear(isolate_database):
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    # Create session with run state
    create_session("sess-target", cwd="/tmp", title="Target goal", path=isolate_database)
    state = RunState(
        run_id="run-1",
        goal="Target goal",
        started_at=time.time(),
        config={},
        session_id="sess-target",
    )
    save_run(state, "/tmp", path=isolate_database)

    res_resume = dispatch(
        "/resume", ["sess-target"], config, cwd="/tmp", session_id="s1", database_path=isolate_database
    )
    assert res_resume.new_session_id == "sess-target"
    assert "Resumed session sess-target" in res_resume.output

    res_clear = dispatch("/clear", [], config, cwd="/tmp", session_id="sess-target")
    assert res_clear.should_clear is True
    assert res_clear.new_session_id is not None
    assert res_clear.new_session_id != "sess-target"


def test_dispatch_cost(isolate_database):
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    create_session("sess-cost", cwd="/tmp", title="Cost goal", path=isolate_database)
    state = RunState(
        run_id="run-c1",
        goal="Cost goal",
        started_at=time.time(),
        config={},
        session_id="sess-cost",
    )
    state.steps.append(
        {
            "role": "review",
            "usage": {"cost_usd": 0.042},
        }
    )
    save_run(state, "/tmp", path=isolate_database)

    res = dispatch("/cost", [], config, cwd="/tmp", session_id="sess-cost", database_path=isolate_database)
    assert "$0.0420" in res.output


def test_dispatch_exit():
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    res_exit = dispatch("/exit", [], config, cwd="/tmp", session_id="s1")
    assert res_exit.should_exit is True
    res_quit = dispatch("/quit", [], config, cwd="/tmp", session_id="s1")
    assert res_quit.should_exit is True


# ============================================================================
# 4. Orchestrator Permission Handler Integration
# ============================================================================


def test_orchestrator_check_tool_permission_with_handler():
    # Read-only tool always auto-allowed regardless of handler
    def deny_handler(tool_name, args):
        return "deny"

    allowed, reason = _check_tool_permission(
        "ReadFile", {"path": "a.txt"}, permissions_policy="prompt", permission_handler=deny_handler
    )
    assert allowed is True
    assert reason is None

    # Mutating tool with allow handler
    def allow_handler(tool_name, args):
        return "allow"

    allowed, reason = _check_tool_permission(
        "WriteFile",
        {"path": "a.txt", "content": "x"},
        permissions_policy="prompt",
        permission_handler=allow_handler,
    )
    assert allowed is True
    assert reason is None

    # Mutating tool with deny handler
    allowed, reason = _check_tool_permission(
        "WriteFile",
        {"path": "a.txt", "content": "x"},
        permissions_policy="prompt",
        permission_handler=deny_handler,
    )
    assert allowed is False
    assert "Permission denied by user for tool 'WriteFile'" in (reason or "")


# ============================================================================
# 5. REPL Integration Mock Turn
# ============================================================================


def test_repl_scripted_session(isolate_database, capsys):
    """Test full REPL turn with scripted user input and simulated events in DB."""
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )

    prompts = iter(["implement greeting feature", ""])

    def fake_prompt(self, message=""):
        try:
            val = next(prompts)
            if not val:
                raise EOFError
            return val
        except StopIteration:
            raise EOFError

    def fake_workflow(goal, config, cwd, run_id, session_id, database_path=None, permission_handler=None, quiet=False):
        # Emit representative events
        append_event(
            run_id,
            1,
            "run_started",
            {"run_id": run_id, "session_id": session_id, "goal": goal},
            path=database_path,
        )
        append_event(
            run_id,
            2,
            "step_started",
            {"role": "review", "iteration": 0},
            path=database_path,
        )
        append_event(
            run_id,
            3,
            "text_delta",
            {"delta": "Reviewing codebase..."},
            path=database_path,
        )
        append_event(
            run_id,
            4,
            "tool_call",
            {"tool_name": "ReadFile", "args": {"path": "greeting.py"}},
            path=database_path,
        )
        append_event(
            run_id,
            5,
            "tool_result",
            {
                "tool_name": "ReadFile",
                "status": "OK",
                "execution_time_ms": 15,
                "result": {"output": "def hello(): pass"},
            },
            path=database_path,
        )
        append_event(
            run_id,
            6,
            "step_finished",
            {"step": {"role": "review", "usage": {"cost_usd": 0.001}}},
            path=database_path,
        )
        append_event(
            run_id,
            7,
            "run_finished",
            {"finished_at": time.time(), "pushed": {"pushed": True}},
            path=database_path,
        )

        state = RunState(
            run_id=run_id,
            goal=goal,
            started_at=time.time() - 2.0,
            config={},
            session_id=session_id,
            finished_at=time.time(),
        )
        state.pushed = {"pushed": True}
        state.steps.append(
            {
                "role": "review",
                "usage": {
                    "backend": "claude-code",
                    "model": "claude-3-7-sonnet",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cost_usd": 0.001,
                },
            }
        )
        save_run(state, cwd, path=database_path)
        return state

    with (
        patch("prompt_toolkit.PromptSession.prompt", side_effect=fake_prompt, autospec=True),
        patch("agentflow.orchestrator.run_workflow", side_effect=fake_workflow) as mock_wf,
    ):
        ret = run_repl(config, cwd="/tmp/repo", session_id="test-repl-session", database_path=isolate_database)

    assert ret == 0
    assert mock_wf.call_args[1].get("quiet") is True
    captured = capsys.readouterr()
    assert "implement greeting feature" in captured.out
    assert "ReadFile" in captured.out
    assert "✓" in captured.out
    assert "PUSHED" in captured.out
    assert "$0.0010" in captured.out


def test_repl_dedup_consecutive_tools(isolate_database, capsys):
    """Test that immediately consecutive identical tool calls render '(same as above)'."""
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )

    prompts = iter(["check duplicates", ""])

    def fake_prompt(self, message=""):
        try:
            val = next(prompts)
            if not val:
                raise EOFError
            return val
        except StopIteration:
            raise EOFError

    def fake_workflow(goal, config, cwd, run_id, session_id, database_path=None, permission_handler=None, quiet=False):
        append_event(run_id, 1, "run_started", {"run_id": run_id, "session_id": session_id, "goal": goal}, path=database_path)
        append_event(run_id, 2, "step_started", {"role": "build", "iteration": 1}, path=database_path)
        # First call
        append_event(run_id, 3, "tool_call", {"tool_name": "ReadFile", "args": {"path": "a.py"}}, path=database_path)
        append_event(run_id, 4, "tool_result", {"tool_name": "ReadFile", "args": {"path": "a.py"}, "status": "OK", "result": {"output": "1\n2\n"}}, path=database_path)
        # Duplicate consecutive call
        append_event(run_id, 5, "tool_call", {"tool_name": "ReadFile", "args": {"path": "a.py"}}, path=database_path)
        append_event(run_id, 6, "tool_result", {"tool_name": "ReadFile", "args": {"path": "a.py"}, "status": "OK", "result": {"output": "1\n2\n"}}, path=database_path)
        # Different call
        append_event(run_id, 7, "tool_call", {"tool_name": "ReadFile", "args": {"path": "b.py"}}, path=database_path)
        append_event(run_id, 8, "tool_result", {"tool_name": "ReadFile", "args": {"path": "b.py"}, "status": "OK", "result": {"output": "3\n"}}, path=database_path)
        append_event(run_id, 9, "run_finished", {"finished_at": time.time(), "pushed": {"pushed": True}}, path=database_path)

        state = RunState(
            run_id=run_id,
            goal=goal,
            started_at=time.time() - 1.0,
            config={},
            session_id=session_id,
            finished_at=time.time(),
        )
        state.pushed = {"pushed": True}
        save_run(state, cwd, path=database_path)
        return state

    with (
        patch("prompt_toolkit.PromptSession.prompt", side_effect=fake_prompt, autospec=True),
        patch("agentflow.orchestrator.run_workflow", side_effect=fake_workflow),
    ):
        ret = run_repl(config, cwd="/tmp/repo", session_id="test-repl-session", database_path=isolate_database)

    assert ret == 0
    captured = capsys.readouterr()
    assert "(same as above)" in captured.out


# ============================================================================
# 8. Slash Command Autocomplete & Registry Tests
# ============================================================================


def test_slash_command_completer():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.backends import BACKENDS
    from agentflow.tui.commands import COMMANDS
    from agentflow.tui.completion import SlashCommandCompleter

    completer = SlashCommandCompleter()

    def complete(text: str) -> list[str]:
        completions = list(completer.get_completions(Document(text, len(text)), CompleteEvent()))
        return [c.text for c in completions]

    # "/" -> yields every registry command name
    all_cmd_names = [spec.name for spec in COMMANDS]
    assert complete("/") == all_cmd_names

    # "/co" -> yields exactly /config and /cost
    assert complete("/co") == ["/config", "/cost"]

    # "/config " -> yields permissions, max-cost, review, build, verify
    assert complete("/config ") == ["permissions", "max-cost", "review", "build", "verify"]

    # "/config review " -> yields the backend names (tuple(BACKENDS))
    assert complete("/config review ") == list(BACKENDS.keys())

    # "/config permissions " -> yields auto, prompt, deny
    assert complete("/config permissions ") == ["auto", "prompt", "deny"]

    # "/model " -> yields review, build, verify
    assert complete("/model ") == ["review", "build", "verify"]

    # "build a login page" (no slash) -> yields nothing
    assert complete("build a login page") == []

    # Partial arguments
    assert complete("/config p") == ["permissions"]
    assert complete("/config permissions a") == ["auto"]
    assert complete("/model r") == ["review"]

    # Model ID completions (static curated catalog, < 40 models)
    model_completions = complete("/model review ")
    assert 0 < len(model_completions) < 40
    assert "deepseek/deepseek-v4-flash" in model_completions
    assert "claude-3-7-sonnet" in model_completions


def test_slash_command_completer_session_resolver():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import SlashCommandCompleter

    # Default has no session resolver -> returns empty list
    completer_default = SlashCommandCompleter()
    assert list(completer_default.get_completions(Document("/resume ", len("/resume ")), CompleteEvent())) == []

    # Custom session resolver
    completer_with_sessions = SlashCommandCompleter(session_resolver=lambda: ["sess-1", "sess-2"])
    completions = [c.text for c in completer_with_sessions.get_completions(Document("/resume ", len("/resume ")), CompleteEvent())]
    assert completions == ["sess-1", "sess-2"]


def test_help_contains_all_commands():
    from agentflow.config import Config, RoleConfig
    from agentflow.tui.commands import COMMANDS, dispatch

    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    res = dispatch("/help", [], config, cwd="/tmp", session_id="s1")
    for spec in COMMANDS:
        assert spec.name in res.output


# ============================================================================
# 9. Mid-Run Input & Steer Tests (Phase J.5a)
# ============================================================================


def test_process_new_events_renders_tool_calls(isolate_database):
    from agentflow.tui.repl import _process_new_events

    run_id = "test-proc-run"
    append_event(
        run_id,
        1,
        "step_started",
        {"role": "build", "iteration": 1},
        path=isolate_database,
    )
    append_event(
        run_id,
        2,
        "text_delta",
        {"delta": "Writing code..."},
        path=isolate_database,
    )
    append_event(
        run_id,
        3,
        "tool_call",
        {"tool_name": "WriteFile", "args": {"path": "test.py"}},
        path=isolate_database,
    )
    append_event(
        run_id,
        4,
        "tool_result",
        {
            "tool_name": "WriteFile",
            "status": "OK",
            "execution_time_ms": 20,
            "result": {"output": "ok"},
        },
        path=isolate_database,
    )
    append_event(
        run_id,
        5,
        "step_finished",
        {"step": {"role": "build", "usage": {"cost_usd": 0.0125}}},
        path=isolate_database,
    )

    fake_console = MagicMock()
    state = {
        "last_seq": -1,
        "accumulated_deltas": [],
        "last_role": "agent",
        "last_rendered_tool_sig": None,
        "current_call_sig": None,
        "cost": 0.0,
    }

    _process_new_events(run_id, isolate_database, fake_console, state)

    assert state["last_seq"] == 5
    assert state["last_role"] == "build"
    assert pytest.approx(state["cost"], 1e-6) == 0.0125
    printed_calls = [call[0][0] for call in fake_console.print.call_args_list]
    full_printed = "\n".join(printed_calls)
    assert "Writing code..." in full_printed
    assert "WriteFile" in full_printed
    assert "test.py" in full_printed
    assert "build finished" in full_printed


def test_parse_perm_answer():
    from agentflow.tui.repl import _parse_perm_answer

    assert _parse_perm_answer("a") == "allow"
    assert _parse_perm_answer("A") == "allow"
    assert _parse_perm_answer("allow") == "allow"
    assert _parse_perm_answer("y") == "allow"
    assert _parse_perm_answer("yes") == "allow"
    assert _parse_perm_answer("s") == "allow_session"
    assert _parse_perm_answer("S") == "allow_session"
    assert _parse_perm_answer("session") == "allow_session"
    assert _parse_perm_answer("allow_session") == "allow_session"
    assert _parse_perm_answer("allow for session") == "allow_session"
    assert _parse_perm_answer("d") == "deny"
    assert _parse_perm_answer("deny") == "deny"
    assert _parse_perm_answer("n") == "deny"
    assert _parse_perm_answer("no") == "deny"
    assert _parse_perm_answer("") == "deny"
    assert _parse_perm_answer(None) == "deny"
    assert _parse_perm_answer("random_garbage") == "deny"


def test_fmt_elapsed(monkeypatch):
    from agentflow.tui.repl import _fmt_elapsed

    assert _fmt_elapsed(None) == "0s"
    assert _fmt_elapsed(0.0) == "0s"

    monkeypatch.setattr(time, "monotonic", lambda: 105.0)
    assert _fmt_elapsed(100.0) == "5s"

    monkeypatch.setattr(time, "monotonic", lambda: 165.0)
    assert _fmt_elapsed(100.0) == "1:05"

    monkeypatch.setattr(time, "monotonic", lambda: 3705.0)
    assert _fmt_elapsed(100.0) == "60:05"


def test_turn_toolbar_hidden_when_worker_done():
    from agentflow.tui.repl import _turn_toolbar

    fake_worker = MagicMock()
    fake_worker.is_alive.return_value = False
    tstate = {"last_role": "build", "cost": 0.042}

    assert _turn_toolbar(tstate, fake_worker) == ""


def test_turn_toolbar_shows_role_when_alive():
    from prompt_toolkit.formatted_text import to_plain_text

    from agentflow.tui.repl import _turn_toolbar

    fake_worker = MagicMock()
    fake_worker.is_alive.return_value = True
    tstate = {"last_role": "build", "cost": 0.042, "start": time.monotonic()}

    bar = _turn_toolbar(tstate, fake_worker)
    plain = to_plain_text(bar)
    assert "build" in plain
    assert "working" in plain
    assert "$0.0420" in plain
    assert "Ctrl+C" in plain
    assert "0s" in plain or "s" in plain


def test_turn_prompt_message_shows_spinner_when_running():
    from prompt_toolkit.formatted_text import to_plain_text

    from agentflow.tui.permissions import PermissionRequest
    from agentflow.tui.repl import _turn_prompt_message

    fake_worker = MagicMock()
    fake_worker.is_alive.return_value = True
    tstate = {"last_role": "build", "cost": 0.042, "start": time.monotonic()}
    pending_perm = {"req": None}

    # Worker alive -> HTML rendered text contains role + › + an s/: elapsed token
    msg = _turn_prompt_message(tstate, fake_worker, pending_perm)
    plain = to_plain_text(msg)
    assert "build" in plain
    assert "›" in plain
    assert "$0.0420" in plain
    assert "Ctrl+C to interrupt" in plain
    assert "0s" in plain or "s" in plain

    # pending_perm["req"] set -> returns the permission string containing the tool name
    req = PermissionRequest(tool_name="WriteFile", args={"path": "main.py"})
    pending_perm["req"] = req
    msg_perm = _turn_prompt_message(tstate, fake_worker, pending_perm)
    assert isinstance(msg_perm, str)
    assert "WriteFile" in msg_perm
    assert "allow" in msg_perm
    assert "[d]eny" in msg_perm

    # worker done + no perm -> just ›
    pending_perm["req"] = None
    fake_worker.is_alive.return_value = False
    msg_done = _turn_prompt_message(tstate, fake_worker, pending_perm)
    plain_done = to_plain_text(msg_done)
    assert "›" in plain_done
    assert "build" not in plain_done


def test_perm_prompt_message():
    from agentflow.tui.permissions import PermissionRequest
    from agentflow.tui.repl import _perm_prompt_message

    req = PermissionRequest(tool_name="WriteFile", args={"path": "main.py"})
    msg = _perm_prompt_message(req)
    assert "WriteFile" in msg
    assert "allow" in msg
    assert "[d]eny" in msg


def test_turn_interactive_steer(isolate_database):
    from prompt_toolkit import PromptSession
    from rich.console import Console

    from agentflow.config import Config, RoleConfig
    from agentflow.database import get_pending_messages
    from agentflow.tui.permissions import SessionPermissionBroker
    from agentflow.tui.repl import _execute_turn

    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )

    prompt_responses = ["please write tests first"]

    async def fake_prompt_async(*args, **kwargs):
        if prompt_responses:
            return prompt_responses.pop(0)
        raise EOFError

    def fake_workflow(goal, config, cwd, run_id, session_id, database_path=None, permission_handler=None, quiet=False):
        append_event(run_id, 1, "run_started", {"run_id": run_id, "session_id": session_id, "goal": goal}, path=database_path)
        append_event(run_id, 2, "step_started", {"role": "build", "iteration": 1}, path=database_path)
        time.sleep(0.05)
        append_event(run_id, 3, "run_finished", {"finished_at": time.time()}, path=database_path)
        state = RunState(
            run_id=run_id,
            goal=goal,
            started_at=time.time() - 1.0,
            config={},
            session_id=session_id,
            finished_at=time.time(),
        )
        save_run(state, cwd, path=database_path)
        return state

    broker = SessionPermissionBroker()
    console = Console(file=io.StringIO())
    session = PromptSession()

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("prompt_toolkit.patch_stdout.patch_stdout", lambda *a, **k: contextlib.nullcontext()),
        patch.object(PromptSession, "prompt_async", side_effect=fake_prompt_async),
        patch("agentflow.orchestrator.run_workflow", side_effect=fake_workflow),
    ):
        state = _execute_turn(
            goal="write a module",
            run_id="run-interactive-steer",
            config=config,
            cwd="/tmp",
            session_id="sess-interactive",
            database_path=isolate_database,
            broker=broker,
            console=console,
            prompt_session=session,
        )

    assert state is not None
    pending = get_pending_messages("run-interactive-steer", kind="steer", path=isolate_database)
    assert len(pending) == 1
    assert pending[0]["body"] == "please write tests first"
    assert pending[0]["kind"] == "steer"


def test_turn_interactive_uses_raw_patch_stdout(isolate_database):
    from prompt_toolkit import PromptSession
    from rich.console import Console

    from agentflow.config import Config, RoleConfig
    from agentflow.orchestrator import RunState
    from agentflow.tui.permissions import SessionPermissionBroker
    from agentflow.tui.repl import _execute_turn

    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )

    recorded_kwargs: dict[str, Any] = {}

    def fake_patch_stdout(*args, **kwargs):
        recorded_kwargs.update(kwargs)
        return contextlib.nullcontext()

    async def fake_prompt_async(*args, **kwargs):
        raise EOFError

    def fake_workflow(goal, config, cwd, run_id, session_id, database_path=None, permission_handler=None, quiet=False):
        append_event(run_id, 1, "run_started", {"run_id": run_id, "session_id": session_id, "goal": goal}, path=database_path)
        time.sleep(0.01)
        append_event(run_id, 2, "run_finished", {"finished_at": time.time()}, path=database_path)
        state = RunState(
            run_id=run_id,
            goal=goal,
            started_at=time.time() - 1.0,
            config={},
            session_id=session_id,
            finished_at=time.time(),
        )
        save_run(state, cwd, path=database_path)
        return state

    broker = SessionPermissionBroker()
    console = Console(file=io.StringIO())
    session = PromptSession()

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("prompt_toolkit.patch_stdout.patch_stdout", side_effect=fake_patch_stdout),
        patch.object(PromptSession, "prompt_async", side_effect=fake_prompt_async),
        patch("agentflow.orchestrator.run_workflow", side_effect=fake_workflow),
    ):
        state = _execute_turn(
            goal="check raw",
            run_id="run-raw-check",
            config=config,
            cwd="/tmp",
            session_id="sess-raw",
            database_path=isolate_database,
            broker=broker,
            console=console,
            prompt_session=session,
        )

    assert state is not None
    assert recorded_kwargs.get("raw") is True


def test_handle_mid_run_input_steer_plain_text(isolate_database):
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    mock_console = MagicMock()
    with patch("agentflow.database.add_pending_message") as mock_add:
        _handle_mid_run_input(
            "focus on tests",
            "run-123",
            config,
            "/tmp",
            "sess-1",
            isolate_database,
            mock_console,
        )
        mock_add.assert_called_once_with(
            "run-123", "focus on tests", kind="steer", path=isolate_database
        )
        mock_console.print.assert_called_once_with(
            "[dim]↳ queued — will steer at the next phase boundary.[/dim]"
        )


def test_handle_mid_run_input_safe_command_config(isolate_database):
    config = Config(
        permissions="auto",
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    mock_console = MagicMock()
    with patch("agentflow.database.add_pending_message") as mock_add:
        _handle_mid_run_input(
            "/config permissions deny",
            "run-123",
            config,
            "/tmp",
            "sess-1",
            isolate_database,
            mock_console,
        )
        assert config.permissions == "deny"
        mock_add.assert_not_called()
        assert mock_console.print.called
        printed = mock_console.print.call_args[0][0]
        assert "deny" in printed


def test_handle_mid_run_input_safe_command_model(isolate_database):
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    mock_console = MagicMock()
    with patch("agentflow.database.add_pending_message") as mock_add:
        _handle_mid_run_input(
            "/model review claude-3-7-sonnet",
            "run-123",
            config,
            "/tmp",
            "sess-1",
            isolate_database,
            mock_console,
        )
        assert config.review.model == "claude-3-7-sonnet"
        mock_add.assert_not_called()
        assert mock_console.print.called
        printed = mock_console.print.call_args[0][0]
        assert "claude-3-7-sonnet" in printed


def test_handle_mid_run_input_unsafe_command(isolate_database):
    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    mock_console = MagicMock()
    with patch("agentflow.database.add_pending_message") as mock_add:
        _handle_mid_run_input(
            "/clear",
            "run-123",
            config,
            "/tmp",
            "sess-1",
            isolate_database,
            mock_console,
        )
        mock_add.assert_not_called()
        mock_console.print.assert_called_once_with(
            "[yellow]/clear is not available during a run.[/yellow] It will not be queued."
        )


# ============================================================================
# 10. File Mention Autocomplete Tests (Phase J.6)
# ============================================================================


def test_file_mention_completer_basic():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = [
        "README.md",
        "src/agentflow/tui/render.py",
        "src/agentflow/tui/reader.py",
        "docs/readiness.md",
        "other/file.txt",
    ]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    # "@rea" -> matches README.md, docs/readiness.md, src/agentflow/tui/reader.py
    completions = list(completer.get_completions(Document("@rea", len("@rea")), CompleteEvent()))
    texts = [c.text for c in completions]
    assert "README.md" in texts
    assert "src/agentflow/tui/reader.py" in texts
    assert "docs/readiness.md" in texts
    assert "other/file.txt" not in texts

    # README.md (shorter path, basename match) ranks higher than deeper paths
    assert texts[0] == "README.md"
    # start_position covers len("rea") = 3
    assert completions[0].start_position == -3


def test_file_mention_completer_empty_query():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = [f"file_{i:03d}.py" for i in range(100)]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    completions = list(completer.get_completions(Document("@", len("@")), CompleteEvent()))
    assert len(completions) == 50
    assert completions[0].text == "file_000.py"
    assert completions[0].start_position == 0
    assert completions[-1].text == "file_049.py"


def test_file_mention_completer_ignores_slash():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = ["README.md", "config.py"]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    completions = list(completer.get_completions(Document("/config ", len("/config ")), CompleteEvent()))
    assert completions == []
    completions2 = list(completer.get_completions(Document("/config @con", len("/config @con")), CompleteEvent()))
    assert completions2 == []


def test_file_mention_completer_not_a_mention():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = ["README.md", "src/agentflow/tui/render.py"]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    assert list(completer.get_completions(Document("build a thing", len("build a thing")), CompleteEvent())) == []
    assert list(completer.get_completions(Document("user@example.com", len("user@example.com")), CompleteEvent())) == []
    assert list(completer.get_completions(Document("", 0), CompleteEvent())) == []
    assert list(completer.get_completions(Document("hello   ", len("hello   ")), CompleteEvent())) == []


def test_file_mention_completer_midline():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = [
        "src/agentflow/tui/render.py",
        "src/agentflow/tui/completion.py",
        "tests/test_tui.py",
    ]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    text = "fix the bug in @src/ag"
    completions = list(completer.get_completions(Document(text, len(text)), CompleteEvent()))
    texts = [c.text for c in completions]
    assert "src/agentflow/tui/render.py" in texts
    assert "src/agentflow/tui/completion.py" in texts

    # start_position must be -len("src/ag") = -6
    for c in completions:
        assert c.start_position == -6


def test_file_mention_completer_dotfile_prefix():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = [
        ".gitignore",
        ".github/copilot.md",
        ".env.example",
        "PLAN.md",
        "README.md",
        "src/app.py",
    ]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    # "@.gi" -> .gitignore (tier 1: basename prefix) ranks before .github/copilot.md (tier 2: segment prefix)
    completions_gi = list(
        completer.get_completions(Document("@.gi", len("@.gi")), CompleteEvent())
    )
    texts_gi = [c.text for c in completions_gi]
    assert texts_gi == [".gitignore", ".github/copilot.md"]
    assert "PLAN.md" not in texts_gi
    assert "README.md" not in texts_gi
    assert "src/app.py" not in texts_gi

    # "@." -> dotfile / dot-dir matches rank before normal files containing '.'
    completions_dot = list(
        completer.get_completions(Document("@.", len("@.")), CompleteEvent())
    )
    texts_dot = [c.text for c in completions_dot]
    assert texts_dot[:3] == [".gitignore", ".env.example", ".github/copilot.md"]
    assert set(texts_dot[3:]) == {"PLAN.md", "README.md", "src/app.py"}


def test_list_project_files_git(tmp_path):
    import shutil
    import subprocess
    import pytest
    from agentflow.tui.completion import _list_project_files

    if not shutil.which("git"):
        pytest.skip("git not available")

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    try:
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    except Exception:
        pytest.skip("git init failed")

    (repo_dir / "tracked.txt").write_text("hello")
    (repo_dir / "untracked.txt").write_text("world")
    (repo_dir / ".gitignore").write_text("ignored.txt\n")
    (repo_dir / "ignored.txt").write_text("secret")

    subprocess.run(["git", "add", "tracked.txt", ".gitignore"], cwd=repo_dir, check=True)

    files = _list_project_files(str(repo_dir))
    assert ".gitignore" in files
    assert "tracked.txt" in files
    assert "untracked.txt" in files
    assert "ignored.txt" in files
    assert not any(f.startswith(".git/") or f == ".git" for f in files)


def test_list_project_files_fallback(tmp_path):
    from agentflow.tui.completion import _list_project_files

    # Non-git directory
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    (plain_dir / "src").mkdir()
    (plain_dir / "src" / "app.py").write_text("print(1)")
    (plain_dir / "README.md").write_text("# Hi")

    # Noise dirs that should be ignored
    (plain_dir / ".venv").mkdir()
    (plain_dir / ".venv" / "pip.py").write_text("ignore")
    (plain_dir / "node_modules").mkdir()
    (plain_dir / "node_modules" / "pkg.js").write_text("ignore")
    (plain_dir / "__pycache__").mkdir()
    (plain_dir / "__pycache__" / "app.pyc").write_text("ignore")

    files = _list_project_files(str(plain_dir))
    assert "src/" in files
    assert "README.md" in files
    assert "src/app.py" in files
    assert not any(".venv" in f or "node_modules" in f or "__pycache__" in f for f in files)


def test_list_project_files_keeps_dot_dirs(tmp_path):
    from agentflow.tui.completion import _list_project_files

    plain_dir = tmp_path / "plain_with_dotdirs"
    plain_dir.mkdir()
    (plain_dir / ".github" / "workflows").mkdir(parents=True)
    (plain_dir / ".github" / "workflows" / "ci.yml").write_text("name: CI")
    (plain_dir / ".git").mkdir()
    (plain_dir / ".git" / "HEAD").write_text("ref: refs/heads/main")
    (plain_dir / "src").mkdir()
    (plain_dir / "src" / "app.py").write_text("print(1)")

    files = _list_project_files(str(plain_dir))
    assert ".github/" in files
    assert ".github/workflows/" in files
    assert ".github/workflows/ci.yml" in files
    assert "src/" in files
    assert "src/app.py" in files
    assert not any(f.startswith(".git/") or f == ".git" for f in files)


def test_list_project_files_includes_gitignored(tmp_path):
    import shutil
    import subprocess
    import pytest
    from agentflow.tui.completion import _list_project_files

    if not shutil.which("git"):
        pytest.skip("git not available")

    repo_dir = tmp_path / "repo_ignored"
    repo_dir.mkdir()
    try:
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    except Exception:
        pytest.skip("git init failed")

    (repo_dir / ".gitignore").write_text("scratch/\n")
    (repo_dir / "scratch").mkdir()
    (repo_dir / "scratch" / "notes.py").write_text("secret notes")

    subprocess.run(["git", "add", ".gitignore"], cwd=repo_dir, check=True)

    files = _list_project_files(str(repo_dir))
    assert "scratch/notes.py" in files
    assert "scratch/" in files


def test_file_mention_completer_dir_entries():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = [".test/", ".test/dragtask/", ".test/dragtask/app.py", "src/app.py"]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    completions = list(completer.get_completions(Document("@.te", len("@.te")), CompleteEvent()))
    texts = [c.text for c in completions]
    assert texts[0] == ".test/"
    assert ".test/dragtask/app.py" in texts
    test_completion = next(c for c in completions if c.text == ".test/")
    assert test_completion.display_meta_text == "dir"


def test_score_strips_trailing_slash():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = [".test/", ".testing/", ".test/x.py"]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    completions = list(completer.get_completions(Document("@.test", len("@.test")), CompleteEvent()))
    texts = [c.text for c in completions]
    assert texts[0] == ".test/"


def test_merged_completer_in_repl():
    from prompt_toolkit.completion import CompleteEvent, merge_completers
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter, SlashCommandCompleter

    files = ["README.md", "src/agentflow/tui/render.py"]
    completer = merge_completers([
        SlashCommandCompleter(),
        FileMentionCompleter("/tmp", file_lister=lambda: files),
    ])

    # Slash command works
    slash_completions = [c.text for c in completer.get_completions(Document("/co", len("/co")), CompleteEvent())]
    assert "/config" in slash_completions

    # @-file mention works
    mention_completions = [c.text for c in completer.get_completions(Document("@REA", len("@REA")), CompleteEvent())]
    assert "README.md" in mention_completions

    # Plain text without @ yields nothing
    plain_completions = [c.text for c in completer.get_completions(Document("just plain goal", len("just plain goal")), CompleteEvent())]
    assert plain_completions == []


def test_file_mention_completer_caching():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    call_count = 0

    def lister():
        nonlocal call_count
        call_count += 1
        return ["file1.txt"]

    completer = FileMentionCompleter(cwd="/fake", file_lister=lister, cache_ttl=10.0)

    # First call triggers lister
    list(completer.get_completions(Document("@f", len("@f")), CompleteEvent()))
    assert call_count == 1

    # Second call within TTL does not trigger lister again
    list(completer.get_completions(Document("@f", len("@f")), CompleteEvent()))
    assert call_count == 1


def test_file_mention_completer_display_meta():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from agentflow.tui.completion import FileMentionCompleter

    files = ["README.md", "src/agentflow/tui/render.py"]
    completer = FileMentionCompleter(cwd="/fake", file_lister=lambda: files)

    completions = list(completer.get_completions(Document("@", len("@")), CompleteEvent()))
    meta_by_text = {c.text: c.display_meta_text for c in completions}
    assert meta_by_text["README.md"] == ""
    assert meta_by_text["src/agentflow/tui/render.py"] == "src/agentflow/tui"





