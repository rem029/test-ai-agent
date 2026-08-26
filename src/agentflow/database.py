"""SQLite persistence for agentflow run state."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import AGENTFLOW_HOME

if TYPE_CHECKING:
    from .orchestrator import RunState

DEFAULT_DATABASE_PATH = AGENTFLOW_HOME / "agentflow.db"


def _connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            cwd TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS runs_cwd_updated ON runs (cwd, updated_at DESC)")
    return connection


def save_run(state: RunState, cwd: str, path: Path = DEFAULT_DATABASE_PATH) -> Path:
    """Upsert a workflow state so live web polling reads the latest snapshot."""
    with _connection(path) as connection:
        connection.execute(
            """
            INSERT INTO runs (run_id, cwd, state_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                cwd = excluded.cwd,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (state.run_id, cwd, json.dumps(state.to_dict()), time.time()),
        )
    return path


def list_runs(cwd: str, path: Path = DEFAULT_DATABASE_PATH) -> list[dict]:
    """Return saved runs for one target repository, newest first."""
    if not path.exists():
        return []
    with _connection(path) as connection:
        rows = connection.execute(
            "SELECT state_json FROM runs WHERE cwd = ? ORDER BY updated_at DESC", (cwd,)
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def load_run(run_id: str, cwd: str, path: Path = DEFAULT_DATABASE_PATH) -> dict | None:
    """Return a saved run for one target repository."""
    if not path.exists():
        return None
    with _connection(path) as connection:
        row = connection.execute(
            "SELECT state_json FROM runs WHERE run_id = ? AND cwd = ?", (run_id, cwd)
        ).fetchone()
    return json.loads(row[0]) if row else None
