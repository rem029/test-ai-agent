"""Credential resolution shared by API-backed agent backends."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .config import DEFAULT_CONFIG_PATH


class CredentialConfigError(ValueError):
    """Raised when a configured credential source cannot be read."""


def _mask_key(key: str) -> str:
    if len(key) <= 2:
        return "…"
    if len(key) <= 12:
        return f"…{key[-2:]}"
    return f"{key[:8]}…{key[-4:]}"


def _read_config_credentials(config_path: str = DEFAULT_CONFIG_PATH) -> dict | None:
    try:
        p = Path(config_path)
        if not p.exists() or not p.is_file():
            return None
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None
        creds = data.get("credentials")
        if not isinstance(creds, dict):
            return None
        return creds
    except Exception:
        return None


def openrouter_api_key(config_path: str = DEFAULT_CONFIG_PATH) -> str | None:
    """Resolve an OpenRouter key: env var (dev override) > config file > None."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and key.strip():
        return key.strip()
    creds = _read_config_credentials(config_path)
    if creds:
        file_key = creds.get("openrouter_api_key")
        if isinstance(file_key, str) and file_key.strip():
            return file_key.strip()
    return None


def openrouter_api_key_info(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Return status and masked representation of the configured OpenRouter key."""
    env_key = os.environ.get("OPENROUTER_API_KEY")
    if env_key and env_key.strip():
        return {
            "set": True,
            "masked": _mask_key(env_key.strip()),
            "source": "env",
        }
    creds = _read_config_credentials(config_path)
    if creds:
        file_key = creds.get("openrouter_api_key")
        if isinstance(file_key, str) and file_key.strip():
            return {
                "set": True,
                "masked": _mask_key(file_key.strip()),
                "source": "config",
            }
    return {"set": False, "masked": None, "source": None}


def openrouter_credential_source(config_path: str = DEFAULT_CONFIG_PATH) -> str:
    """Describe the configured source without returning or exposing its value."""
    env_key = os.environ.get("OPENROUTER_API_KEY")
    if env_key and env_key.strip():
        return "environment (OPENROUTER_API_KEY, dev override)"
    creds = _read_config_credentials(config_path)
    if creds:
        file_key = creds.get("openrouter_api_key")
        if isinstance(file_key, str) and file_key.strip():
            return f"{config_path} (credentials.openrouter_api_key)"
    return f"not set (configure in {config_path} or set OPENROUTER_API_KEY)"


def smtp_password(config_path: str = DEFAULT_CONFIG_PATH) -> str | None:
    """Resolve an SMTP password: env var (dev override) > config file > None."""
    pwd = os.environ.get("AGENTFLOW_SMTP_PASSWORD")
    if pwd and pwd.strip():
        return pwd.strip()
    creds = _read_config_credentials(config_path)
    if creds:
        file_pwd = creds.get("smtp_password")
        if isinstance(file_pwd, str) and file_pwd.strip():
            return file_pwd.strip()
    return None


def smtp_password_info(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Return status and masked representation of the configured SMTP password."""
    env_pwd = os.environ.get("AGENTFLOW_SMTP_PASSWORD")
    if env_pwd and env_pwd.strip():
        return {
            "set": True,
            "masked": _mask_key(env_pwd.strip()),
            "source": "env",
        }
    creds = _read_config_credentials(config_path)
    if creds:
        file_pwd = creds.get("smtp_password")
        if isinstance(file_pwd, str) and file_pwd.strip():
            return {
                "set": True,
                "masked": _mask_key(file_pwd.strip()),
                "source": "config",
            }
    return {"set": False, "masked": None, "source": None}
