"""Backend-per-role configuration.

Each workflow role (review/plan, build, verify) picks a backend
independently. Default mapping below is a starting point, not finalized —
see PLAN.md, Open items.

Resolution order (highest wins): env vars > config file > defaults.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

BackendName = Literal["claude-code", "antigravity", "openrouter"]

AGENTFLOW_HOME = Path.home() / ".agentflow"
DEFAULT_CONFIG_PATH = "agentflow.config.yaml"

_ACTIVE_CONFIG_PATH: str | None = None


def active_config_path() -> str | None:
    return _ACTIVE_CONFIG_PATH

# Not finalized — see PLAN.md "Decide the default backend-per-role mapping".
DEFAULTS: dict[str, "RoleConfig"] = {}


PermissionMode = Literal["auto", "prompt", "deny"]
WorkflowMode = Literal["auto", "review_only", "full"]


_MCP_SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class MCPServerConfig(BaseModel):
    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    enabled: bool = True
    auto_approve: list[str] = Field(default_factory=list)   # tool names always allowed; the single-element list ["all"] means every tool on this server

    @model_validator(mode="after")
    def _validate_server(self) -> MCPServerConfig:
        if not self.name or not _MCP_SERVER_NAME_PATTERN.match(self.name):
            raise ValueError(
                f"MCPServerConfig name must be non-empty and match ^[A-Za-z0-9_-]+$, got {self.name!r}"
            )
        has_command = self.command is not None and self.command != ""
        has_url = self.url is not None and self.url != ""
        if (has_command and has_url) or (not has_command and not has_url):
            raise ValueError(
                "MCPServerConfig must specify exactly one of 'command' or 'url'"
            )
        return self


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


class CredentialsConfig(BaseModel):
    openrouter_api_key: str | None = None
    smtp_password: str | None = None


class RoleConfig(BaseModel):
    backend: BackendName
    model: str | None = None


class Config(BaseModel):
    review: RoleConfig
    build: RoleConfig
    verify: RoleConfig
    max_iterations: int = 3
    max_requirements_rounds: int = 3
    build_review: bool = False
    permissions: PermissionMode = "auto"
    workflow_mode: WorkflowMode = "auto"
    max_cost_usd: float | None = None
    notifications: NotificationConfig | None = None
    credentials: CredentialsConfig | None = None
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)

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
    global _ACTIVE_CONFIG_PATH
    _ACTIVE_CONFIG_PATH = str(path)
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

    workflow_mode = file_data.get("workflow_mode")
    if os.environ.get("AGENTFLOW_WORKFLOW_MODE"):
        workflow_mode = os.environ["AGENTFLOW_WORKFLOW_MODE"]
    if workflow_mode is not None:
        roles["workflow_mode"] = workflow_mode

    max_cost_usd = file_data.get("max_cost_usd")
    if os.environ.get("AGENTFLOW_MAX_COST_USD"):
        max_cost_usd = float(os.environ["AGENTFLOW_MAX_COST_USD"])
    if max_cost_usd is not None:
        roles["max_cost_usd"] = max_cost_usd

    max_requirements_rounds = file_data.get("max_requirements_rounds")
    if os.environ.get("AGENTFLOW_MAX_REQUIREMENTS_ROUNDS"):
        max_requirements_rounds = int(os.environ["AGENTFLOW_MAX_REQUIREMENTS_ROUNDS"])
    if max_requirements_rounds is not None:
        roles["max_requirements_rounds"] = max_requirements_rounds

    build_review = file_data.get("build_review")
    if os.environ.get("AGENTFLOW_BUILD_REVIEW") in ("1", "true", "True", "0", "false", "False"):
        build_review = os.environ["AGENTFLOW_BUILD_REVIEW"].lower() in ("1", "true")
    if build_review is not None:
        roles["build_review"] = build_review

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

    credentials_data = file_data.get("credentials")
    if isinstance(credentials_data, dict):
        creds = CredentialsConfig(**credentials_data)
    elif isinstance(credentials_data, CredentialsConfig):
        creds = credentials_data
    else:
        creds = None

    if creds is not None:
        roles["credentials"] = creds

    mcp_servers_data = file_data.get("mcp_servers")
    mcp_servers: list[MCPServerConfig] = []
    if isinstance(mcp_servers_data, list):
        for item in mcp_servers_data:
            if isinstance(item, dict):
                mcp_servers.append(MCPServerConfig(**item))
            elif isinstance(item, MCPServerConfig):
                mcp_servers.append(item)

    if os.environ.get("AGENTFLOW_MCP_DISABLED") in ("1", "true", "True"):
        for s in mcp_servers:
            s.enabled = False

    roles["mcp_servers"] = mcp_servers

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
        "max_requirements_rounds": config.max_requirements_rounds,
        "build_review": config.build_review,
        "permissions": config.permissions,
        "workflow_mode": config.workflow_mode,
    }
    if config.max_cost_usd is not None:
        data["max_cost_usd"] = config.max_cost_usd
    if config.notifications is not None:
        data["notifications"] = config.notifications.model_dump(exclude_none=True)
    if config.credentials is not None:
        creds_dump = config.credentials.model_dump(exclude_none=True)
        if creds_dump:
            data["credentials"] = creds_dump
    if config.mcp_servers:
        data["mcp_servers"] = [s.model_dump(exclude_none=True) for s in config.mcp_servers]

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(data, sort_keys=False))
    output.chmod(0o600)

