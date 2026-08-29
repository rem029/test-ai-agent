"""Tests for agentflow configuration loading, dumping, and validation."""

from __future__ import annotations

from agentflow.config import (
    Config,
    NotificationConfig,
    RoleConfig,
    _from_file,
    dump_config,
    load_config,
)


def test_dump_and_load_config_notifications_roundtrip(tmp_path):
    config_file = tmp_path / "agentflow.config.yaml"
    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity", model="gemini-2.5-pro"),
        verify=RoleConfig(backend="claude-code"),
        max_iterations=4,
        permissions="prompt",
        max_cost_usd=2.5,
        notifications=NotificationConfig(
            enabled=True,
            email_to="dev@example.com",
            email_from="bot@example.com",
            smtp_host="smtp.mail.com",
            smtp_port=465,
            smtp_username="bot@example.com",
            smtp_use_tls=True,
            notify_on=["finished", "blocked"],
            base_url="https://agentui.example.com",
        ),
    )

    dump_config(cfg, str(config_file))

    # Verify file permission is 0600
    assert oct(config_file.stat().st_mode & 0o777) == oct(0o600)

    # Verify raw file content has no secrets
    raw_data = _from_file(str(config_file))
    assert "smtp_password" not in raw_data
    assert "openrouter_api_key" not in raw_data
    assert raw_data["notifications"]["email_to"] == "dev@example.com"
    assert raw_data["notifications"]["smtp_port"] == 465

    # Verify load_config loads NotificationConfig correctly
    loaded = load_config(str(config_file))
    assert loaded.max_iterations == 4
    assert loaded.permissions == "prompt"
    assert loaded.max_cost_usd == 2.5
    assert loaded.notifications is not None
    assert loaded.notifications.enabled is True
    assert loaded.notifications.email_to == "dev@example.com"
    assert loaded.notifications.smtp_port == 465
    assert loaded.notifications.notify_on == ["finished", "blocked"]
    assert loaded.notifications.base_url == "https://agentui.example.com"


def test_dump_config_strips_deprecated_secrets_from_existing_file(tmp_path):
    config_file = tmp_path / "agentflow.config.yaml"
    config_file.write_text(
        "openrouter_api_key: legacy-key\n"
        "smtp_password: legacy-pw\n"
        "build:\n"
        "  backend: claude-code\n"
    )

    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
    )

    dump_config(cfg, str(config_file))
    raw = _from_file(str(config_file))
    assert "openrouter_api_key" not in raw
    assert "smtp_password" not in raw


def test_load_config_env_notifications_enabled(tmp_path, monkeypatch):
    config_file = tmp_path / "agentflow.config.yaml"
    config_file.write_text("build:\n  backend: claude-code\n")

    monkeypatch.setenv("AGENTFLOW_NOTIFICATIONS_ENABLED", "true")
    loaded = load_config(str(config_file))
    assert loaded.notifications is not None
    assert loaded.notifications.enabled is True
