"""Unit tests for the Antigravity backend."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.antigravity import AntigravityBackend, _antigravity_binary
from agentflow.backends.base import HealthCheckResult, Message, RunResult, Usage


def test_antigravity_binary_resolution():
    def mock_which(cmd: str) -> str | None:
        if cmd == "agy":
            return "/config/.local/bin/agy"
        if cmd == "antigravity":
            return "/usr/bin/antigravity"
        return None

    with patch("shutil.which", side_effect=mock_which):
        assert _antigravity_binary() == "/config/.local/bin/agy"

    def mock_which_legacy(cmd: str) -> str | None:
        if cmd == "agy":
            return None
        if cmd == "antigravity":
            return "/usr/bin/antigravity"
        return None

    with patch("shutil.which", side_effect=mock_which_legacy):
        assert _antigravity_binary() == "/usr/bin/antigravity"

    with patch("shutil.which", return_value=None):
        assert _antigravity_binary() is None


def test_check_cli_success():
    backend = AntigravityBackend()
    mock_proc = MagicMock(returncode=0, stdout="pong\n", stderr="")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = backend._check_cli()

    assert res is not None
    assert res.ok is True
    assert res.backend == "antigravity"
    assert res.detail == "CLI (agy): pong"
    mock_run.assert_called_once_with(
        ["/fake/bin/agy", "-p", "Reply with exactly one word: pong", "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_check_cli_not_found():
    backend = AntigravityBackend()
    with patch("agentflow.backends.antigravity._antigravity_binary", return_value=None):
        assert backend._check_cli() is None


def test_check_cli_timeout():
    backend = AntigravityBackend()
    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="agy", timeout=60)):
            res = backend._check_cli()

    assert res is not None
    assert res.ok is False
    assert res.detail == "agy -p timed out"


def test_check_cli_failure():
    backend = AntigravityBackend()
    mock_proc = MagicMock(returncode=1, stdout="", stderr="authentication failed")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc):
            res = backend._check_cli()

    assert res is not None
    assert res.ok is False
    assert res.detail == "agy -p exited 1: authentication failed"


def test_run_cli_write_mode_with_model():
    backend = AntigravityBackend(model="gemini-2.5-pro")
    mock_proc = MagicMock(returncode=0, stdout="done generating changes", stderr="")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = backend._run_cli("Please edit file.py", cwd="/workspace", mode="write")

    assert res.success is True
    assert res.text == "done generating changes"
    mock_run.assert_called_once_with(
        [
            "/fake/bin/agy",
            "-p",
            "Please edit file.py",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--mode",
            "accept-edits",
            "--model",
            "gemini-2.5-pro",
        ],
        cwd="/workspace",
        capture_output=True,
        text=True,
        timeout=900,
    )


def test_run_cli_read_mode_no_model():
    backend = AntigravityBackend()
    mock_proc = MagicMock(returncode=0, stdout="read output", stderr="")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = backend._run_cli("Read this", cwd="/workspace", mode="read")

    assert res.success is True
    assert res.text == "read output"
    mock_run.assert_called_once_with(
        [
            "/fake/bin/agy",
            "-p",
            "Read this",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ],
        cwd="/workspace",
        capture_output=True,
        text=True,
        timeout=900,
    )


def test_run_cli_json_usage():
    backend = AntigravityBackend(model="gemini-3.7-flash-high")
    payload = {
        "conversation_id": "c47eff30-0d59-4cda-bfce-c1f4a0140950",
        "status": "SUCCESS",
        "response": "pong\n",
        "duration_seconds": 1.67,
        "num_turns": 1,
        "usage": {
            "input_tokens": 13803,
            "output_tokens": 23,
            "thinking_tokens": 22,
            "cache_read_tokens": 0,
            "total_tokens": 13826,
        },
        "total_cost_usd": 0.0015,
    }
    mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc):
            res = backend._run_cli("Reply with exactly one word: pong", cwd="/workspace", mode="read")

    assert res.success is True
    assert res.text == "pong"
    assert res.usage.backend == "antigravity"
    assert res.usage.model == "gemini-3.7-flash-high"
    assert res.usage.input_tokens == 13803
    assert res.usage.output_tokens == 23
    assert res.usage.cost_usd == 0.0015
    assert res.raw == payload


def test_run_cli_model_usage_map():
    backend = AntigravityBackend()
    payload = {
        "status": "SUCCESS",
        "result": "analyzed",
        "modelUsage": {
            "gemini-3.7-flash": {"inputTokens": 100, "outputTokens": 20},
            "gemini-3.1-pro": {"inputTokens": 200, "outputTokens": 50},
        },
        "total_cost_usd": 0.004,
    }
    mock_proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc):
            res = backend._run_cli("analyze", cwd="/workspace", mode="read")

    assert res.success is True
    assert res.text == "analyzed"
    assert res.usage.input_tokens == 300
    assert res.usage.output_tokens == 70
    assert res.usage.cost_usd == 0.004


def test_run_cli_non_json_fallback():
    backend = AntigravityBackend(model="gemini-2.5-pro")
    mock_proc = MagicMock(returncode=0, stdout="plain text response without json", stderr="")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc):
            res = backend._run_cli("hello", cwd="/workspace", mode="read")

    assert res.success is True
    assert res.text == "plain text response without json"
    assert res.usage.input_tokens is None
    assert res.usage.output_tokens is None
    assert res.usage.cost_usd is None



def test_run_cli_not_found():
    backend = AntigravityBackend()
    with patch("agentflow.backends.antigravity._antigravity_binary", return_value=None):
        res = backend._run_cli("Prompt", cwd="/workspace", mode="read")

    assert res.success is False
    assert "Antigravity CLI (agy) not found on PATH" in res.text


def test_run_cli_timeout():
    backend = AntigravityBackend()
    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="agy", timeout=900)):
            res = backend._run_cli("Prompt", cwd="/workspace", mode="read")

    assert res.success is False
    assert res.text == "agy -p timed out"


def test_run_cli_error():
    backend = AntigravityBackend()
    mock_proc = MagicMock(returncode=1, stdout="", stderr="Something went wrong")

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch("subprocess.run", return_value=mock_proc):
            res = backend._run_cli("Prompt", cwd="/workspace", mode="read")

    assert res.success is False
    assert res.text == "Something went wrong"


def test_run_routes_to_cli():
    backend = AntigravityBackend()
    mock_cli_res = RunResult(success=True, text="CLI pong", usage=Usage("antigravity", None, None, None, None), raw={})

    with patch("agentflow.backends.antigravity._antigravity_binary", return_value="/fake/bin/agy"):
        with patch.object(backend, "_run_cli", return_value=mock_cli_res) as mock_run_cli:
            events = list(backend.run("test prompt", cwd="/workspace"))

    mock_run_cli.assert_called_once_with("test prompt", cwd="/workspace", mode="read")
    assert any(e.type == "text_delta" and e.payload["delta"] == "CLI pong" for e in events)


def test_check_sdk_wording(monkeypatch):
    backend = AntigravityBackend()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)

    fake_module = MagicMock()
    with patch.dict("sys.modules", {"google.antigravity": fake_module}):
        res = backend._check_sdk()
        assert res.ok is False
        assert "Antigravity CLI (`agy`) not found on PATH" in res.detail
