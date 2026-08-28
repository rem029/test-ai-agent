"""Phase I demo: streaming events, event log, sessions, run reconstruction.

Runs the real orchestrator against a FAKE backend (no API cost, no repo
changes) so you can see the Phase I machinery end to end.

    uv run python demo_phase_i.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agentflow.backends import base as backend_base
from agentflow.backends.base import Event
from agentflow.config import Config, RoleConfig
from agentflow.database import list_events, list_sessions, reconstruct_run
from agentflow import orchestrator


class FakeBackend:
    """Streams a few text_delta events, then a verdict. No network."""

    name = "fake"

    def __init__(self, model=None):
        self.model = model or "fake-1"

    def run(self, prompt, *, cwd, mode="read", tools=None):
        if mode == "read":
            chunks = ["1. Add a greeting\n", "2. Wire it into the CLI\n"]
        elif mode == "write":
            chunks = ["Editing files", " ... done."]
        else:  # verify
            chunks = ["Ran the CLI, imports fine.\n", "VERIFY_RESULT: PASS"]
        for c in chunks:
            yield Event.text_delta(c)
        yield Event.usage(
            backend_base.Usage("fake", self.model, 100, 20, 0.00012)
        )
        yield Event.done(success=True, text="".join(chunks))

    def run_sync(self, prompt, *, cwd, mode="read", tools=None):
        return backend_base.run_sync(self.run(prompt, cwd=cwd, mode=mode))


def main() -> None:
    # Point the orchestrator's backend registry at the fake
    orchestrator.BACKENDS["openrouter"] = FakeBackend

    tmp = Path(tempfile.mkdtemp())
    db = tmp / "demo.db"
    cfg = Config(
        review=RoleConfig(backend="openrouter"),
        build=RoleConfig(backend="openrouter"),
        verify=RoleConfig(backend="openrouter"),
        max_iterations=1,
    )

    print("=" * 60)
    print("RUN 1")
    print("=" * 60)
    state = orchestrator.run_workflow(
        "Add a greeting to the CLI", cfg, cwd=str(tmp), database_path=db
    )
    sid = state.session_id

    print("\n--- EVENT LOG (from the `events` table) ---")
    for ev in list_events(state.run_id, path=db):
        payload = ev["payload"]
        extra = payload.get("delta") or payload.get("role") or payload.get("error") or ""
        print(f"  #{ev['seq']:>3}  {ev['type']:<14} {str(extra)[:50]}")

    print("\n--- RUN REBUILT PURELY FROM THE EVENT LOG ---")
    rebuilt = reconstruct_run(state.run_id, path=db)
    print(f"  goal:     {rebuilt['goal']}")
    print(f"  session:  {rebuilt['session_id']}")
    print(f"  steps:    {[s['role'] for s in rebuilt['steps']]}")
    print(f"  finished: {rebuilt['finished_at'] is not None}")

    print("\n" + "=" * 60)
    print(f"RUN 2 — follow-up on session {sid}")
    print("=" * 60)
    orchestrator.run_workflow(
        "Also add a --greet flag", cfg, cwd=str(tmp), session_id=sid, database_path=db
    )

    print("\n--- SESSIONS (from the `sessions` table) ---")
    for s in list_sessions(str(tmp), path=db):
        print(f"  {s['session_id']}  title={s['title']!r}")


if __name__ == "__main__":
    main()
