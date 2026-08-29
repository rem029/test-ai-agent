"""Tests for agentflow credential resolution (env override > config credentials > None)."""

from __future__ import annotations

from agentflow.config import load_config
from agentflow.credentials import (
    openrouter_api_key,
    openrouter_api_key_info,
    openrouter_credential_source,
    smtp_password,
    smtp_password_info,
)


def test_openrouter_key_resolution_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config_key = "sk-or-test-from-config-1234567890"
    config = tmp_path / "agentflow.config.yaml"
    config.write_text(f"credentials:\n  openrouter_api_key: {config_key}\n")

    info = openrouter_api_key_info(str(config))
    assert info["set"] is True
    assert info["source"] == "config"
    assert info["masked"] == f"{config_key[:8]}…{config_key[-4:]}"


def test_openrouter_api_key_info_when_not_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("build:\n  backend: openrouter\n")

    info = openrouter_api_key_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}


def test_openrouter_credential_source_descriptions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
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
    monkeypatch.chdir(tmp_path)
    pwd = "pw-unit-test-value-000000"
    monkeypatch.setenv("AGENTFLOW_SMTP_PASSWORD", pwd)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("credentials:\n  smtp_password: file-pw\n")

    info = smtp_password_info(str(config))
    assert info["set"] is True
    assert info["source"] == "env"
    assert info["masked"] == f"{pwd[:8]}…{pwd[-4:]}"


def test_smtp_password_info_with_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    pwd = "pw-unit-test-value-config-123"
    config = tmp_path / "agentflow.config.yaml"
    config.write_text(f"credentials:\n  smtp_password: {pwd}\n")

    info = smtp_password_info(str(config))
    assert info["set"] is True
    assert info["source"] == "config"
    assert info["masked"] == f"{pwd[:8]}…{pwd[-4:]}"


def test_smtp_password_info_when_not_set(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    config = tmp_path / "agentflow.config.yaml"
    config.write_text("build:\n  backend: openrouter\n")

    info = smtp_password_info(str(config))
    assert info == {"set": False, "masked": None, "source": None}


def test_openrouter_key_active_config_path_regression(tmp_path, monkeypatch):
    """Regression test for cwd bug: openrouter_api_key() resolves via active_config_path()."""
    config_dir = tmp_path / "config_dir"
    config_dir.mkdir()
    config = config_dir / "agentflow.config.yaml"
    config.write_text("credentials:\n  openrouter_api_key: sk-or-from-active-config-path-999\n")

    target_repo_dir = tmp_path / "target_repo"
    target_repo_dir.mkdir()
    monkeypatch.chdir(target_repo_dir)

    load_config(str(config))
    assert openrouter_api_key() == "sk-or-from-active-config-path-999"
    info = openrouter_api_key_info()
    assert info["set"] is True
    assert info["source"] == "config"
    assert info["masked"] == "sk-or-fr…-999"


def test_openrouter_key_agentflow_home_fallback(tmp_path, monkeypatch):
    """AGENTFLOW_HOME fallback when no env var or explicit path, and cwd elsewhere."""
    fake_home = tmp_path / "custom_home"
    fake_home.mkdir()
    home_config = fake_home / "agentflow.config.yaml"
    home_config.write_text("credentials:\n  openrouter_api_key: sk-or-from-agentflow-home-111\n")

    monkeypatch.setattr("agentflow.credentials.AGENTFLOW_HOME", fake_home)
    monkeypatch.setattr("agentflow.config.AGENTFLOW_HOME", fake_home)

    other_dir = tmp_path / "other_dir"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    assert openrouter_api_key() == "sk-or-from-agentflow-home-111"
    info = openrouter_api_key_info()
    assert info["set"] is True
    assert info["source"] == "config"
    assert info["masked"] == "sk-or-fr…-111"


def test_env_var_overrides_all_config_candidates(tmp_path, monkeypatch):
    """Env var overrides explicit path, AGENTFLOW_CONFIG, active_config_path, AGENTFLOW_HOME, and DEFAULT_CONFIG_PATH."""
    explicit_cfg = tmp_path / "explicit.yaml"
    explicit_cfg.write_text("credentials:\n  openrouter_api_key: sk-explicit\n  smtp_password: pw-explicit\n")

    env_cfg = tmp_path / "env_config.yaml"
    env_cfg.write_text("credentials:\n  openrouter_api_key: sk-env-cfg\n  smtp_password: pw-env-cfg\n")
    monkeypatch.setenv("AGENTFLOW_CONFIG", str(env_cfg))

    active_cfg = tmp_path / "active.yaml"
    active_cfg.write_text("credentials:\n  openrouter_api_key: sk-active\n  smtp_password: pw-active\n")
    load_config(str(active_cfg))

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    home_cfg = home_dir / "agentflow.config.yaml"
    home_cfg.write_text("credentials:\n  openrouter_api_key: sk-home\n  smtp_password: pw-home\n")
    monkeypatch.setattr("agentflow.credentials.AGENTFLOW_HOME", home_dir)

    monkeypatch.chdir(tmp_path)
    default_cfg = tmp_path / "agentflow.config.yaml"
    default_cfg.write_text("credentials:\n  openrouter_api_key: sk-default\n  smtp_password: pw-default\n")

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env-override")
    monkeypatch.setenv("AGENTFLOW_SMTP_PASSWORD", "pw-env-override")

    assert openrouter_api_key(str(explicit_cfg)) == "sk-env-override"
    assert openrouter_api_key_info(str(explicit_cfg))["source"] == "env"
    assert smtp_password(str(explicit_cfg)) == "pw-env-override"
    assert smtp_password_info(str(explicit_cfg))["source"] == "env"


def test_explicit_config_path_without_key_falls_through_to_home(tmp_path, monkeypatch):
    """Explicit config_path with no key falls through to AGENTFLOW_HOME candidate that has one."""
    empty_cfg = tmp_path / "empty.yaml"
    empty_cfg.write_text("review:\n  backend: claude-code\n")

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    home_cfg = home_dir / "agentflow.config.yaml"
    home_cfg.write_text(
        "credentials:\n  openrouter_api_key: sk-from-home-fallback\n  smtp_password: pw-from-home-fallback\n"
    )
    monkeypatch.setattr("agentflow.credentials.AGENTFLOW_HOME", home_dir)

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    assert openrouter_api_key(str(empty_cfg)) == "sk-from-home-fallback"
    info = openrouter_api_key_info(str(empty_cfg))
    assert info["set"] is True
    assert info["source"] == "config"

    assert smtp_password(str(empty_cfg)) == "pw-from-home-fallback"
    smtp_info = smtp_password_info(str(empty_cfg))
    assert smtp_info["set"] is True
    assert smtp_info["source"] == "config"


def test_candidate_search_order(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    (home_dir / "agentflow.config.yaml").write_text("credentials:\n  openrouter_api_key: sk-from-home\n")
    monkeypatch.setattr("agentflow.credentials.AGENTFLOW_HOME", home_dir)

    active_cfg = tmp_path / "active.yaml"
    active_cfg.write_text("credentials:\n  openrouter_api_key: sk-from-active\n")

    env_cfg = tmp_path / "env.yaml"
    env_cfg.write_text("credentials:\n  openrouter_api_key: sk-from-env-cfg\n")

    explicit_cfg = tmp_path / "explicit.yaml"
    explicit_cfg.write_text("credentials:\n  openrouter_api_key: sk-from-explicit\n")

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "agentflow.config.yaml").write_text("credentials:\n  openrouter_api_key: sk-from-cwd\n")
    monkeypatch.chdir(repo_dir)

    # 1. explicit wins
    assert openrouter_api_key(str(explicit_cfg)) == "sk-from-explicit"

    # 2. AGENTFLOW_CONFIG wins over active, home, cwd
    monkeypatch.setenv("AGENTFLOW_CONFIG", str(env_cfg))
    load_config(str(active_cfg))
    assert openrouter_api_key() == "sk-from-env-cfg"

    # 3. active wins over home, cwd
    monkeypatch.delenv("AGENTFLOW_CONFIG")
    assert openrouter_api_key() == "sk-from-active"

    # 4. home wins over cwd
    monkeypatch.setattr("agentflow.config._ACTIVE_CONFIG_PATH", None)
    assert openrouter_api_key() == "sk-from-home"

    # 5. cwd wins when home has no key
    (home_dir / "agentflow.config.yaml").write_text("review:\n  backend: claude-code\n")
    assert openrouter_api_key() == "sk-from-cwd"
