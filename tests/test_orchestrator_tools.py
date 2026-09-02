"""Tests for the orchestrator tool loop."""

from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.orchestrator import run_workflow
from agentflow.tools import ToolResult


@pytest.fixture
def cwd(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return str(path)


def _make_backend_responses(texts: list[str]):
    """Return a mock backend whose run() returns texts in order."""
    backend = MagicMock()
    responses = [
        RunResult(success=True, text=t, usage=Usage("mock", "model", 1, 1, 0.0), raw={})
        for t in texts
    ]
    backend.run.side_effect = responses
    return backend


def test_tool_loop_executes_requested_tool(cwd):
    (Path(cwd) / "src").mkdir()
    (Path(cwd) / "src" / "foo.py").write_text("hello")

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    review_text = """
1. Read the file.
<tool_call>
  <ReadFile>
    <args>
      <path>src/foo.py</path>
    </args>
  </ReadFile>
</tool_call>
"""
    review_final = "Plan: read the file."
    build_text = "Built the changes. VERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text, review_final, build_text, verify_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    assert state.steps[0]["role"] == "review"
    assert len(state.tool_calls) >= 1
    assert state.tool_calls[0]["tool_name"] == "ReadFile"
    assert state.tool_calls[0]["args"] == {"path": "src/foo.py"}
    assert state.tool_calls[0]["status"] == "success"
    assert "hello" in state.tool_calls[0]["result"]["output"]


def test_tool_loop_respects_max_calls(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_read_tool_calls=10,
    )

    # Always request a different tool call each time, never produce a final answer.
    review_texts = [
        f"""
<tool_call>
  <ReadFile>
    <args><path>src/foo_{i}.py</path></args>
  </ReadFile>
</tool_call>
"""
        for i in range(20)
    ]

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses(review_texts)
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    # The review step should fail after max tool calls.
    assert state.steps[0]["role"] == "review"
    assert state.steps[0]["success"] is False
    assert "maximum" in state.steps[0]["text"].lower()


def test_no_tool_requests_skips_loop(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    review_text = "Plan: do nothing."
    build_text = "Done. VERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text, build_text, verify_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    assert state.steps[0]["success"] is True
    assert len(state.tool_calls) == 0


def test_unparsed_tool_call_retries_and_executes(cwd):
    (Path(cwd) / "src").mkdir()
    (Path(cwd) / "src" / "foo.py").write_text("hello world")

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    unparsed_text = """<｜DSML｜tool_call>
<｜DSML｜invoke>123notaname</｜DSML｜invoke>
</｜DSML｜tool_call>"""
    proper_tool_text = """<tool_call>
{"name": "ReadFile", "args": {"path": "src/foo.py"}}
</tool_call>"""
    review_final = "Plan: read the file."
    build_text = "Built the changes. VERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([
            unparsed_text,
            proper_tool_text,
            review_final,
            build_text,
            verify_text,
        ])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    assert len(state.tool_calls) >= 1
    assert state.tool_calls[0]["tool_name"] == "ReadFile"
    assert state.tool_calls[0]["args"] == {"path": "src/foo.py"}
    assert "hello world" in state.tool_calls[0]["result"]["output"]

    # Verify tool_parse_failed event was logged
    from agentflow.database import list_events
    events = list_events(state.run_id, path=Path(cwd) / "test.db")
    parse_failed_events = [e for e in events if e.get("type") == "tool_parse_failed"]
    assert len(parse_failed_events) == 1
    assert parse_failed_events[0]["payload"]["step_index"] == 0
    assert "123notaname" in parse_failed_events[0]["payload"]["snippet"]


def test_tool_loop_executes_format_b(cwd):
    (Path(cwd) / "src").mkdir()
    (Path(cwd) / "src" / "foo.py").write_text("format b content")

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    format_b_text = """<｜DSML｜tool_call>
<｜DSML｜invoke>ReadFile</｜DSML｜invoke>
<｜DSML｜invoke>{"path": "src/foo.py"}</｜DSML｜invoke>
</｜DSML｜tool_call>"""
    review_final = "Plan: format b read."
    build_text = "Built the changes. VERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([
            format_b_text,
            review_final,
            build_text,
            verify_text,
        ])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    assert len(state.tool_calls) >= 1
    assert state.tool_calls[0]["tool_name"] == "ReadFile"
    assert state.tool_calls[0]["args"] == {"path": "src/foo.py"}
    assert "format b content" in state.tool_calls[0]["result"]["output"]


def test_unparsed_tool_call_retry_limit_reached(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
    )

    unparsed_text = """<｜DSML｜tool_call>
<｜DSML｜invoke>123notaname</｜DSML｜invoke>
</｜DSML｜tool_call>"""

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = MagicMock()
        backend.run.return_value = RunResult(
            success=True, text=unparsed_text, usage=Usage("mock", "model", 1, 1, 0.0), raw={}
        )
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, cwd, database_path=Path(cwd) / "test.db")

    from agentflow.database import list_events
    events = list_events(state.run_id, path=Path(cwd) / "test.db")
    parse_failed_events = [e for e in events if e.get("type") == "tool_parse_failed"]
    # Up to 2 retries allowed per step before returning result as-is
    step0_events = [e for e in parse_failed_events if e["payload"]["step_index"] == 0]
    assert len(step0_events) == 2
    assert len(parse_failed_events) == 14


def test_review_only_workflow_marker_no(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        workflow_mode="auto",
    )

    review_text = "Here is my detailed analysis of the dragtask feature.\n\nBUILD_NEEDED: no"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        state = run_workflow("can you review @.test/dragtask, give me your feedback", config, cwd, database_path=Path(cwd) / "test.db")

    assert len(state.steps) == 1
    assert state.steps[0]["role"] == "review"
    assert state.steps[0]["success"] is True
    assert "BUILD_NEEDED" not in state.steps[0]["text"]
    assert "Here is my detailed analysis" in state.steps[0]["text"]
    assert state.finished_at is not None

    from agentflow.database import list_events
    events = list_events(state.run_id, path=Path(cwd) / "test.db")
    finish_events = [e for e in events if e.get("type") == "run_finished"]
    assert len(finish_events) == 1
    assert finish_events[0]["payload"]["mode"] == "review_only"


def test_review_only_workflow_forced_by_mode(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        workflow_mode="review_only",
    )

    review_text = "Plan to build a massive system.\n\nBUILD_NEEDED: yes"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        state = run_workflow("build a login page", config, cwd, database_path=Path(cwd) / "test.db")

    # Only 1 step ran because workflow_mode is review_only
    assert len(state.steps) == 1
    assert state.steps[0]["role"] == "review"
    assert state.finished_at is not None


def test_full_workflow_mode_overrides_marker_no(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        workflow_mode="full",
    )

    review_text = "Analysis.\n\nBUILD_NEEDED: no"
    build_text = "Building.\nVERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text, build_text, verify_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("review the code", config, cwd, database_path=Path(cwd) / "test.db")

    # Ran all 3 steps because workflow_mode is full
    assert len(state.steps) == 3
    assert state.steps[0]["role"] == "review"
    assert state.steps[1]["role"] == "build"
    assert state.steps[2]["role"] == "verify"


def test_auto_workflow_mode_builds_when_marker_yes(cwd):
    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        workflow_mode="auto",
    )

    review_text = "1. Step one\n\nBUILD_NEEDED: yes"
    build_text = "Built.\nVERIFY_RESULT: PASS"
    verify_text = "VERIFY_RESULT: PASS"

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text, build_text, verify_text])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("build a login page", config, cwd, database_path=Path(cwd) / "test.db")

    assert len(state.steps) == 3
    assert state.steps[0]["role"] == "review"
    assert state.steps[1]["role"] == "build"
    assert state.steps[2]["role"] == "verify"


def test_consecutive_identical_tool_calls_guard(cwd):
    (Path(cwd) / "src").mkdir()
    (Path(cwd) / "src" / "foo.py").write_text("content")

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        workflow_mode="auto",
        max_iterations=1,
    )

    review_text = "Plan to check foo.\nBUILD_NEEDED: yes"
    # Build repeats exact same tool call 3 times
    repeat_tool = """<tool_call>
{"name": "ReadFile", "args": {"path": "src/foo.py"}}
</tool_call>"""

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([
            review_text,
            repeat_tool,
            repeat_tool,
            repeat_tool,
        ])
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("build task", config, cwd, database_path=Path(cwd) / "test.db")

    # Build step stopped due to consecutive identical calls
    assert len(state.steps) == 2
    build_step = state.steps[1]
    assert build_step["role"] == "build"
    assert build_step["success"] is False
    assert "repeated the same tool call (ReadFile) 3 times" in build_step["text"]
    assert any("repeated the same tool call" in b["detail"] for b in state.blockers)


def test_no_progress_write_mode_guard(cwd):
    (Path(cwd) / "src").mkdir()
    (Path(cwd) / "src" / "foo.py").write_text("content")

    config = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        workflow_mode="auto",
        max_iterations=1,
    )

    review_text = "Plan.\nBUILD_NEEDED: yes"
    # Issue 7 different non-mutating tool calls (ReadFile on different paths)
    tools = [
        f'<tool_call>{{"name": "ReadFile", "args": {{"path": "src/f{i}.py"}}}}</tool_call>'
        for i in range(7)
    ]

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        backend = _make_backend_responses([review_text] + tools)
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)

        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("build task", config, cwd, database_path=Path(cwd) / "test.db")

    build_step = state.steps[1]
    assert build_step["role"] == "build"
    assert build_step["success"] is False
    assert "ran 6 tools but wrote no files" in build_step["text"]
    assert any("ran 6 tools but wrote no files" in b["detail"] for b in state.blockers)



