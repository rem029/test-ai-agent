"""Tests for agentflow credential resolution (env override > config credentials > None)."""

from __future__ import annotations

from agentflow.credentials import (
    openrouter_api_key,
    openrouter_api_key_info,
    openrouter_credential_source,
    smtp_password,
    smtp_password_info,
)


def test_openrouter_key_resolution_order(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("credentials:\n  openrouter_api_key: sk-or-test-from-config-12345\n")

    # Config file value is used when env is unset
    assert openrouter_api_key(str(config)) == "sk-or-test-from-config-12345"

    # Environment variable overrides config file value
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-from-env-67890")
    assert openrouter_api_key(str(config)) == "sk-or-test-from-env-67890"

    # When both are unset
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config.write_text("review:\n  backend: claude-code\n")
    assert openrouter_api_key(str(config)) is None


def test_openrouter_api_key_info_with_env(tmp_path, monkeypatch):
    real_key = "fake-openrouter-key-abcdefghijklmnop"
    monkeypatch.setenv("OPENROUTER_API_KEY", real_key)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("credentials:\n  openrouter_api_key: sk-or-test-config-key\n")

    info = openrouter_api_key_info(str(config))
    assert info["set"] is True
    assert info["source"] == "env"
    masked = info["masked"]
    assert masked == f"{real_key[:8]}…{real_key[-4:]}"


def test_openrouter_api_key_info_with_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config_key = "sk-or-test-from-config-1234567890"
    config = tmp_path / "agentflow.config.yaml"
    config.write_text(f"credentials:\n  openrouter_api_key: {config_key}\n")

    info = openrouter_api_key_info(str(config))
    assert info["set"] is True
    assert info["source"] == "config"
    assert info["masked"] == f"{config_key[:8]}…{config_key[-4:]}"


def test_openrouter_api_key_info_when_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("build:\n  backend: openrouter\n")

    info = openrouter_api_key_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}


def test_openrouter_credential_source_descriptions(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("build:\n  backend: openrouter\n")

    assert openrouter_credential_source(str(config)) == f"not set (configure in {config} or set OPENROUTER_API_KEY)"

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key-not-a-secret-01")
    assert openrouter_credential_source(str(config)) == "environment (OPENROUTER_API_KEY, dev override)"

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config.write_text("credentials:\n  openrouter_api_key: sk-or-test-key-not-a-secret-01\n")
    assert openrouter_credential_source(str(config)) == f"{config} (credentials.openrouter_api_key)"


def test_openrouter_api_key_info_short_keys(tmp_path, monkeypatch):
    config = tmp_path / "agentflow.config.yaml"

    monkeypatch.setenv("OPENROUTER_API_KEY", "123456789012")  # 12 chars
    info = openrouter_api_key_info(str(config))
    assert info["masked"] == "…12"

    monkeypatch.setenv("OPENROUTER_API_KEY", "xy")  # 2 chars
    info = openrouter_api_key_info(str(config))
    assert info["masked"] == "…"

    monkeypatch.setenv("OPENROUTER_API_KEY", "a")  # 1 char
    info = openrouter_api_key_info(str(config))
    assert info["masked"] == "…"


def test_smtp_password_resolution_order(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("credentials:\n  smtp_password: pw-unit-test-from-config\n")

    # Config file value is used when env is unset
    assert smtp_password(str(config)) == "pw-unit-test-from-config"

    # Environment variable overrides config file value
    monkeypatch.setenv("AGENTFLOW_SMTP_PASSWORD", "pw-unit-test-from-env")
    assert smtp_password(str(config)) == "pw-unit-test-from-env"

    # When both are unset
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    config.write_text("review:\n  backend: claude-code\n")
    assert smtp_password(str(config)) is None


def test_smtp_password_info_with_env(tmp_path, monkeypatch):
    pwd = "pw-unit-test-value-000000"
    monkeypatch.setenv("AGENTFLOW_SMTP_PASSWORD", pwd)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("credentials:\n  smtp_password: file-pw\n")

    info = smtp_password_info(str(config))
    assert info["set"] is True
    assert info["source"] == "env"
    assert info["masked"] == f"{pwd[:8]}…{pwd[-4:]}"


def test_smtp_password_info_with_config(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    pwd = "pw-unit-test-value-config-123"
    config = tmp_path / "agentflow.config.yaml"
    config.write_text(f"credentials:\n  smtp_password: {pwd}\n")

    info = smtp_password_info(str(config))
    assert info["set"] is True
    assert info["source"] == "config"
    assert info["masked"] == f"{pwd[:8]}…{pwd[-4:]}"


def test_smtp_password_info_when_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("build:\n  backend: openrouter\n")

    info = smtp_password_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}
