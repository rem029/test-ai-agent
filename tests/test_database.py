"""Tests for SQLite-backed workflow run persistence."""

from __future__ import annotations

from agentflow.database import count_runs, get_tool_calls, list_runs, load_run, save_run
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


def test_tool_calls_are_persisted_to_dedicated_table(tmp_path):
    database_path = tmp_path / "agentflow.db"
    state = _state("run-with-tools", "use tools")
    state.add_tool_call(
        step_index=0,
        tool_name="ReadFile",
        args={"path": "foo.py"},
        result={"success": True, "output": "hello", "duration_ms": 5},
    )
    save_run(state, "/project", database_path)

    loaded = load_run("run-with-tools", "/project", database_path)
    assert len(loaded["tool_calls"]) == 1
    assert loaded["tool_calls"][0]["tool_name"] == "ReadFile"

    calls = get_tool_calls("run-with-tools", "/project", database_path)
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "ReadFile"
    assert calls[0]["status"] == "success"


def test_real_database_untouched_during_workflow(tmp_path, monkeypatch):
    """Regression test: run_workflow without explicit database_path uses isolated DEFAULT_DATABASE_PATH and does not touch real DB."""
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    import agentflow.database
    from agentflow.backends.base import RunResult, Usage
    from agentflow.config import Config, RoleConfig
    from agentflow.orchestrator import run_workflow

    isolated_target_db = tmp_path / "isolated_target" / "agentflow.db"
    monkeypatch.setattr(agentflow.database, "DEFAULT_DATABASE_PATH", isolated_target_db)

    assert not isolated_target_db.exists()

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )
    backend = MagicMock()
    backend.run.return_value = RunResult(
        success=True,
        text="Plan: simple. VERIFY_RESULT: PASS",
        usage=Usage("mock", "m", 1, 1, 0.0),
        raw={},
    )

    real_db = Path.home() / ".agentflow" / "agentflow.db"
    mtime_before = real_db.stat().st_mtime if real_db.exists() else None
    size_before = real_db.stat().st_size if real_db.exists() else None

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                # Call run_workflow without passing database_path
                run_workflow("test goal without explicit db_path", config, str(tmp_path))

    # Assert isolated_target_db was created and written to
    assert isolated_target_db.exists()

    # Assert real ~/.agentflow/agentflow.db was not modified
    if real_db.exists():
        assert real_db.stat().st_mtime == mtime_before
        assert real_db.stat().st_size == size_before


def test_list_runs_pagination_and_count_runs(tmp_path):
    database_path = tmp_path / "agentflow.db"
    cwd = "/projects/pagination"

    # Non-existent DB count_runs returns 0
    assert count_runs(cwd, database_path) == 0

    # Seed 5 runs with distinct goals
    for i in range(5):
        save_run(_state(f"run-{i}", f"goal {i}"), cwd, database_path)

    assert count_runs(cwd, database_path) == 5
    assert count_runs("/other/project", database_path) == 0

    # All runs without limit
    all_runs = list_runs(cwd, database_path)
    assert len(all_runs) == 5

    # Pagination: limit=2, offset=1
    paginated = list_runs(cwd, database_path, limit=2, offset=1)
    assert len(paginated) == 2
    assert paginated[0]["run_id"] == all_runs[1]["run_id"]
    assert paginated[1]["run_id"] == all_runs[2]["run_id"]

    # Offset at edge
    assert len(list_runs(cwd, database_path, limit=2, offset=5)) == 0


def test_reconstruct_run_with_blockers(tmp_path):
    from agentflow.database import append_event, reconstruct_run
    db = tmp_path / "agentflow.db"
    append_event("run-blk", 1, "run_started", {"run_id": "run-blk", "goal": "test"}, path=db)
    append_event("run-blk", 2, "blocker", {"reason": "budget", "detail": "cost limit", "fatal": True, "step_index": None, "ts": 10.0}, path=db)
    append_event("run-blk", 3, "blocker", {"reason": "permission", "detail": "denied", "fatal": False, "step_index": 1, "ts": 11.0}, path=db)
    append_event("run-blk", 4, "run_finished", {"finished_at": 12.0, "pushed": None}, path=db)

    reconstructed = reconstruct_run("run-blk", path=db)
    assert reconstructed is not None
    assert "blockers" in reconstructed
    assert len(reconstructed["blockers"]) == 2
    assert reconstructed["blockers"][0]["reason"] == "budget"
    assert reconstructed["blockers"][0]["fatal"] is True
    assert reconstructed["blockers"][1]["reason"] == "permission"
    assert reconstructed["blockers"][1]["fatal"] is False

