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
from pydantic import BaseModel

BackendName = Literal["claude-code", "antigravity", "openrouter"]

DEFAULT_CONFIG_PATH = "agentflow.config.yaml"

# Not finalized — see PLAN.md "Decide the default backend-per-role mapping".
DEFAULTS: dict[str, "RoleConfig"] = {}


class RoleConfig(BaseModel):
    backend: BackendName
    model: str | None = None


class Config(BaseModel):
    review: RoleConfig
    build: RoleConfig
    verify: RoleConfig

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
        return yaml.safe_load(f) or {}


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
    return Config(**roles)
