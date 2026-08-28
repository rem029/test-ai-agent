"""Tests for budget guardrail: aborting workflow when cumulative cost exceeds max_cost_usd."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.cli import main as cli_main
from agentflow.config import Config, RoleConfig
from agentflow.orchestrator import _check_budget, run_workflow, RunState


def _make_backend_with_cost(cost_per_call: float, texts: list[str]):
    backend = MagicMock()
    responses = [
        RunResult(success=True, text=t, usage=Usage("mock", "model", 100, 100, cost_per_call), raw={})
        for t in texts
    ]
    backend.run.side_effect = responses
    return backend


def test_check_budget_helper():
    state = RunState(run_id="test", goal="test", started_at=100.0, config={})
    # No limit set
    ok, err = _check_budget(state, None)
    assert ok is True
    assert err is None

    # Within budget
    ok, err = _check_budget(state, 1.0)
    assert ok is True

    # Over budget
    state.steps.append({
        "role": "review",
        "mode": "read",
        "iteration": 0,
        "success": True,
        "text": "plan",
        "usage": {"backend": "mock", "model": "m", "input_tokens": 100, "output_tokens": 100, "cost_usd": 1.5},
    })
    ok, err = _check_budget(state, 1.0)
    assert ok is False
    assert "exceeded" in err


def test_workflow_aborts_when_cost_exceeds_budget(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_cost_usd=0.05,
    )

    # Review costs 0.10, which exceeds 0.05 budget
    backend = _make_backend_with_cost(0.10, ["Plan: expensive task."])

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("expensive goal", config, str(repo), database_path=db_path)

    assert state.finished_at is not None
    assert state.pushed is None
    # Build and verify should not have executed
    roles_executed = [s["role"] for s in state.steps]
    assert roles_executed == ["review"]
    assert state.total_cost() >= 0.10


def test_workflow_runs_within_budget(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_cost_usd=1.00,
    )

    # All calls cost 0.01 each, total ~0.03, well within 1.00
    texts = [
        "Plan: cheap task.",
        "Done.\nVERIFY_RESULT: PASS",
        "VERIFY_RESULT: PASS",
    ]
    backend = _make_backend_with_cost(0.01, texts)

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("cheap goal", config, str(repo), database_path=db_path)

    assert state.finished_at is not None
    assert state.pushed == {"pushed": True}
    assert len(state.steps) == 3


def test_cli_max_cost_usd_flag():
    with patch("agentflow.cli.run_workflow") as mock_run:
        mock_run.return_value = MagicMock(pushed={"pushed": True})
        rc = cli_main(["--max-cost-usd", "0.50", "budget goal"])
        assert rc == 0
        called_config = mock_run.call_args[0][1]
        assert called_config.max_cost_usd == 0.50
