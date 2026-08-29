"""Interactive REPL terminal UI for agentflow."""

from __future__ import annotations

import json
import select
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..config import Config
from ..tools import strip_tool_blocks

_SAFE_DURING_RUN = frozenset({"/config", "/model", "/cost", "/help", "/tools", "/?"})


def _read_pending_line() -> str | None:
    """Non-blocking read of one line from stdin. None if nothing is ready or stdin isn't a tty."""
    try:
        if not sys.stdin.isatty():
            return None
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return None
        line = sys.stdin.readline()
        if not line:
            return None
        return line.strip() or None
    except Exception:
        return None


def _handle_mid_run_input(
    text: str,
    run_id: str,
    config: Config,
    cwd: str,
    session_id: str,
    database_path: Path | None,
    console: Any,
) -> None:
    from ..database import add_pending_message
    from .commands import dispatch, parse_command

    parsed = parse_command(text)
    if parsed is not None:
        cmd, args = parsed
        if cmd in _SAFE_DURING_RUN:
            result = dispatch(
                cmd,
                args,
                config,
                cwd=cwd,
                session_id=session_id,
                database_path=database_path,
            )
            if result.output:
                console.print(result.output)
        else:
            console.print(f"[yellow]{cmd} is not available during a run.[/yellow] It will not be queued.")
        return
    # plain text -> steer
    add_pending_message(run_id, text, kind="steer", path=database_path)
    console.print("[dim]↳ queued — will steer at the next phase boundary.[/dim]")


def _session_tag(sid: str) -> str:
    """Return the short display tag for a session ID."""
    if not sid:
        return ""
    return sid.split("-")[-1] if "-" in sid else sid[-8:]


def _build_toolbar(active_session_id: str, database_path: Path | None = None) -> str:
    """Construct the persistent bottom toolbar status string."""
    try:
        from ..database import get_session, get_session_runs
        from .render import session_cost

        sess = get_session(active_session_id, path=database_path)
        title = sess.get("title") if sess and sess.get("title") else "untitled"
        tag = _session_tag(active_session_id)
        runs = get_session_runs(active_session_id, path=database_path)
        running_cost = session_cost(runs)
        return f'agentflow · {tag} · "{title}" · ${running_cost:.4f}'
    except Exception:
        return ""


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
    from ..database import add_control_signal, get_session, list_events, reconstruct_run
    from ..orchestrator import new_run_id, new_session_id, run_workflow
    from .commands import dispatch, parse_command
    from .completion import SlashCommandCompleter
    from .permissions import SessionPermissionBroker
    from .render import format_event, format_footer

    console = Console()

    active_session_id = session_id or new_session_id()

    def _toolbar() -> str:
        return _build_toolbar(active_session_id, database_path=database_path)

    # Configure session history
    history_file = AGENTFLOW_HOME / "repl_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_file)),
        completer=SlashCommandCompleter(),
        complete_while_typing=True,
        bottom_toolbar=lambda: _toolbar(),
    )

    # Default REPL permissions to "prompt" unless explicitly set to "deny"
    if config.permissions != "deny":
        config.permissions = "prompt"

    broker = SessionPermissionBroker()

    # Print banner
    home_dir = str(Path.home())
    resolved_cwd = str(Path(cwd).resolve())
    if resolved_cwd == home_dir:
        shown_cwd = "~"
    elif resolved_cwd.startswith(home_dir + "/"):
        shown_cwd = "~" + resolved_cwd[len(home_dir):]
    else:
        shown_cwd = str(cwd)

    console.print("[bold cyan]agentflow[/bold cyan] [dim]interactive REPL[/dim]")
    console.print(f"[dim]Project:[/dim] {shown_cwd}", soft_wrap=True)
    console.print(
        f"[dim]Backends:[/dim] review={config.review.backend} build={config.build.backend} verify={config.verify.backend}",
        soft_wrap=True,
    )
    console.print(f"[dim]Session:[/dim] {active_session_id}", soft_wrap=True)
    console.print("[dim]Type [cyan]/help[/cyan] for commands or enter a goal to start a workflow.[/dim]\n")

    while True:
        try:
            session_tag = _session_tag(active_session_id)
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
                    quiet=True,
                )
            except Exception as exc:
                run_box["exc"] = exc

        worker = threading.Thread(target=_run_turn, daemon=True)
        worker.start()
        console.print("[dim](type to steer · /config /model /cost /help /tools work · Ctrl+C to stop)[/dim]")

        last_seq = -1
        accumulated_deltas: list[str] = []
        stop_signal_sent = False
        last_role = "agent"
        last_rendered_tool_sig: tuple[str, str] | None = None
        current_call_sig: tuple[str, str] | None = None

        status = console.status(f"[dim]{last_role} working…[/dim]")
        status_running = False

        def _stop_status() -> None:
            nonlocal status_running
            if status_running:
                try:
                    status.stop()
                except Exception:
                    pass
                status_running = False

        def _start_status() -> None:
            nonlocal status_running
            if not status_running and worker.is_alive():
                try:
                    status.update(f"[dim]{last_role} working…[/dim]")
                    status.start()
                    status_running = True
                except Exception:
                    status_running = False

        def _flush_deltas() -> None:
            if accumulated_deltas:
                raw_text = "".join(accumulated_deltas)
                accumulated_deltas.clear()
                cleaned = strip_tool_blocks(raw_text)
                if cleaned:
                    _stop_status()
                    console.print(cleaned)

        try:
            while True:
                try:
                    # 1. Drain new events
                    events = list_events(run_id, path=database_path)
                    for ev in events:
                        if ev["seq"] > last_seq:
                            last_seq = ev["seq"]
                            etype = ev["type"]
                            payload = ev.get("payload", {})

                            if etype == "text_delta":
                                delta = payload.get("delta", "")
                                accumulated_deltas.append(delta)
                            elif etype == "step_started":
                                last_rendered_tool_sig = None
                                last_role = payload.get("role", "agent")
                                _flush_deltas()
                                rendered = format_event(ev)
                                if rendered is not None:
                                    _stop_status()
                                    console.print(rendered)
                            elif etype == "tool_call":
                                tool_name = payload.get("tool_name", "")
                                args = payload.get("args", {})
                                args_repr = json.dumps(args, sort_keys=True)
                                current_call_sig = (tool_name, args_repr)
                                _flush_deltas()
                                rendered = format_event(ev)
                                if rendered is not None:
                                    _stop_status()
                                    console.print(rendered)
                            elif etype == "tool_result":
                                _flush_deltas()
                                tool_name = payload.get("tool_name", "")
                                args = payload.get("args", {})
                                args_repr = (
                                    json.dumps(args, sort_keys=True)
                                    if args
                                    else (current_call_sig[1] if current_call_sig else "")
                                )
                                res_sig = (tool_name, args_repr)
                                status_val = payload.get("status", "OK")
                                is_error = status_val != "OK" or bool(payload.get("error"))

                                if not is_error and res_sig == last_rendered_tool_sig:
                                    _stop_status()
                                    console.print(f"[dim]✓ {tool_name} (same as above)[/dim]")
                                else:
                                    rendered = format_event(ev)
                                    if rendered is not None:
                                        _stop_status()
                                        console.print(rendered)
                                    if not is_error:
                                        last_rendered_tool_sig = res_sig
                                    else:
                                        last_rendered_tool_sig = None
                            else:
                                _flush_deltas()
                                rendered = format_event(ev)
                                if rendered is not None:
                                    _stop_status()
                                    console.print(rendered)

                    # 2. Service broker permission requests
                    req = broker.poll()
                    if req is not None:
                        _flush_deltas()
                        _stop_status()
                        ans = _prompt_user_permission(console, req.tool_name, req.args)
                        broker.respond(req, ans)

                    # 3. Check for mid-run typed input
                    if worker.is_alive():
                        typed = _read_pending_line()
                        if typed:
                            _flush_deltas()
                            _stop_status()
                            _handle_mid_run_input(
                                typed,
                                run_id,
                                config,
                                cwd,
                                active_session_id,
                                database_path,
                                console,
                            )

                    # 4. Check loop completion
                    finished = any(ev["type"] in ("run_finished", "run_stopped") for ev in events)
                    if not worker.is_alive() and (finished or run_box["exc"] is not None or not events):
                        _flush_deltas()
                        break

                    if worker.is_alive() and not finished:
                        _start_status()

                    time.sleep(0.15)

                except KeyboardInterrupt:
                    _flush_deltas()
                    _stop_status()
                    if not stop_signal_sent:
                        stop_signal_sent = True
                        add_control_signal(run_id, "stop", path=database_path)
                        console.print("\n[yellow]stopping after the current step…[/yellow]")
                    else:
                        console.print("\n[red]aborting turn…[/red]")
                        broker.cancel_all()
                        break
        finally:
            _stop_status()

        # Render final footer or error
        final_state = run_box["state"]
        if final_state is None:
            final_state = reconstruct_run(run_id, path=database_path)

        if final_state is not None:
            state_dict = final_state.to_dict() if hasattr(final_state, "to_dict") else final_state
            console.print(
                format_footer(
                    state_dict,
                    session=get_session(active_session_id, path=database_path),
                )
            )
        elif run_box["exc"] is not None:
            console.print(f"[bold red]Turn error:[/bold red] {run_box['exc']}")

    return 0
