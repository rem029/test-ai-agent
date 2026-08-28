"""Credential resolution shared by API-backed agent backends."""

from __future__ import annotations

import os

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


def openrouter_api_key(config_path: str = DEFAULT_CONFIG_PATH) -> str | None:
    """Resolve an OpenRouter key from the environment or agentflow YAML config."""
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return key

    from pathlib import Path

    path = Path(config_path)
    if not path.exists():
        return None

    try:
        config = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise CredentialConfigError(f"Could not read agentflow config at {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CredentialConfigError(f"Invalid agentflow config at {path}: {exc}") from exc

    if not isinstance(config, dict):
        raise CredentialConfigError(f"Invalid agentflow config at {path}: expected a mapping")

    key = config.get("openrouter_api_key")
    return key if isinstance(key, str) and key else None


def openrouter_api_key_info(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Return status and masked representation of the configured OpenRouter key."""
    try:
        key = openrouter_api_key(config_path)
    except CredentialConfigError:
        key = None

    if not key:
        return {"set": False, "masked": None, "source": None}

    source = "env" if os.environ.get("OPENROUTER_API_KEY") else "config"
    return {
        "set": True,
        "masked": _mask_key(key),
        "source": source,
    }


def openrouter_credential_source(config_path: str = DEFAULT_CONFIG_PATH) -> str:
    """Describe the configured source without returning or exposing its value."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return "Environment variable"
    try:
        return (
            f"agentflow config: {config_path}"
            if openrouter_api_key(config_path)
            else f"Not configured (checked {config_path})"
        )
    except CredentialConfigError as exc:
        return str(exc)
