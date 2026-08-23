"""Core loop: goal -> review -> build -> verify -> iterate -> push.

Each role's backend is whatever config.py resolved (see PLAN.md's pluggable
backend design). Per-task token/cost usage is recorded on every step and
persisted to a structured per-run state file (PLAN.md, "Cost & token
tracking per task" and "Interface: CLI first, web later").
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .backends import BACKENDS
from .backends.base import RunResult
from .config import Config, RoleConfig

STATE_DIR = ".agentflow/runs"

REVIEW_PROMPT = """You are the reviewer/planner for a coding task in this repository.

Goal: {goal}

Read whatever files you need to understand the codebase, then respond with a
concise, numbered task breakdown for what the build step should implement.
Keep it to the essential steps only - no preamble, no explanation of what \
you did, just the numbered plan."""

BUILD_PROMPT = """You are implementing a coding task in this repository.

Goal: {goal}

Plan:
{plan}
{feedback_section}
Make the necessary changes to the repository now."""

VERIFY_PROMPT = """You are verifying a coding task in this repository.

Goal: {goal}

Plan that was implemented:
{plan}

Check the changes: run any relevant tests/lint, and judge whether the goal \
was actually met. Respond with your findings, and end your response with \
exactly one of these two lines:
VERIFY_RESULT: PASS
VERIFY_RESULT: FAIL"""


def _build_backend(role_config: RoleConfig):
    return BACKENDS[role_config.backend](model=role_config.model)


@dataclass
class RunState:
    run_id: str
    goal: str
    started_at: float
    config: dict
    steps: list = field(default_factory=list)
    finished_at: float | None = None
    pushed: dict | None = None

    def total_usage(self) -> dict[str, dict]:
        totals: dict[str, dict] = {}
        for step in self.steps:
            u = step["usage"]
            key = f"{u['backend']}:{u['model']}"
            bucket = totals.setdefault(
                key, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
            )
            bucket["input_tokens"] += u.get("input_tokens") or 0
            bucket["output_tokens"] += u.get("output_tokens") or 0
            bucket["cost_usd"] += u.get("cost_usd") or 0.0
        return totals

    def save(self, cwd: str) -> Path:
        out_dir = Path(cwd) / STATE_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.run_id}.json"
        path.write_text(json.dumps(asdict(self), indent=2, default=str))
        return path


def _record(role: str, mode: str, iteration: int, result: RunResult) -> dict:
    return {
        "role": role,
        "mode": mode,
        "iteration": iteration,
        "success": result.success,
        "text": result.text[:2000],
        "usage": asdict(result.usage),
    }


def _parse_verify_result(text: str) -> bool:
    for line in reversed(text.strip().splitlines()):
        if line.strip().upper().startswith("VERIFY_RESULT:"):
            return "PASS" in line.upper()
    return False  # no explicit verdict in the response - don't guess pass


def run_workflow(goal: str, config: Config, cwd: str) -> RunState:
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    state = RunState(
        run_id=run_id,
        goal=goal,
        started_at=time.time(),
        config={role: cfg.model_dump() for role, cfg in config.roles().items()},
    )

    review_backend = _build_backend(config.review)
    build_backend = _build_backend(config.build)
    verify_backend = _build_backend(config.verify)

    print(f"[review] planning for goal: {goal}")
    review_result = review_backend.run(REVIEW_PROMPT.format(goal=goal), cwd=cwd, mode="read")
    state.steps.append(_record("review", "read", 0, review_result))
    state.save(cwd)

    if not review_result.success:
        print(f"[review] FAILED: {review_result.text[:300]}")
        state.finished_at = time.time()
        state.save(cwd)
        _print_summary(state)
        return state

    plan = review_result.text
    print(f"[review] plan:\n{plan}\n")

    feedback = ""
    verified = False
    for iteration in range(1, config.max_iterations + 1):
        print(f"[build] iteration {iteration}/{config.max_iterations}")
        feedback_section = (
            f"\nFeedback from the previous attempt:\n{feedback}\n" if feedback else ""
        )
        build_prompt = BUILD_PROMPT.format(goal=goal, plan=plan, feedback_section=feedback_section)
        build_result = build_backend.run(build_prompt, cwd=cwd, mode="write")
        state.steps.append(_record("build", "write", iteration, build_result))
        state.save(cwd)

        if not build_result.success:
            print(f"[build] FAILED: {build_result.text[:300]}")
            feedback = build_result.text
            continue

        print(f"[verify] iteration {iteration}/{config.max_iterations}")
        verify_result = verify_backend.run(
            VERIFY_PROMPT.format(goal=goal, plan=plan), cwd=cwd, mode="verify"
        )
        state.steps.append(_record("verify", "verify", iteration, verify_result))
        state.save(cwd)

        verified = verify_result.success and _parse_verify_result(verify_result.text)
        print(f"[verify] {'PASS' if verified else 'FAIL'}: {verify_result.text[:300]}")

        if verified:
            break
        feedback = verify_result.text

    state.finished_at = time.time()

    if verified:
        state.pushed = _commit_and_push(goal, plan, cwd)
    else:
        print(f"[iterate] gave up after {config.max_iterations} iteration(s) without passing verify")

    state.save(cwd)
    _print_summary(state)
    return state


def _commit_and_push(goal: str, plan: str, cwd: str) -> dict | None:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True
    )
    if not status.stdout.strip():
        print("[push] no changes to commit")
        return None

    subject = goal.strip().splitlines()[0][:72]
    message = (
        f"{subject}\n\n"
        f"Goal: {goal}\n\n"
        f"Plan:\n{plan}\n\n"
        f"Verify: PASS\n\n"
        f"Co-Authored-By: agentflow <noreply@agentflow.local>"
    )

    add = subprocess.run(["git", "add", "-A"], cwd=cwd, capture_output=True, text=True)
    if add.returncode != 0:
        print(f"[push] git add failed: {add.stderr.strip()}")
        return {"pushed": False, "error": f"git add failed: {add.stderr.strip()}"}

    commit = subprocess.run(
        ["git", "commit", "-m", message], cwd=cwd, capture_output=True, text=True
    )
    if commit.returncode != 0:
        print(f"[push] git commit failed: {commit.stderr.strip()}")
        return {"pushed": False, "error": f"git commit failed: {commit.stderr.strip()}"}

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()
    push = subprocess.run(
        ["git", "push", "-u", "origin", branch], cwd=cwd, capture_output=True, text=True
    )

    if push.returncode != 0:
        print(f"[push] commit created ({sha[:8]}) but push failed: {push.stderr.strip()}")
        return {"branch": branch, "commit": sha, "pushed": False, "error": push.stderr.strip()}

    print(f"[push] committed and pushed {sha[:8]} to {branch}")
    return {"branch": branch, "commit": sha, "pushed": True}


def _print_summary(state: RunState) -> None:
    print("\n=== agentflow run summary ===")
    print(f"run_id: {state.run_id}")
    print(f"goal: {state.goal}")
    totals = state.total_usage()
    grand_cost = 0.0
    for key, bucket in totals.items():
        print(
            f"  {key}: in={bucket['input_tokens']} out={bucket['output_tokens']} "
            f"cost=${bucket['cost_usd']:.6f}"
        )
        grand_cost += bucket["cost_usd"]
    print(f"total cost: ${grand_cost:.6f}")
    print(f"pushed: {state.pushed if state.pushed else 'no (not verified, or no changes)'}")
