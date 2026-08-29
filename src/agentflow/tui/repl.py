"""Interactive REPL terminal UI for agentflow."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from ..config import Config


def _prompt_user_permission(console: Any, tool_name: str, args: dict[str, Any]) -> str:
    """Prompt the user interactively to approve or deny a mutating tool call."""
    console.print(f"\n[bold yellow]Tool Confirmation Required:[/bold yellow] [cyan]{tool_name}[/cyan]")
    for k, v in args.items():
        console.print(f"  [dim]{k}:[/dim] {v}")
    try:
        from prompt_toolkit import prompt as pt_prompt

        ans = pt_prompt("  [a]llow once / allow for [s]ession / [d]eny [d]: ").strip().lower()
    except Exception:
        try:
            ans = input("  [a]llow once / allow for [s]ession / [d]eny [d]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "deny"

    if ans in ("a", "allow", "y", "yes"):
        return "allow"
    if ans in ("s", "session", "allow_session", "allow for session"):
        return "allow_session"
    return "deny"


def run_repl(
    config: Config,
    cwd: str,
    *,
    session_id: str | None = None,
    database_path: Path | None = None,
) -> int:
    """Run the interactive Claude Code-like REPL."""
    # Lazy-import presentation dependencies
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from rich.console import Console

    from ..config import AGENTFLOW_HOME
    from ..database import add_control_signal, list_events, reconstruct_run
    from ..orchestrator import new_run_id, new_session_id, run_workflow
    from .commands import dispatch, parse_command
    from .permissions import SessionPermissionBroker
    from .render import format_event, format_footer

    console = Console()

    # Configure session history
    history_file = AGENTFLOW_HOME / "repl_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession[str] = PromptSession(history=FileHistory(str(history_file)))

    active_session_id = session_id or new_session_id()

    # Default REPL permissions to "prompt" unless explicitly set to "deny"
    if config.permissions != "deny":
        config.permissions = "prompt"

    broker = SessionPermissionBroker()

    # Print banner
    console.print("[bold cyan]agentflow[/bold cyan] [dim]interactive REPL[/dim]")
    console.print(f"[dim]Project:[/dim] {cwd}")
    console.print(
        f"[dim]Backends:[/dim] review={config.review.backend} build={config.build.backend} verify={config.verify.backend}"
    )
    console.print(f"[dim]Session:[/dim] {active_session_id}")
    console.print("[dim]Type [cyan]/help[/cyan] for commands or enter a goal to start a workflow.[/dim]\n")

    while True:
        try:
            session_tag = (
                active_session_id.split("-")[-1]
                if "-" in active_session_id
                else active_session_id[-8:]
            )
            line = prompt_session.prompt(f"agentflow [{session_tag}]> ")
        except (EOFError, SystemExit):
            console.print("\n[dim]Goodbye![/dim]")
            return 0
        except KeyboardInterrupt:
            console.print("")
            continue

        stripped = line.strip()
        if not stripped:
            continue

        cmd_info = parse_command(stripped)
        if cmd_info is not None:
            cmd, args = cmd_info
            result = dispatch(
                cmd,
                args,
                config,
                cwd=cwd,
                session_id=active_session_id,
                database_path=database_path,
            )
            if result.should_exit:
                if result.output:
                    console.print(result.output)
                return 0
            if result.new_session_id:
                active_session_id = result.new_session_id
            if result.should_clear:
                console.clear()
                broker = SessionPermissionBroker()
            if result.output:
                console.print(result.output)
            continue

        # Positional goal turn
        goal = stripped
        run_id = new_run_id()
        run_box: dict[str, Any] = {"state": None, "exc": None}

        def _run_turn() -> None:
            try:
                run_box["state"] = run_workflow(
                    goal=goal,
                    config=config,
                    cwd=cwd,
                    run_id=run_id,
                    session_id=active_session_id,
                    database_path=database_path,
                    permission_handler=broker.handler,
                )
            except Exception as exc:
                run_box["exc"] = exc

        worker = threading.Thread(target=_run_turn, daemon=True)
        worker.start()

        last_seq = -1
        accumulated_deltas: list[str] = []
        stop_signal_sent = False

        def _flush_deltas() -> None:
            if accumulated_deltas:
                console.print("".join(accumulated_deltas))
                accumulated_deltas.clear()

        while True:
            try:
                # 1. Drain new events
                events = list_events(run_id, path=database_path)
                for ev in events:
                    if ev["seq"] > last_seq:
                        last_seq = ev["seq"]
                        if ev["type"] == "text_delta":
                            delta = ev.get("payload", {}).get("delta", "")
                            accumulated_deltas.append(delta)
                        else:
                            _flush_deltas()
                            rendered = format_event(ev)
                            if rendered is not None:
                                console.print(rendered)

                # 2. Service broker permission requests
                req = broker.poll()
                if req is not None:
                    _flush_deltas()
                    ans = _prompt_user_permission(console, req.tool_name, req.args)
                    broker.respond(req, ans)

                # 3. Check loop completion
                finished = any(ev["type"] in ("run_finished", "run_stopped") for ev in events)
                if not worker.is_alive() and (finished or run_box["exc"] is not None or not events):
                    _flush_deltas()
                    break

                time.sleep(0.15)

            except KeyboardInterrupt:
                _flush_deltas()
                if not stop_signal_sent:
                    stop_signal_sent = True
                    add_control_signal(run_id, "stop", path=database_path)
                    console.print("\n[yellow]stopping after the current step…[/yellow]")
                else:
                    console.print("\n[red]aborting turn…[/red]")
                    broker.cancel_all()
                    break

        # Render final footer or error
        final_state = run_box["state"]
        if final_state is None:
            final_state = reconstruct_run(run_id, path=database_path)

        if final_state is not None:
            state_dict = final_state.to_dict() if hasattr(final_state, "to_dict") else final_state
            console.print(format_footer(state_dict))
        elif run_box["exc"] is not None:
            console.print(f"[bold red]Turn error:[/bold red] {run_box['exc']}")

    return 0
