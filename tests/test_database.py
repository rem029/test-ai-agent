"""Tests for SQLite-backed workflow run persistence."""

from __future__ import annotations

from agentflow.database import list_runs, load_run, save_run
from agentflow.orchestrator import RunState


def _state(run_id: str, goal: str) -> RunState:
    return RunState(
        run_id=run_id,
        goal=goal,
        started_at=1.0,
        config={},
        finished_at=2.0,
    )


def test_runs_are_persisted_and_scoped_to_repository(tmp_path):
    database_path = tmp_path / "agentflow.db"
    first = _state("first", "first goal")
    second = _state("second", "second goal")
    save_run(first, "/projects/one", database_path)
    save_run(second, "/projects/two", database_path)

    assert load_run("first", "/projects/one", database_path)["goal"] == "first goal"
    assert load_run("first", "/projects/two", database_path) is None
    assert [run["run_id"] for run in list_runs("/projects/one", database_path)] == ["first"]


def test_saving_a_run_updates_its_snapshot(tmp_path):
    database_path = tmp_path / "agentflow.db"
    state = _state("run-id", "original goal")
    save_run(state, "/project", database_path)
    state.goal = "updated goal"
    save_run(state, "/project", database_path)

    assert load_run("run-id", "/project", database_path)["goal"] == "updated goal"
