"""Tests for agentflow credential resolution (env-only)."""

from __future__ import annotations

from agentflow.credentials import (
    openrouter_api_key,
    openrouter_api_key_info,
    openrouter_credential_source,
    smtp_password,
    smtp_password_info,
)


def test_openrouter_key_resolves_from_env_only(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("openrouter_api_key: from-agentflow\n")

    # Config file is ignored for keys
    assert openrouter_api_key(str(config)) is None

    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-key-not-a-secret-01")
    assert openrouter_api_key(str(config)) == "unit-test-key-not-a-secret-01"


def test_openrouter_api_key_info_with_env(tmp_path, monkeypatch):
    real_key = "fake-openrouter-key-abcdefghijklmnop"
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


def test_openrouter_api_key_info_when_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("openrouter_api_key: ignored-config-key\n")

    info = openrouter_api_key_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}


def test_openrouter_credential_source_descriptions(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert openrouter_credential_source() == "not set (add OPENROUTER_API_KEY to .env)"

    monkeypatch.setenv("OPENROUTER_API_KEY", "unit-test-key-not-a-secret-01")
    assert openrouter_credential_source() == "environment (.env / OPENROUTER_API_KEY)"


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


def test_smtp_password_resolves_from_env_only(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("smtp_password: my-smtp-secret\n")

    # Config file is ignored for smtp password
    assert smtp_password(str(config)) is None

    monkeypatch.setenv("AGENTFLOW_SMTP_PASSWORD", "pw-unit-test-value-000000")
    assert smtp_password(str(config)) == "pw-unit-test-value-000000"


def test_smtp_password_info_with_env(tmp_path, monkeypatch):
    pwd = "pw-unit-test-value-000000"
    monkeypatch.setenv("AGENTFLOW_SMTP_PASSWORD", pwd)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("smtp_password: file-pw\n")

    info = smtp_password_info(str(config))
    assert info["set"] is True
    assert info["source"] == "env"
    assert info["masked"] == f"{pwd[:8]}…{pwd[-4:]}"


def test_smtp_password_info_when_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("smtp_password: file-pw\n")

    info = smtp_password_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}
