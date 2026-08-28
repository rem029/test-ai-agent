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
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            cwd TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            title TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT,
            cwd TEXT NOT NULL,
            state_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
        )
        """
    )
    cursor = connection.execute("PRAGMA table_info(runs)")
    cols = [r[1] for r in cursor.fetchall()]
    if "session_id" not in cols:
        connection.execute("ALTER TABLE runs ADD COLUMN session_id TEXT")

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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            ts REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS runs_cwd_updated ON runs (cwd, updated_at DESC)")
    connection.execute("CREATE INDEX IF NOT EXISTS runs_session_id ON runs (session_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS sessions_cwd ON sessions (cwd, updated_at DESC)")
    connection.execute("CREATE INDEX IF NOT EXISTS tool_calls_run_id ON tool_calls(run_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS events_run_id_seq ON events (run_id, seq)")
    return connection


def create_session(
    session_id: str,
    cwd: str,
    title: str | None = None,
    metadata: dict | None = None,
    path: Path | None = None,
) -> None:
    """Create or update a session entry."""
    db_path = path or DEFAULT_DATABASE_PATH
    now = time.time()
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO sessions (session_id, cwd, created_at, updated_at, title, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (session_id, cwd, now, now, title, json.dumps(metadata or {})),
        )


def get_session(
    session_id: str, path: Path | None = None
) -> dict | None:
    """Retrieve session metadata by session ID."""
    db_path = path or DEFAULT_DATABASE_PATH
    if not db_path.exists():
        return None
    with _connection(db_path) as connection:
        row = connection.execute(
            """
            SELECT session_id, cwd, created_at, updated_at, title, metadata_json
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "session_id": row[0],
        "cwd": row[1],
        "created_at": row[2],
        "updated_at": row[3],
        "title": row[4],
        "metadata": json.loads(row[5]),
    }


def list_sessions(
    cwd: str, path: Path | None = None
) -> list[dict]:
    """List sessions for a repository, newest first."""
    db_path = path or DEFAULT_DATABASE_PATH
    if not db_path.exists():
        return []
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT session_id, cwd, created_at, updated_at, title, metadata_json
            FROM sessions
            WHERE cwd = ?
            ORDER BY updated_at DESC
            """,
            (cwd,),
        ).fetchall()
    return [
        {
            "session_id": r[0],
            "cwd": r[1],
            "created_at": r[2],
            "updated_at": r[3],
            "title": r[4],
            "metadata": json.loads(r[5]),
        }
        for r in rows
    ]


def get_session_runs(
    session_id: str, path: Path | None = None
) -> list[dict]:
    """Return all run states associated with a session, oldest first."""
    db_path = path or DEFAULT_DATABASE_PATH
    if not db_path.exists():
        return []
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT state_json
            FROM runs
            WHERE session_id = ?
            ORDER BY updated_at ASC
            """,
            (session_id,),
        ).fetchall()
    return [json.loads(r[0]) for r in rows]


def append_event(
    run_id: str,
    seq: int,
    event_type: str,
    payload: dict,
    ts: float | None = None,
    path: Path | None = None,
) -> None:
    """Append a single event to the persistent event log."""
    db_path = path or DEFAULT_DATABASE_PATH
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO events (run_id, seq, type, payload_json, ts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                event_type,
                json.dumps(payload),
                ts if ts is not None else time.time(),
            ),
        )


def list_events(
    run_id: str, path: Path | None = None
) -> list[dict]:
    """Return all events for a run in chronological order."""
    db_path = path or DEFAULT_DATABASE_PATH
    if not db_path.exists():
        return []
    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT seq, type, payload_json, ts
            FROM events
            WHERE run_id = ?
            ORDER BY seq ASC
            """,
            (run_id,),
        ).fetchall()
    return [
        {
            "seq": r[0],
            "type": r[1],
            "payload": json.loads(r[2]),
            "ts": r[3],
        }
        for r in rows
    ]


def reconstruct_run(
    run_id: str, path: Path | None = None
) -> dict | None:
    """Reconstruct a run state dict from its event log."""
    events = list_events(run_id, path=path)
    if not events:
        return None

    state: dict = {
        "run_id": run_id,
        "session_id": None,
        "goal": "",
        "started_at": 0.0,
        "config": {},
        "steps": [],
        "tool_calls": [],
        "finished_at": None,
        "pushed": None,
    }

    for ev in events:
        etype = ev["type"]
        payload = ev["payload"]
        if etype == "run_started":
            state["run_id"] = payload.get("run_id", run_id)
            state["session_id"] = payload.get("session_id")
            state["goal"] = payload.get("goal", "")
            state["started_at"] = payload.get("started_at", ev["ts"])
            state["config"] = payload.get("config", {})
        elif etype == "tool_result":
            state["tool_calls"].append(
                {
                    "step_index": payload.get("step_index", -1),
                    "tool_name": payload.get("tool_name", ""),
                    "args": payload.get("args", {}),
                    "result": payload.get("result", {}),
                    "status": payload.get("status", "unknown"),
                    "execution_time_ms": payload.get("execution_time_ms", 0),
                    "error": payload.get("error"),
                }
            )
        elif etype == "step_finished":
            step = payload.get("step")
            if step:
                state["steps"].append(step)
        elif etype == "run_finished":
            state["finished_at"] = payload.get("finished_at", ev["ts"])
            state["pushed"] = payload.get("pushed")

    return state


def save_run(
    state: RunState, cwd: str, path: Path | None = None
) -> Path:
    """Upsert a workflow state so live web polling reads the latest snapshot."""
    db_path = path or DEFAULT_DATABASE_PATH
    session_id = getattr(state, "session_id", None)
    now = time.time()
    with _connection(db_path) as connection:
        if session_id:
            connection.execute(
                """
                INSERT INTO sessions (session_id, cwd, created_at, updated_at, title)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (session_id, cwd, state.started_at, now, state.goal),
            )

        connection.execute(
            """
            INSERT INTO runs (run_id, session_id, cwd, state_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                session_id = excluded.session_id,
                cwd = excluded.cwd,
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (
                state.run_id,
                session_id,
                cwd,
                json.dumps(state.to_dict()),
                now,
            ),
        )
        existing_calls_count = connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE run_id = ?", (state.run_id,)
        ).fetchone()[0]
        new_calls = state.tool_calls[existing_calls_count:]
        for tc in new_calls:
            connection.execute(
                """
                INSERT INTO tool_calls (
                    run_id, step_index, tool_name, args_json, result_json,
                    status, execution_time_ms, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.run_id,
                    tc.get("step_index", 0),
                    tc.get("tool_name", ""),
                    json.dumps(tc.get("args", {})),
                    json.dumps(tc.get("result", {})),
                    tc.get("status", "unknown"),
                    tc.get("execution_time_ms", 0),
                    tc.get("error"),
                    time.time(),
                ),
            )
    return db_path


def list_runs(cwd: str, path: Path | None = None) -> list[dict]:
    """Return saved runs for one target repository, newest first."""
    db_path = path or DEFAULT_DATABASE_PATH
    if not db_path.exists():
        return []
    with _connection(db_path) as connection:
        rows = connection.execute(
            "SELECT state_json FROM runs WHERE cwd = ? ORDER BY updated_at DESC", (cwd,)
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def load_run(run_id: str, cwd: str, path: Path | None = None) -> dict | None:
    """Return a saved run for one target repository."""
    db_path = path or DEFAULT_DATABASE_PATH
    if not db_path.exists():
        return None
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT state_json FROM runs WHERE run_id = ? AND cwd = ?", (run_id, cwd)
        ).fetchone()
    return json.loads(row[0]) if row else None


def get_tool_calls(
    run_id: str, cwd: str, path: Path | None = None
) -> list[dict]:
    """Return tool calls for a saved run, newest first."""
    db_path = path or DEFAULT_DATABASE_PATH
    if not db_path.exists():
        return []
    with _connection(db_path) as connection:
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
