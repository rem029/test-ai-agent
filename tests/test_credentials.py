"""Tests for agentflow credential resolution."""

from __future__ import annotations

from agentflow.credentials import openrouter_api_key


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
