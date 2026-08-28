"""Persistent instruction/fact store injected into agent prompts (global + per-project)."""

from __future__ import annotations

import hashlib
from pathlib import Path

MAX_MEMORY_CHARS = 8000


def _home() -> Path:
    from .config import AGENTFLOW_HOME  # fresh each call so tests can monkeypatch

    return AGENTFLOW_HOME


def global_memory_path() -> Path:
    return _home() / "memory.md"


def project_memory_path(cwd: str) -> Path:
    digest = hashlib.sha256(str(Path(cwd).resolve()).encode("utf-8")).hexdigest()[:16]
    return _home() / "projects" / digest / "memory.md"


def _read(p: Path) -> str:
    try:
        if not p.exists() or not p.is_file():
            return ""
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text[:MAX_MEMORY_CHARS], encoding="utf-8")
    p.chmod(0o600)


def read_global_memory() -> str:
    return _read(global_memory_path())


def write_global_memory(text: str) -> None:
    _write(global_memory_path(), text)


def read_project_memory(cwd: str) -> str:
    return _read(project_memory_path(cwd))


def write_project_memory(cwd: str, text: str) -> None:
    _write(project_memory_path(cwd), text)


def compose_memory(cwd: str) -> str:
    """Combined block for prompt injection. '' when both are empty."""
    global_raw = read_global_memory()
    project_raw = read_project_memory(cwd)

    global_text = global_raw[:MAX_MEMORY_CHARS].rstrip()
    project_text = project_raw[:MAX_MEMORY_CHARS].rstrip()

    has_global = bool(global_text.strip())
    has_project = bool(project_text.strip())

    if not has_global and not has_project:
        return ""

    sections: list[str] = ["## Standing instructions & project memory"]
    if has_global:
        sections.append(f"### Global\n{global_text}")
    if has_project:
        sections.append(f"### This project\n{project_text}")

    return "\n\n".join(sections).rstrip()
