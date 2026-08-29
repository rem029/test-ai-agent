"""Minimal .env file loader and writer without external dependencies."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env file and populate os.environ.

    Returns the dictionary of parsed pairs.
    Missing files return an empty dictionary silently without raising errors.
    """
    p = Path(path)
    if not p.is_file():
        return {}

    parsed: dict[str, str] = {}
    try:
        content = p.read_text(encoding="utf-8")
    except OSError:
        return {}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[6:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if not k:
            continue
        v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        parsed[k] = v
        if override or k not in os.environ:
            os.environ[k] = v

    return parsed


def load_env(cwd: str | Path = ".") -> None:
    """Load credentials from AGENTFLOW_HOME/.env first, then <cwd>/.env.

    Existing shell environment variables take precedence over all files.
    <cwd>/.env takes precedence over AGENTFLOW_HOME/.env.
    """
    from .config import AGENTFLOW_HOME

    initial_env = set(os.environ.keys())
    load_dotenv(AGENTFLOW_HOME / ".env")
    cwd_vars = load_dotenv(Path(cwd) / ".env")
    for k, v in cwd_vars.items():
        if k not in initial_env:
            os.environ[k] = v


def set_dotenv_var(key: str, value: str, path: str | Path = ".env") -> None:
    """Upsert a KEY=value line into a .env file and update os.environ.

    Preserves comments and existing lines in the file. Sets file mode to 0600.
    """
    p = Path(path)
    lines: list[str] = []
    found = False
    new_line = f"{key}={value}\n"

    if p.is_file():
        try:
            content = p.read_text(encoding="utf-8")
            raw_lines = content.splitlines(keepends=True)
            for line in raw_lines:
                stripped = line.strip()
                if not stripped.startswith("#"):
                    clean = stripped
                    if clean.startswith("export ") or clean.startswith("export\t"):
                        clean = clean[6:].strip()
                    if "=" in clean:
                        k = clean.split("=", 1)[0].strip()
                        if k == key:
                            lines.append(new_line)
                            found = True
                            continue
                lines.append(line)
        except OSError:
            lines = []

    if not found:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new_line)

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(lines), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass

    os.environ[key] = value
