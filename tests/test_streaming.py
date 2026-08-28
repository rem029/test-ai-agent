"""Unit tests for backend streaming and run_sync drain."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import (
    Event,
    Message,
    RunResult,
    Usage,
    format_messages_to_prompt,
    run_sync,
)
from agentflow.backends.antigravity import AntigravityBackend
from agentflow.backends.claude_code import ClaudeCodeBackend
from agentflow.backends.openrouter import OpenRouterBackend


def test_event_creation_and_to_dict():
    delta = Event.text_delta("chunk")
    assert delta.type == "text_delta"
    assert delta.payload == {"delta": "chunk"}
    assert delta.to_dict() == {"type": "text_delta", "payload": {"delta": "chunk"}}

    tc = Event.tool_call("ReadFile", {"path": "foo.py"})
    assert tc.type == "tool_call"
    assert tc.payload["name"] == "ReadFile"

    tr = Event.tool_result("ReadFile", {"output": "hello"}, success=True)
    assert tr.type == "tool_result"
    assert tr.payload["success"] is True

    u = Event.usage(Usage("mock", "model", 10, 20, 0.001))
    assert u.type == "usage"
    assert u.payload["cost_usd"] == 0.001

    d = Event.done(success=True, text="finished", raw={"foo": "bar"})
    assert d.type == "done"
    assert d.payload["text"] == "finished"

    err = Event.error("something broke")
    assert err.type == "error"
    assert err.payload["error"] == "something broke"


def test_message_and_formatting():
    msg1 = Message(role="system", content="You are a helper")
    msg2 = Message(role="user", content="Hello")
    msg3 = Message(role="assistant", content="Hi", tool_calls=[{"name": "test"}])
    msg4 = Message(role="tool", content="tool output", tool_results=[{"output": "ok"}])

    formatted = format_messages_to_prompt([msg1, msg2, msg3, msg4])
    assert "System:\nYou are a helper" in formatted
    assert "User:\nHello" in formatted
    assert "Assistant:\nHi" in formatted
    assert "Tool Result:\ntool output" in formatted

    assert format_messages_to_prompt("plain string") == "plain string"
    assert msg3.to_dict()["tool_calls"] == [{"name": "test"}]


def test_run_sync_drains_stream():
    def event_generator():
        yield Event.text_delta("Hello, ")
        yield Event.text_delta("world!")
        yield Event.usage(Usage(backend="test", model="m1", input_tokens=5, output_tokens=10, cost_usd=0.005))
        yield Event.done(success=True, raw={"k": "v"})

    result = run_sync(event_generator())
    assert isinstance(result, RunResult)
    assert result.success is True
    assert result.text == "Hello, world!"
    assert result.usage.cost_usd == 0.005
    assert result.usage.input_tokens == 5
    assert result.usage.output_tokens == 10
    assert result.raw == {"k": "v"}


def test_run_sync_handles_error_event():
    def event_generator():
        yield Event.text_delta("partial")
        yield Event.error("Model overloaded")
        yield Event.done(success=False)

    result = run_sync(event_generator())
    assert result.success is False
    assert result.text == "partial"


def test_run_sync_passthrough_run_result():
    existing = RunResult(success=True, text="already done", usage=Usage("b", "m", 1, 1, 0.0), raw={})
    assert run_sync(existing) is existing


def test_claude_code_streaming():
    backend = ClaudeCodeBackend(model="claude-3-7-sonnet")
    stream_lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Step 1"}]}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "ReadFile", "input": {"path": "a.py"}}]}}),
        json.dumps({
            "type": "result",
            "is_error": False,
            "result": "Step 1",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "total_cost_usd": 0.002,
        }),
    ]

    mock_proc = MagicMock()
    mock_proc.stdout = iter(stream_lines)
    mock_proc.stderr = StringIO("")
    mock_proc.returncode = 0
    mock_proc.wait.return_value = 0

    with patch("subprocess.Popen", return_value=mock_proc):
        events = list(backend.run("test prompt", cwd="."))
        types = [e.type for e in events]
        assert "text_delta" in types
        assert "tool_call" in types
        assert "usage" in types
        assert "done" in types

    mock_proc2 = MagicMock()
    mock_proc2.stdout = iter(stream_lines)
    mock_proc2.stderr = StringIO("")
    mock_proc2.returncode = 0
    mock_proc2.wait.return_value = 0

    with patch("subprocess.Popen", return_value=mock_proc2):
        res = backend.run_sync("test prompt", cwd=".")
        assert res.success is True


def test_openrouter_streaming():
    backend = OpenRouterBackend(model="deepseek/deepseek-v4-flash")
    sse_lines = [
        'data: {"choices": [{"delta": {"content": "Chunk 1"}}]}',
        'data: {"choices": [{"delta": {"content": " Chunk 2"}}]}',
        'data: {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "cost": 0.0001}}',
        'data: [DONE]',
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_lines.return_value = iter(sse_lines)

    mock_stream = MagicMock()
    mock_stream.__enter__.return_value = mock_resp
    mock_stream.__exit__.return_value = False

    with patch("agentflow.backends.openrouter.openrouter_api_key", return_value="fake-key"):
        with patch("httpx.stream", return_value=mock_stream):
            events = list(backend.run("test prompt", cwd="."))

    types = [e.type for e in events]
    assert types == ["text_delta", "text_delta", "usage", "done"]
    assert events[0].payload["delta"] == "Chunk 1"
    assert events[1].payload["delta"] == " Chunk 2"
    assert events[2].payload["cost_usd"] == 0.0001


def test_antigravity_streaming():
    backend = AntigravityBackend(model="gemini-2.5-flash")
    mock_result = RunResult(
        success=True,
        text="SDK output",
        usage=Usage("antigravity", "gemini-2.5-flash", 10, 20, 0.0),
        raw={},
    )
    with patch.object(backend, "_run_sdk", return_value=mock_result):
        with patch("shutil.which", return_value=None):
            events = list(backend.run("test prompt", cwd="."))

    assert len(events) == 3
    assert events[0].type == "text_delta"
    assert events[0].payload["delta"] == "SDK output"
    assert events[1].type == "usage"
    assert events[2].type == "done"
    assert events[2].payload["success"] is True

    with patch.object(backend, "_run_sdk", return_value=mock_result):
        with patch("shutil.which", return_value=None):
            res = backend.run_sync("test prompt", cwd=".")
            assert res.text == "SDK output"
            assert res.success is True
