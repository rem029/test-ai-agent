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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            tool_name TEXT NOT NULL,
            args_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            status TEXT NOT NULL,
            execution_time_ms INTEGER NOT NULL,
            error TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS runs_cwd_updated ON runs (cwd, updated_at DESC)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS tool_calls_run_id ON tool_calls(run_id)"
    )
    return connection


def save_run(
    state: RunState, cwd: str, path: Path | None = DEFAULT_DATABASE_PATH
) -> Path:
    """Upsert a workflow state so live web polling reads the latest snapshot."""
    db_path = path or DEFAULT_DATABASE_PATH
    with _connection(db_path) as connection:
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
        # Persist tool calls to a dedicated table for querying/auditing.
        connection.execute(
            "DELETE FROM tool_calls WHERE run_id = ?",
            (state.run_id,),
        )
        for call in state.tool_calls:
            connection.execute(
                """
                INSERT INTO tool_calls
                    (run_id, step_index, tool_name, args_json, result_json,
                     status, execution_time_ms, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.run_id,
                    call.get("step_index", -1),
                    call.get("tool_name", ""),
                    json.dumps(call.get("args", {})),
                    json.dumps(call.get("result", {})),
                    call.get("status", "unknown"),
                    call.get("execution_time_ms", 0),
                    call.get("error"),
                    time.time(),
                ),
            )
    return db_path


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


def get_tool_calls(
    run_id: str, cwd: str, path: Path = DEFAULT_DATABASE_PATH
) -> list[dict]:
    """Return tool calls for a saved run, newest first."""
    if not path.exists():
        return []
    with _connection(path) as connection:
        rows = connection.execute(
            """
            SELECT step_index, tool_name, args_json, result_json, status,
                   execution_time_ms, error
            FROM tool_calls
            WHERE run_id = ?
            ORDER BY id DESC
            """,
            (run_id,),
        ).fetchall()
    return [
        {
            "step_index": r[0],
            "tool_name": r[1],
            "args": json.loads(r[2]),
            "result": json.loads(r[3]),
            "status": r[4],
            "execution_time_ms": r[5],
            "error": r[6],
        }
        for r in rows
    ]
