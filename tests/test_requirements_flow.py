"""Tests for the requirements-clarification loop and the build-review step."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from agentflow.backends.base import RunResult, Usage
from agentflow.config import Config, RoleConfig
from agentflow.orchestrator import is_analysis_only_goal, run_workflow

import pytest


@pytest.fixture
def cwd(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    return str(path)


def _make_backend(texts):
    backend = MagicMock()
    backend.run.side_effect = [
        RunResult(success=True, text=t, usage=Usage("mock", "model", 1, 1, 0.0), raw={})
        for t in texts
    ]
    return backend


def _config(**kw):
    return Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        **kw,
    )


def _run(goal, config, cwd, responses, responder=None):
    backend = _make_backend(responses)
    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                return run_workflow(
                    goal,
                    config,
                    cwd,
                    database_path=Path(cwd) / "test.db",
                    requirements_responder=responder,
                )


def test_requirements_incomplete_asks_then_proceeds(cwd):
    review = (
        "Plan: build a login page. Ambiguous requirements:\n"
        "1. Which auth provider?\n"
        "BUILD_NEEDED: yes\n"
        "REQUIREMENTS_CLEAR: no"
    )
    re_review = (
        "Plan: build a login page with email/password.\n"
        "BUILD_NEEDED: yes\n"
        "REQUIREMENTS_CLEAR: yes"
    )
    build = "Implemented the login page."
    verify = "Tests pass\nVERIFY_RESULT: PASS"

    answers = []
    def responder(questions):
        answers.append(questions)
        return "Use email/password auth."

    config = _config(max_requirements_rounds=3, build_review=False)
    state = _run("build a login page", config, cwd, [review, re_review, build, verify], responder=responder)

    roles = [s["role"] for s in state.steps]
    assert "requirements" in roles
    assert len(answers) == 1
    assert "Which auth provider" in answers[0]
    assert state.pushed == {"pushed": True}
    # The answer was folded into the build/verify flow.
    assert any("email/password" in s.get("text", "") for s in state.steps)


def test_requirements_rounds_cap_proceeds_anyway(cwd):
    review = (
        "Plan: build a thing. Still ambiguous.\n"
        "BUILD_NEEDED: yes\n"
        "REQUIREMENTS_CLEAR: no"
    )
    build = "Implemented."
    verify = "Tests pass\nVERIFY_RESULT: PASS"

    answers = []
    def responder(questions):
        answers.append(questions)
        return "answer"

    config = _config(max_requirements_rounds=1, build_review=False)
    state = _run("build a thing", config, cwd, [review, build, verify], responder=responder)

    assert len(answers) == 1  # asked once, then proceeded
    assert state.pushed == {"pushed": True}


def test_requirements_clear_first_pass_no_extra_calls(cwd):
    review = "Plan: build a thing. Requirements clear. BUILD_NEEDED: yes\nREQUIREMENTS_CLEAR: yes"
    build = "Implemented."
    verify = "Tests pass\nVERIFY_RESULT: PASS"

    called = []
    def responder(questions):
        called.append(questions)
        return "unexpected"

    config = _config(max_requirements_rounds=3, build_review=False)
    state = _run("build a thing", config, cwd, [review, build, verify], responder=responder)

    assert called == []
    roles = [s["role"] for s in state.steps]
    assert "requirements" not in roles
    assert state.pushed == {"pushed": True}


def test_build_review_step_records_when_enabled(cwd):
    review = "Plan: build a thing. REQUIREMENTS_CLEAR: yes BUILD_NEEDED: yes"
    build = "Implemented."
    build_review = "The diff matches the plan; no correctness issues found."
    verify = "Tests pass\nVERIFY_RESULT: PASS"

    config = _config(build_review=True)
    state = _run("build a thing", config, cwd, [review, build, build_review, verify])

    roles = [s["role"] for s in state.steps]
    assert "build_review" in roles
    assert state.pushed == {"pushed": True}


def test_build_review_skipped_when_disabled(cwd):
    review = "Plan: build a thing. REQUIREMENTS_CLEAR: yes BUILD_NEEDED: yes"
    build = "Implemented."
    verify = "Tests pass\nVERIFY_RESULT: PASS"

    config = _config(build_review=False)
    state = _run("build a thing", config, cwd, [review, build, verify])

    roles = [s["role"] for s in state.steps]
    assert "build_review" not in roles
    assert state.pushed == {"pushed": True}


def test_analysis_classifier_detects_questions():
    assert is_analysis_only_goal("Hello whats next to plan? @.test/dragtask/")
    assert is_analysis_only_goal("whats next?")
    assert is_analysis_only_goal("review the code")
    assert not is_analysis_only_goal("build a login page")


def test_review_empty_after_tools_gets_nudged(cwd):
    # The review used tools then returned nothing readable; it must be nudged
    # into actually writing its analysis instead of ending with no_response.
    (Path(cwd) / "readme.txt").write_text("hello")
    tool_txt = '<tool_call>\n{"name": "ReadFile", "args": {"path": "readme.txt"}}\n</tool_call>'
    blank = "  "
    answer = "The file says hello. ANALYSIS_COMPLETE"
    build = "Implemented."
    verify = "Tests pass\nVERIFY_RESULT: PASS"

    config = _config(build_review=False)
    state = _run("build a login page", config, cwd, [tool_txt, blank, answer, build, verify])

    review = state.steps[0]
    assert review["role"] == "review"
    assert review["no_response"] is not True
    assert "ANALYSIS_COMPLETE" in review["text"]
    assert state.pushed == {"pushed": True}


def test_analysis_question_goal_never_builds(cwd):
    # The user's failing case: a question-style goal where the review returns
    # no plan. It must finish as analysis-only rather than spin the build step.
    review = ""
    config = _config(max_requirements_rounds=3, build_review=False)
    state = _run("Hello whats next to plan? @.test/dragtask/", config, cwd, [review])

    roles = [s["role"] for s in state.steps]
    assert "build" not in roles
    assert state.finished_at is not None
    assert state.pushed is None
