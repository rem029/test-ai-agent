"""Tests for memory injection into review, build, and verify prompts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.database import list_events
from agentflow.memory import write_global_memory, write_project_memory
from agentflow.orchestrator import (
    BUILD_PROMPT,
    REVIEW_PROMPT,
    VERIFY_PROMPT,
    run_workflow,
)


@pytest.fixture
def cwd(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return str(path)


def _config() -> Config:
    return Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )


def test_memory_injected_into_all_step_prompts_and_logs_event(cwd, tmp_path):
    write_global_memory("Global standing instruction: ALWAYS_TEST")
    write_project_memory(cwd, "Project standing instruction: REPO_CONVENTION")

    captured_prompts: list[str] = []

    def fake_run(messages, cwd=None, mode=None):
        prompt = messages[0].content
        captured_prompts.append(prompt)
        if mode == "read":
            text = "1. Plan step"
        elif mode == "write":
            text = "Built step"
        elif mode == "verify":
            text = "VERIFY_RESULT: PASS"
        else:
            text = "ok"
        return RunResult(success=True, text=text, usage=Usage("mock", "model", 1, 1, 0.0), raw={})

    mock_backend = MagicMock()
    mock_backend.name = "mock"
    mock_backend.model = "mock-model"
    mock_backend.run.side_effect = fake_run

    db_path = tmp_path / "test_memory.db"
    with patch("agentflow.orchestrator._build_backend", return_value=mock_backend):
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            state = run_workflow("add a memory feature", _config(), cwd, database_path=db_path)

    assert len(captured_prompts) == 3
    review_prompt, build_prompt, verify_prompt = captured_prompts

    # All three prompts must contain the memory block
    for p in (review_prompt, build_prompt, verify_prompt):
        assert "## Standing instructions & project memory" in p
        assert "### Global\nGlobal standing instruction: ALWAYS_TEST" in p
        assert "### This project\nProject standing instruction: REPO_CONVENTION" in p

    # Verify event logged
    events = list_events(state.run_id, path=db_path)
    mem_events = [e for e in events if e["type"] == "memory_injected"]
    assert len(mem_events) == 1
    assert mem_events[0]["payload"]["has_global"] is True
    assert mem_events[0]["payload"]["has_project"] is True
    assert mem_events[0]["payload"]["chars"] > 0


def test_no_memory_leaves_prompts_clean_and_no_event(cwd, tmp_path):
    captured_prompts: list[str] = []

    def fake_run(messages, cwd=None, mode=None):
        prompt = messages[0].content
        captured_prompts.append(prompt)
        if mode == "read":
            text = "1. Plan step"
        elif mode == "write":
            text = "Built step"
        elif mode == "verify":
            text = "VERIFY_RESULT: PASS"
        else:
            text = "ok"
        return RunResult(success=True, text=text, usage=Usage("mock", "model", 1, 1, 0.0), raw={})

    mock_backend = MagicMock()
    mock_backend.name = "mock"
    mock_backend.model = "mock-model"
    mock_backend.run.side_effect = fake_run

    db_path = tmp_path / "test_no_memory.db"
    goal = "add a clean feature"
    with patch("agentflow.orchestrator._build_backend", return_value=mock_backend):
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            state = run_workflow(goal, _config(), cwd, database_path=db_path)

    assert len(captured_prompts) == 3
    review_prompt, build_prompt, verify_prompt = captured_prompts

    # No memory text
    for p in (review_prompt, build_prompt, verify_prompt):
        assert "## Standing instructions & project memory" not in p

    # Confirm one-shot review prompt starts with exact REVIEW_PROMPT
    expected_review_base = REVIEW_PROMPT.format(goal=goal)
    assert review_prompt.startswith(expected_review_base)

    expected_verify_base = VERIFY_PROMPT.format(goal=goal, plan="1. Plan step")
    assert verify_prompt.startswith(expected_verify_base)

    # Verify event NOT logged
    events = list_events(state.run_id, path=db_path)
    mem_events = [e for e in events if e["type"] == "memory_injected"]
    assert len(mem_events) == 0


def test_memory_layering_order_outermost(cwd, tmp_path):
    write_global_memory("GLOBAL_MEM")
    write_project_memory(cwd, "PROJECT_MEM")

    captured_prompts: list[str] = []

    def fake_run(messages, cwd=None, mode=None):
        prompt = messages[0].content
        captured_prompts.append(prompt)
        if mode == "read":
            text = "1. Plan"
        elif mode == "write":
            text = "Built"
        elif mode == "verify":
            text = "VERIFY_RESULT: PASS"
        else:
            text = "ok"
        return RunResult(success=True, text=text, usage=Usage("mock", "model", 1, 1, 0.0), raw={})

    mock_backend = MagicMock()
    mock_backend.name = "mock"
    mock_backend.model = "mock-model"
    mock_backend.run.side_effect = fake_run

    db_path = tmp_path / "test_layering.db"

    # Seed a prior run in this session
    from agentflow.database import save_run
    from agentflow.orchestrator import RunState
    prior_state = RunState(
        run_id="run-prior-1",
        session_id="session-layering-1",
        goal="prior goal",
        started_at=1.0,
        config={},
        steps=[{"role": "build", "text": "prior build", "usage": {}}],
    )
    save_run(prior_state, cwd, db_path)

    with patch("agentflow.orchestrator._build_backend", return_value=mock_backend):
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            run_workflow(
                "second turn goal",
                _config(),
                cwd,
                session_id="session-layering-1",
                database_path=db_path,
            )

    review_prompt = captured_prompts[0]
    # Check ordering in review_prompt:
    # Memory block -> Prior runs summary -> Goal/Role Prompt
    pos_memory = review_prompt.index("## Standing instructions & project memory")
    pos_prior = review_prompt.index("Prior runs in this session")
    pos_goal = review_prompt.index("Goal: second turn goal")

    assert pos_memory < pos_prior < pos_goal
