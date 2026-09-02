"""Tests for agentflow configuration loading, dumping, and validation."""

from __future__ import annotations

from agentflow.config import (
    Config,
    CredentialsConfig,
    NotificationConfig,
    RoleConfig,
    _from_file,
    active_config_path,
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
        max_requirements_rounds=5,
        build_review=True,
        max_tool_calls=12,
        max_read_tool_calls=50,
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
    assert loaded.max_requirements_rounds == 5
    assert loaded.build_review is True
    assert loaded.max_tool_calls == 12
    assert loaded.max_read_tool_calls == 50
    assert loaded.permissions == "prompt"
    assert loaded.max_cost_usd == 2.5
    assert loaded.notifications is not None
    assert loaded.notifications.enabled is True
    assert loaded.notifications.email_to == "dev@example.com"
    assert loaded.notifications.smtp_port == 465
    assert loaded.notifications.notify_on == ["finished", "blocked"]
    assert loaded.notifications.base_url == "https://agentui.example.com"


def test_dump_and_load_config_credentials_roundtrip(tmp_path):
    config_file = tmp_path / "agentflow.config.yaml"
    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity", model="gemini-2.5-pro"),
        verify=RoleConfig(backend="claude-code"),
        credentials=CredentialsConfig(
            openrouter_api_key="sk-or-test-not-a-real-key",
            smtp_password="test-smtp-password-123",
        ),
    )

    dump_config(cfg, str(config_file))
    assert oct(config_file.stat().st_mode & 0o777) == oct(0o600)

    raw_data = _from_file(str(config_file))
    assert "credentials" in raw_data
    assert raw_data["credentials"]["openrouter_api_key"] == "sk-or-test-not-a-real-key"
    assert raw_data["credentials"]["smtp_password"] == "test-smtp-password-123"

    loaded = load_config(str(config_file))
    assert loaded.credentials is not None
    assert loaded.credentials.openrouter_api_key == "sk-or-test-not-a-real-key"
    assert loaded.credentials.smtp_password == "test-smtp-password-123"


def test_dump_config_omits_empty_credentials(tmp_path):
    config_file = tmp_path / "agentflow.config.yaml"
    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        credentials=CredentialsConfig(),
    )
    dump_config(cfg, str(config_file))
    raw_data = _from_file(str(config_file))
    assert "credentials" not in raw_data


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


def test_active_config_path(tmp_path):
    config_file = tmp_path / "custom_config.yaml"
    config_file.write_text("build:\n  backend: claude-code\n")

    load_config(str(config_file))
    assert active_config_path() == str(config_file)

    # Even for nonexistent file, load_config records the path hint
    load_config("nonexistent_path.yaml")
    assert active_config_path() == "nonexistent_path.yaml"


def test_dump_and_load_config_workflow_mode_roundtrip(tmp_path):
    config_file = tmp_path / "agentflow.config.yaml"
    cfg = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
        workflow_mode="review_only",
    )
    dump_config(cfg, str(config_file))
    loaded = load_config(str(config_file))
    assert loaded.workflow_mode == "review_only"

    # Default is auto
    cfg2 = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="antigravity"),
        verify=RoleConfig(backend="claude-code"),
    )
    assert cfg2.workflow_mode == "auto"


def test_load_config_env_workflow_mode(tmp_path, monkeypatch):
    config_file = tmp_path / "agentflow.config.yaml"
    config_file.write_text("workflow_mode: auto\nbuild:\n  backend: claude-code\n")

    monkeypatch.setenv("AGENTFLOW_WORKFLOW_MODE", "full")
    loaded = load_config(str(config_file))
    assert loaded.workflow_mode == "full"


