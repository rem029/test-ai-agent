"""Credential resolution shared by API-backed agent backends."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .config import AGENTFLOW_HOME, DEFAULT_CONFIG_PATH, active_config_path


class CredentialConfigError(ValueError):
    """Raised when a configured credential source cannot be read."""


def _mask_key(key: str) -> str:
    if len(key) <= 2:
        return "…"
    if len(key) <= 12:
        return f"…{key[-2:]}"
    return f"{key[:8]}…{key[-4:]}"


def _candidate_config_paths(explicit_path: str | Path | None = None) -> list[Path]:
    raw_candidates: list[str | Path] = []
    if explicit_path is not None and str(explicit_path).strip():
        raw_candidates.append(str(explicit_path).strip())
    env_cfg = os.environ.get("AGENTFLOW_CONFIG")
    if env_cfg and env_cfg.strip():
        raw_candidates.append(env_cfg.strip())
    active = active_config_path()
    if active and str(active).strip():
        raw_candidates.append(str(active).strip())
    raw_candidates.append(AGENTFLOW_HOME / "agentflow.config.yaml")
    raw_candidates.append(DEFAULT_CONFIG_PATH)

    seen: set[str] = set()
    candidates: list[Path] = []
    for c in raw_candidates:
        try:
            p = Path(c).expanduser()
            resolved_key = str(p.resolve())
            if resolved_key not in seen:
                seen.add(resolved_key)
                candidates.append(p)
        except Exception:
            key = str(c)
            if key not in seen:
                seen.add(key)
                candidates.append(Path(c))
    return candidates


def _read_file_credentials(path: Path | str) -> dict | None:
    try:
        p = Path(path).expanduser()
        if not p.is_file():
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


def _read_config_credentials(config_path: str | None = None) -> dict | None:
    try:
        for candidate in _candidate_config_paths(config_path):
            creds = _read_file_credentials(candidate)
            if creds:
                return creds
        return None
    except Exception:
        return None


def _resolve_credential(
    key_name: str,
    config_path: str | None = None,
) -> tuple[str | None, Path | None]:
    try:
        for candidate in _candidate_config_paths(config_path):
            creds = _read_file_credentials(candidate)
            if creds:
                val = creds.get(key_name)
                if isinstance(val, str) and val.strip():
                    return val.strip(), candidate
        return None, None
    except Exception:
        return None, None


def openrouter_api_key(config_path: str | None = None) -> str | None:
    """Resolve an OpenRouter key: env var (dev override) > config file > None."""
    try:
        key = os.environ.get("OPENROUTER_API_KEY")
        if key and key.strip():
            return key.strip()
        val, _ = _resolve_credential("openrouter_api_key", config_path)
        return val
    except Exception:
        return None


def openrouter_api_key_info(config_path: str | None = None) -> dict:
    """Return status and masked representation of the configured OpenRouter key."""
    try:
        env_key = os.environ.get("OPENROUTER_API_KEY")
        if env_key and env_key.strip():
            return {
                "set": True,
                "masked": _mask_key(env_key.strip()),
                "source": "env",
            }
        val, _ = _resolve_credential("openrouter_api_key", config_path)
        if val:
            return {
                "set": True,
                "masked": _mask_key(val),
                "source": "config",
            }
        return {"set": False, "masked": None, "source": None}
    except Exception:
        return {"set": False, "masked": None, "source": None}


def openrouter_credential_source(config_path: str | None = None) -> str:
    """Describe the configured source without returning or exposing its value."""
    try:
        env_key = os.environ.get("OPENROUTER_API_KEY")
        if env_key and env_key.strip():
            return "environment (OPENROUTER_API_KEY, dev override)"
        val, matched_path = _resolve_credential("openrouter_api_key", config_path)
        if val and matched_path:
            return f"{matched_path} (credentials.openrouter_api_key)"
        fallback_target = config_path or active_config_path() or DEFAULT_CONFIG_PATH
        return f"not set (configure in {fallback_target} or set OPENROUTER_API_KEY)"
    except Exception:
        return f"not set (configure in {config_path or DEFAULT_CONFIG_PATH} or set OPENROUTER_API_KEY)"


def smtp_password(config_path: str | None = None) -> str | None:
    """Resolve an SMTP password: env var (dev override) > config file > None."""
    try:
        pwd = os.environ.get("AGENTFLOW_SMTP_PASSWORD")
        if pwd and pwd.strip():
            return pwd.strip()
        val, _ = _resolve_credential("smtp_password", config_path)
        return val
    except Exception:
        return None


def smtp_password_info(config_path: str | None = None) -> dict:
    """Return status and masked representation of the configured SMTP password."""
    try:
        env_pwd = os.environ.get("AGENTFLOW_SMTP_PASSWORD")
        if env_pwd and env_pwd.strip():
            return {
                "set": True,
                "masked": _mask_key(env_pwd.strip()),
                "source": "env",
            }
        val, _ = _resolve_credential("smtp_password", config_path)
        if val:
            return {
                "set": True,
                "masked": _mask_key(val),
                "source": "config",
            }
        return {"set": False, "masked": None, "source": None}
    except Exception:
        return {"set": False, "masked": None, "source": None}
