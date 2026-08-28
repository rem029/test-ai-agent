"""Tests for agentflow credential resolution."""

from __future__ import annotations

from agentflow.credentials import (
    openrouter_api_key,
    openrouter_api_key_info,
)


def test_openrouter_key_loads_from_agentflow_config(tmp_path, monkeypatch):
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("openrouter_api_key: from-agentflow\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert openrouter_api_key(str(config)) == "from-agentflow"


def test_environment_key_takes_precedence_over_agentflow_config(tmp_path, monkeypatch):
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("openrouter_api_key: from-agentflow\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-environment")

    assert openrouter_api_key(str(config)) == "from-environment"


def test_openrouter_api_key_info_with_env(tmp_path, monkeypatch):
    real_key = "sk-or-v1-abcdef1234567890abcdef"
    monkeypatch.setenv("OPENROUTER_API_KEY", real_key)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("openrouter_api_key: other-key\n")

    info = openrouter_api_key_info(str(config))
    assert info["set"] is True
    assert info["source"] == "env"
    masked = info["masked"]
    assert masked.startswith(real_key[:8] + "…")
    assert masked.endswith(real_key[-4:])
    assert len(masked) < len(real_key)
    assert masked == f"{real_key[:8]}…{real_key[-4:]}"


def test_openrouter_api_key_info_with_config_only(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    real_key = "sk-or-v1-configkey-9876543210"
    config = tmp_path / "agentflow.config.yaml"
    config.write_text(f"openrouter_api_key: {real_key}\n")

    info = openrouter_api_key_info(str(config))
    assert info["set"] is True
    assert info["source"] == "config"
    assert info["masked"] == f"{real_key[:8]}…{real_key[-4:]}"
    assert len(info["masked"]) < len(real_key)


def test_openrouter_api_key_info_with_neither(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("build:\n  backend: claude-code\n")

    info = openrouter_api_key_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}


def test_openrouter_api_key_info_short_keys(tmp_path, monkeypatch):
    config = tmp_path / "agentflow.config.yaml"

    monkeypatch.setenv("OPENROUTER_API_KEY", "123456789012")  # 12 chars
    info = openrouter_api_key_info(str(config))
    assert info["masked"] == "…12"

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk")  # 2 chars
    info = openrouter_api_key_info(str(config))
    assert info["masked"] == "…"

    monkeypatch.setenv("OPENROUTER_API_KEY", "a")  # 1 char
    info = openrouter_api_key_info(str(config))
    assert info["masked"] == "…"


def test_openrouter_api_key_info_handles_corrupt_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("invalid: yaml: [syntax error\n")

    info = openrouter_api_key_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}
