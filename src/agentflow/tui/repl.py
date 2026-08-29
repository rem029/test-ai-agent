"""Interactive REPL terminal UI for agentflow."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prompt_toolkit import PromptSession
    from rich.console import Console

    from ..config import Config
    from ..orchestrator import RunState
    from .permissions import SessionPermissionBroker

_SAFE_DURING_RUN = frozenset({"/config", "/model", "/cost", "/help", "/tools", "/?"})
FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_WAKE = object()


def _flush_deltas(accumulated_deltas: list[str], console: Any) -> None:
    if accumulated_deltas:
        from ..tools import strip_tool_blocks

        raw_text = "".join(accumulated_deltas)
        accumulated_deltas.clear()
        cleaned = strip_tool_blocks(raw_text)
        if cleaned:
            console.print(cleaned)


def _process_new_events(
    run_id: str,
    database_path: Path | None,
    console: Any,
    state: dict[str, Any],
) -> None:
    from ..database import list_events
    from .render import format_event

    events = list_events(run_id, path=database_path)
    for ev in events:
        if ev["seq"] > state["last_seq"]:
            state["last_seq"] = ev["seq"]
            etype = ev["type"]
            payload = ev.get("payload", {})

            if etype == "text_delta":
                delta = payload.get("delta", "")
                state["accumulated_deltas"].append(delta)
            elif etype == "step_started":
                state["last_rendered_tool_sig"] = None
                state["last_role"] = payload.get("role", "agent")
                _flush_deltas(state["accumulated_deltas"], console)
                rendered = format_event(ev)
                if rendered is not None:
                    console.print(rendered)
            elif etype == "tool_call":
                tool_name = payload.get("tool_name", "")
                args = payload.get("args", {})
                args_repr = json.dumps(args, sort_keys=True)
                state["current_call_sig"] = (tool_name, args_repr)
                _flush_deltas(state["accumulated_deltas"], console)
                rendered = format_event(ev)
                if rendered is not None:
                    console.print(rendered)
            elif etype == "tool_result":
                _flush_deltas(state["accumulated_deltas"], console)
                tool_name = payload.get("tool_name", "")
                args = payload.get("args", {})
                current_call_sig = state.get("current_call_sig")
                args_repr = (
                    json.dumps(args, sort_keys=True)
                    if args
                    else (current_call_sig[1] if current_call_sig else "")
                )
                res_sig = (tool_name, args_repr)
                status_val = payload.get("status", "OK")
                is_error = status_val != "OK" or bool(payload.get("error"))

                if not is_error and res_sig == state.get("last_rendered_tool_sig"):
                    console.print(f"[dim]✓ {tool_name} (same as above)[/dim]")
                else:
                    rendered = format_event(ev)
                    if rendered is not None:
                        console.print(rendered)
                    if not is_error:
                        state["last_rendered_tool_sig"] = res_sig
                    else:
                        state["last_rendered_tool_sig"] = None
            elif etype == "step_finished":
                _flush_deltas(state["accumulated_deltas"], console)
                step_data = payload.get("step") or {}
                usage = step_data.get("usage") or {}
                cost = usage.get("cost_usd")
                if cost is not None:
                    state["cost"] = state.get("cost", 0.0) + float(cost)
                rendered = format_event(ev)
                if rendered is not None:
                    console.print(rendered)
            else:
                _flush_deltas(state["accumulated_deltas"], console)
                rendered = format_event(ev)
                if rendered is not None:
                    console.print(rendered)


def _handle_mid_run_input(
    text: str,
    run_id: str,
    config: Any,
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


def _fmt_elapsed(start: float | None) -> str:
    if not start:
        return "0s"
    s = int(max(0.0, time.monotonic() - start))
    return f"{s}s" if s < 60 else f"{s // 60}:{s % 60:02d}"


def _turn_prompt_message(
    tstate: dict[str, Any],
    worker: Any,
    pending_perm: dict[str, Any],
) -> Any:
    req = pending_perm.get("req")
    if req:
        return _perm_prompt_message(req)
    if worker.is_alive():
        from prompt_toolkit.formatted_text import HTML

        frame = FRAMES[int(time.monotonic() * 10) % len(FRAMES)]
        role = tstate.get("last_role", "agent")
        elapsed = _fmt_elapsed(tstate.get("start"))
        cost = tstate.get("cost", 0.0)
        # fixed-width-ish so the cursor doesn't jump as numbers grow
        return HTML(
            f"<ansicyan><b>{frame}</b></ansicyan> "
            f"<b>{role}</b> <ansibrightblack>{elapsed} · ${cost:.4f} · Ctrl+C to interrupt</ansibrightblack>\n"
            f"<ansigreen>›</ansigreen> "
        )
    from prompt_toolkit.formatted_text import HTML

    return HTML("<ansigreen>›</ansigreen> ")


def _perm_prompt_message(req: Any) -> str:
    tool_name = getattr(req, "tool_name", "") if req else ""
    return f"allow {tool_name}? [a]llow once / [s]ession / [d]eny › "


def _parse_perm_answer(ans: str | None) -> str:
    s = (ans or "").strip().lower()
    if s in ("a", "allow", "y", "yes"):
        return "allow"
    if s in ("s", "session", "allow_session", "allow for session"):
        return "allow_session"
    return "deny"


def _turn_toolbar(tstate: dict[str, Any], worker: Any) -> Any:
    if not worker.is_alive():
        return ""
    from prompt_toolkit.formatted_text import HTML

    frame = FRAMES[int(time.monotonic() * 10) % len(FRAMES)]
    role = tstate.get("last_role", "agent")
    cost = tstate.get("cost", 0.0)
    elapsed = _fmt_elapsed(tstate.get("start"))
    return HTML(f" {frame} <b>{role}</b> working · {elapsed} · ${cost:.4f} · <b>Ctrl+C</b> to interrupt ")


def _wake(prompt_session: Any, sentinel: Any) -> None:
    try:
        app = getattr(prompt_session, "app", None)
        if app is not None and app.is_running:
            app.exit(result=sentinel)
    except Exception:
        pass


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

    return _parse_perm_answer(ans)


async def _drain_async(
    run_id: str,
    database_path: Path | None,
    turn_console: Any,
    broker: Any,
    worker: threading.Thread,
    done: dict[str, bool],
    pending_perm: dict[str, Any],
    tstate: dict[str, Any],
    prompt_session: Any,
    wake_sentinel: Any,
    run_box: dict[str, Any] | None = None,
) -> None:
    from ..database import list_events

    state: dict[str, Any] = {
        "last_seq": -1,
        "accumulated_deltas": [],
        "last_role": tstate.get("last_role", "agent"),
        "last_rendered_tool_sig": None,
        "current_call_sig": None,
        "cost": tstate.get("cost", 0.0),
    }
    while not done["v"]:
        events = list_events(run_id, path=database_path)
        _process_new_events(run_id, database_path, turn_console, state)
        tstate["last_role"] = state["last_role"]
        tstate["cost"] = state["cost"]

        req = broker.poll()
        if req is not None and pending_perm["req"] is None:
            _flush_deltas(state["accumulated_deltas"], turn_console)
            turn_console.print(f"\n[bold yellow]Tool Confirmation Required:[/bold yellow] [cyan]{req.tool_name}[/cyan]")
            for k, v in req.args.items():
                turn_console.print(f"  [dim]{k}:[/dim] {v}")
            pending_perm["req"] = req
            _wake(prompt_session, wake_sentinel)

        finished = any(e["type"] in ("run_finished", "run_stopped") for e in events)
        has_exc = run_box is not None and run_box.get("exc") is not None
        if not worker.is_alive() and (finished or has_exc or not events):
            _flush_deltas(state["accumulated_deltas"], turn_console)
            done["v"] = True
            _wake(prompt_session, wake_sentinel)
            return
        await asyncio.sleep(0.15)


async def _turn_interactive(
    run_id: str,
    config: Any,
    cwd: str,
    session_id: str,
    database_path: Path | None,
    broker: Any,
    worker: threading.Thread,
    prompt_session: Any,
    run_box: dict[str, Any] | None = None,
) -> None:
    from prompt_toolkit.patch_stdout import patch_stdout
    from rich.console import Console

    from ..database import add_control_signal

    done = {"v": False}
    pending_perm: dict[str, Any] = {"req": None}
    turn_start = time.monotonic()
    tstate: dict[str, Any] = {"last_role": "agent", "cost": 0.0, "start": turn_start}
    with patch_stdout(raw=True):
        turn_console = Console(width=shutil.get_terminal_size(fallback=(100, 24)).columns)
        drain = asyncio.create_task(
            _drain_async(
                run_id,
                database_path,
                turn_console,
                broker,
                worker,
                done,
                pending_perm,
                tstate,
                prompt_session,
                _WAKE,
                run_box=run_box,
            )
        )
        stop_sent = False
        while not done["v"]:
            try:
                ans = await prompt_session.prompt_async(
                    lambda: _turn_prompt_message(tstate, worker, pending_perm),
                    bottom_toolbar=lambda: _turn_toolbar(tstate, worker),
                    refresh_interval=0.3,
                )
            except KeyboardInterrupt:
                if not stop_sent:
                    stop_sent = True
                    add_control_signal(run_id, "stop", path=database_path)
                    turn_console.print("\n[yellow]stopping after the current step…[/yellow]")
                    continue
                broker.cancel_all()
                add_control_signal(run_id, "abort", path=database_path)
                break
            except EOFError:
                add_control_signal(run_id, "stop", path=database_path)
                break
            if ans is _WAKE:
                continue
            ans = (ans or "").strip()
            if not ans:
                continue
            if pending_perm["req"] is not None:
                req = pending_perm["req"]
                pending_perm["req"] = None
                broker.respond(req, _parse_perm_answer(ans))
            else:
                _handle_mid_run_input(
                    ans,
                    run_id,
                    config,
                    cwd,
                    session_id,
                    database_path,
                    turn_console,
                )
        drain.cancel()
        try:
            await drain
        except asyncio.CancelledError:
            pass


def _turn_drain_only(
    run_id: str,
    database_path: Path | None,
    broker: Any,
    worker: threading.Thread,
    console: Any,
    run_box: dict[str, Any] | None = None,
) -> None:
    from ..database import add_control_signal, list_events

    state: dict[str, Any] = {
        "last_seq": -1,
        "accumulated_deltas": [],
        "last_role": "agent",
        "last_rendered_tool_sig": None,
        "current_call_sig": None,
        "cost": 0.0,
    }
    stop_signal_sent = False

    while True:
        try:
            events = list_events(run_id, path=database_path)
            _process_new_events(run_id, database_path, console, state)

            req = broker.poll()
            if req is not None:
                _flush_deltas(state["accumulated_deltas"], console)
                ans = _prompt_user_permission(console, req.tool_name, req.args)
                broker.respond(req, ans)

            finished = any(ev["type"] in ("run_finished", "run_stopped") for ev in events)
            has_exc = run_box is not None and run_box.get("exc") is not None
            if not worker.is_alive() and (finished or has_exc or not events):
                _flush_deltas(state["accumulated_deltas"], console)
                break

            time.sleep(0.15)
        except KeyboardInterrupt:
            _flush_deltas(state["accumulated_deltas"], console)
            if not stop_signal_sent:
                stop_signal_sent = True
                add_control_signal(run_id, "stop", path=database_path)
                console.print("\n[yellow]stopping after the current step…[/yellow]")
            else:
                console.print("\n[red]aborting turn…[/red]")
                broker.cancel_all()
                add_control_signal(run_id, "abort", path=database_path)
                break


def _execute_turn(
    goal: str,
    run_id: str,
    config: Any,
    cwd: str,
    session_id: str,
    database_path: Path | None,
    broker: Any,
    console: Any,
    prompt_session: Any,
) -> Any:
    from ..database import get_session, reconstruct_run
    from ..orchestrator import run_workflow
    from .render import format_footer

    run_box: dict[str, Any] = {"state": None, "exc": None}

    def _run_turn() -> None:
        try:
            run_box["state"] = run_workflow(
                goal=goal,
                config=config,
                cwd=cwd,
                run_id=run_id,
                session_id=session_id,
                database_path=database_path,
                permission_handler=broker.handler,
                quiet=True,
            )
        except Exception as exc:
            run_box["exc"] = exc

    worker = threading.Thread(target=_run_turn, daemon=True)
    worker.start()
    console.print("[dim](type to steer · /config /model /cost /help /tools work · Ctrl+C to stop)[/dim]")

    if sys.stdin.isatty():
        asyncio.run(
            _turn_interactive(
                run_id=run_id,
                config=config,
                cwd=cwd,
                session_id=session_id,
                database_path=database_path,
                broker=broker,
                worker=worker,
                prompt_session=prompt_session,
                run_box=run_box,
            )
        )
    else:
        _turn_drain_only(
            run_id=run_id,
            database_path=database_path,
            broker=broker,
            worker=worker,
            console=console,
            run_box=run_box,
        )

    worker.join(timeout=5.0)

    final_state = run_box["state"]
    if final_state is None:
        final_state = reconstruct_run(run_id, path=database_path)

    if final_state is not None:
        state_dict = final_state.to_dict() if hasattr(final_state, "to_dict") else final_state
        console.print(
            format_footer(
                state_dict,
                session=get_session(session_id, path=database_path),
            )
        )
    elif run_box["exc"] is not None:
        console.print(f"[bold red]Turn error:[/bold red] {run_box['exc']}")

    return final_state


def run_repl(
    config: Config,
    cwd: str,
    *,
    session_id: str | None = None,
    database_path: Path | None = None,
) -> int:
    """Run the interactive Claude Code-like REPL."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import merge_completers
    from prompt_toolkit.history import FileHistory
    from rich.console import Console

    from ..config import AGENTFLOW_HOME
    from ..orchestrator import new_run_id, new_session_id
    from .commands import dispatch, parse_command
    from .completion import FileMentionCompleter, SlashCommandCompleter
    from .permissions import SessionPermissionBroker

    console = Console()
    active_session_id = session_id or new_session_id()

    def _toolbar() -> str:
        return _build_toolbar(active_session_id, database_path=database_path)

    # Configure session history
    history_file = AGENTFLOW_HOME / "repl_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_file)),
        completer=merge_completers([
            SlashCommandCompleter(),
            FileMentionCompleter(cwd),
        ]),
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
        _execute_turn(
            goal=stripped,
            run_id=new_run_id(),
            config=config,
            cwd=cwd,
            session_id=active_session_id,
            database_path=database_path,
            broker=broker,
            console=console,
            prompt_session=prompt_session,
        )

    return 0
