"""Backend-per-role configuration.

Each workflow role (review/plan, build, verify) picks a backend
independently. Default mapping below is a starting point, not finalized —
see PLAN.md, Open items.

Resolution order (highest wins): env vars > config file > defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

BackendName = Literal["claude-code", "antigravity", "openrouter"]

AGENTFLOW_HOME = Path.home() / ".agentflow"
DEFAULT_CONFIG_PATH = "agentflow.config.yaml"

# Not finalized — see PLAN.md "Decide the default backend-per-role mapping".
DEFAULTS: dict[str, "RoleConfig"] = {}


PermissionMode = Literal["auto", "prompt", "deny"]


class NotificationConfig(BaseModel):
    enabled: bool = False
    email_to: str | None = None
    email_from: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_use_tls: bool = True
    notify_on: list[Literal["finished", "blocked"]] = Field(default_factory=lambda: ["finished"])
    base_url: str | None = None   # e.g. https://agentui.app.rem029.com — used to build a run link


class RoleConfig(BaseModel):
    backend: BackendName
    model: str | None = None


class Config(BaseModel):
    review: RoleConfig
    build: RoleConfig
    verify: RoleConfig
    max_iterations: int = 3
    permissions: PermissionMode = "auto"
    max_cost_usd: float | None = None
    notifications: NotificationConfig | None = None

    def roles(self) -> dict[str, RoleConfig]:
        return {"review": self.review, "build": self.build, "verify": self.verify}

    def backend_names(self) -> set[str]:
        return {role.backend for role in self.roles().values()}


DEFAULTS.update(
    review=RoleConfig(backend="claude-code"),
    build=RoleConfig(backend="antigravity"),
    verify=RoleConfig(backend="claude-code"),
)


def _from_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config at {path} must be a YAML mapping")
    return data


def _env_override(role: str, data: dict) -> dict:
    backend = os.environ.get(f"AGENTFLOW_{role.upper()}_BACKEND")
    model = os.environ.get(f"AGENTFLOW_{role.upper()}_MODEL")
    merged = dict(data)
    if backend:
        merged["backend"] = backend
    if model:
        merged["model"] = model
    return merged


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Config:
    file_data = _from_file(path)
    roles = {}
    for role, default in DEFAULTS.items():
        data = file_data.get(role, default.model_dump())
        roles[role] = _env_override(role, data)

    max_iterations = file_data.get("max_iterations")
    if os.environ.get("AGENTFLOW_MAX_ITERATIONS"):
        max_iterations = int(os.environ["AGENTFLOW_MAX_ITERATIONS"])
    if max_iterations is not None:
        roles["max_iterations"] = max_iterations

    permissions = file_data.get("permissions")
    if os.environ.get("AGENTFLOW_PERMISSIONS"):
        permissions = os.environ["AGENTFLOW_PERMISSIONS"]
    if permissions is not None:
        roles["permissions"] = permissions

    max_cost_usd = file_data.get("max_cost_usd")
    if os.environ.get("AGENTFLOW_MAX_COST_USD"):
        max_cost_usd = float(os.environ["AGENTFLOW_MAX_COST_USD"])
    if max_cost_usd is not None:
        roles["max_cost_usd"] = max_cost_usd

    notifications_data = file_data.get("notifications")
    if isinstance(notifications_data, dict):
        notif = NotificationConfig(**notifications_data)
    elif isinstance(notifications_data, NotificationConfig):
        notif = notifications_data
    else:
        notif = None

    if os.environ.get("AGENTFLOW_NOTIFICATIONS_ENABLED") in ("1", "true", "True"):
        if notif is None:
            notif = NotificationConfig(enabled=True)
        else:
            notif.enabled = True

    if notif is not None:
        roles["notifications"] = notif

    return Config(**roles)


def dump_config(
    config: Config,
    path: str,
) -> None:
    """Write a validated Config back to the agentflow YAML config.

    Callers must construct `config` via the Config model first (e.g. from
    web-form input) so invalid data never reaches disk.
    """
    data: dict = {
        "review": config.review.model_dump(exclude_none=True),
        "build": config.build.model_dump(exclude_none=True),
        "verify": config.verify.model_dump(exclude_none=True),
        "max_iterations": config.max_iterations,
        "permissions": config.permissions,
    }
    if config.max_cost_usd is not None:
        data["max_cost_usd"] = config.max_cost_usd
    if config.notifications is not None:
        data["notifications"] = config.notifications.model_dump(exclude_none=True)

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(data, sort_keys=False))
    output.chmod(0o600)

