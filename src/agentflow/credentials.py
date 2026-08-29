"""Credential resolution shared by API-backed agent backends."""

from __future__ import annotations

import os

from .config import DEFAULT_CONFIG_PATH


class CredentialConfigError(ValueError):
    """Raised when a configured credential source cannot be read."""


def _mask_key(key: str) -> str:
    if len(key) <= 2:
        return "…"
    if len(key) <= 12:
        return f"…{key[-2:]}"
    return f"{key[:8]}…{key[-4:]}"


def openrouter_api_key(config_path: str = DEFAULT_CONFIG_PATH) -> str | None:
    """Resolve an OpenRouter key from the environment (.env / OPENROUTER_API_KEY).

    The config_path parameter is retained for backward compatibility but ignored.
    """
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and key.strip():
        return key.strip()
    return None


def openrouter_api_key_info(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Return status and masked representation of the configured OpenRouter key."""
    key = openrouter_api_key(config_path)
    if not key:
        return {"set": False, "masked": None, "source": None}

    return {
        "set": True,
        "masked": _mask_key(key),
        "source": "env",
    }


def openrouter_credential_source(config_path: str = DEFAULT_CONFIG_PATH) -> str:
    """Describe the configured source without returning or exposing its value."""
    if openrouter_api_key(config_path):
        return "environment (.env / OPENROUTER_API_KEY)"
    return "not set (add OPENROUTER_API_KEY to .env)"


def smtp_password(config_path: str = DEFAULT_CONFIG_PATH) -> str | None:
    """Resolve an SMTP password from the environment (.env / AGENTFLOW_SMTP_PASSWORD).

    The config_path parameter is retained for backward compatibility but ignored.
    """
    pwd = os.environ.get("AGENTFLOW_SMTP_PASSWORD")
    if pwd and pwd.strip():
        return pwd.strip()
    return None


def smtp_password_info(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Return status and masked representation of the configured SMTP password."""
    pwd = smtp_password(config_path)
    if not pwd:
        return {"set": False, "masked": None, "source": None}

    return {
        "set": True,
        "masked": _mask_key(pwd),
        "source": "env",
    }
