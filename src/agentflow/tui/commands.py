"""Slash command parsing and dispatch for the terminal UI."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..backends import BACKENDS
from ..config import Config
from ..database import get_session_runs
from ..models import get_all_models
from ..orchestrator import new_session_id
from ..tools import get_tool, list_tools
from .render import session_cost


@dataclass
class CommandResult:
    output: str
    new_session_id: str | None = None
    should_clear: bool = False
    should_exit: bool = False


@dataclass(frozen=True)
class CommandSpec:
    name: str
    summary: str
    usage: str | None = None          # shown in /help when it takes args
    arg_completions: tuple[str, ...] = ()  # completion sources per positional slot; see below


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        name="/help",
        summary="Show available commands",
    ),
    CommandSpec(
        name="/model",
        summary="Show or set role models",
        usage="/model [<role> <model-id>]",
        arg_completions=("role", "model_id"),
    ),
    CommandSpec(
        name="/config",
        summary="Show or change configuration",
        usage="/config [permissions <mode> | max-cost <val> | <role> <backend> [model]]",
        arg_completions=("config_sub", "backend", "model_id"),
    ),
    CommandSpec(
        name="/tools",
        summary="List available tools",
    ),
    CommandSpec(
        name="/mcp",
        summary="Check MCP server connections and list their tools",
    ),
    CommandSpec(
        name="/serve",
        summary="Start the web console for this session in the background",
        usage="/serve [<port>] [<host>]",
    ),
    CommandSpec(
        name="/resume",
        summary="Switch to a prior session",
        usage="/resume <session_id>",
        arg_completions=("session_id",),
    ),
    CommandSpec(
        name="/clear",
        summary="Clear screen, start a fresh session",
    ),
    CommandSpec(
        name="/cost",
        summary="Show cumulative cost for this session",
    ),
    CommandSpec(
        name="/exit",
        summary="Exit the REPL",
    ),
    CommandSpec(
        name="/quit",
        summary="Exit the REPL",
    ),
)


def parse_command(line: str) -> tuple[str, list[str]] | None:
    """Parse a line into (command, args) if it starts with '/', else return None."""
    line = line.strip()
    if not line.startswith("/"):
        return None
    try:
        parts = shlex.split(line)
    except ValueError:
        parts = line.split()
    if not parts:
        return None
    return parts[0].lower(), parts[1:]


def dispatch(
    cmd: str,
    args: list[str],
    config: Config,
    cwd: str,
    session_id: str,
    database_path: Path | None = None,
) -> CommandResult:
    """Dispatch a slash command, mutating state/config and returning result."""
    if cmd in ("/help", "/?"):
        lines = ["[bold]Available REPL Commands:[/bold]"]
        for spec in COMMANDS:
            lines.append(f"  • [cyan]{spec.name}[/cyan] - {spec.summary}")
            if spec.usage:
                lines.append(f"    [dim]Usage:[/dim] [cyan]{spec.usage}[/cyan]")
        return CommandResult("\n".join(lines))

    if cmd == "/model":
        if not args:
            lines = ["[bold]Current Role Models:[/bold]"]
            for role, role_cfg in config.roles().items():
                lines.append(
                    f"  • {role}: backend={role_cfg.backend}, model={role_cfg.model or '(default)'}"
                )
            lines.append("\n[bold]Available Models & Pricing:[/bold]")
            all_models = get_all_models()
            for backend, model_list in all_models.items():
                lines.append(f"\n[bold cyan]Backend: {backend}[/bold cyan] ({len(model_list)} models)")
                for m in model_list:
                    rec = " ★ (recommended)" if m.get("recommended") else ""
                    pricing = m.get("pricing", "N/A")
                    desc = m.get("description", "")
                    lines.append(f"  • {m['id']:<35} {pricing:<30} {desc}{rec}")
            return CommandResult("\n".join(lines))

        if len(args) >= 2:
            role = args[0].lower()
            model_id = args[1]
            if role not in ("review", "build", "verify"):
                return CommandResult(
                    f"[red]Error:[/red] unknown role '{role}'. Valid roles: review, build, verify"
                )
            role_cfg = getattr(config, role)
            role_cfg.model = model_id
            return CommandResult(f"[green]✓[/green] Set {role} model to [bold]{model_id}[/bold]")
        return CommandResult("Usage: /model or /model <role> <model-id>")

    if cmd == "/config":
        if not args:
            lines = [
                "[bold]Current Configuration:[/bold]",
                f"  • review: backend={config.review.backend}, model={config.review.model or '(default)'}",
                f"  • build: backend={config.build.backend}, model={config.build.model or '(default)'}",
                f"  • verify: backend={config.verify.backend}, model={config.verify.model or '(default)'}",
                f"  • permissions: {config.permissions}",
                f"  • max_cost_usd: {f'${config.max_cost_usd:.2f}' if config.max_cost_usd is not None else 'unlimited'}",
                f"  • max_iterations: {config.max_iterations}",
            ]
            return CommandResult("\n".join(lines))

        sub = args[0].lower()
        if sub == "permissions":
            if len(args) < 2 or args[1].lower() not in ("auto", "prompt", "deny"):
                return CommandResult("Usage: /config permissions <auto|prompt|deny>")
            config.permissions = args[1].lower()  # type: ignore[assignment]
            return CommandResult(
                f"[green]✓[/green] Permissions policy set to [bold]{config.permissions}[/bold]"
            )

        if sub in ("max-cost", "max_cost"):
            if len(args) < 2:
                return CommandResult("Usage: /config max-cost <float|none>")
            val = args[1].lower()
            if val in ("none", "null", "unlimited"):
                config.max_cost_usd = None
                return CommandResult("[green]✓[/green] Max cost set to [bold]unlimited[/bold]")
            try:
                config.max_cost_usd = float(val)
                return CommandResult(
                    f"[green]✓[/green] Max cost set to [bold]${config.max_cost_usd:.2f}[/bold]"
                )
            except ValueError:
                return CommandResult(f"[red]Error:[/red] invalid cost value: {args[1]}")

        if sub in ("review", "build", "verify"):
            role = sub
            if len(args) < 2:
                return CommandResult(f"Usage: /config {role} <backend> [model]")
            backend = args[1].lower()
            if backend not in BACKENDS:
                return CommandResult(
                    f"[red]Error:[/red] unknown backend '{backend}'. Valid backends: {', '.join(BACKENDS)}"
                )
            role_cfg = getattr(config, role)
            role_cfg.backend = backend  # type: ignore[assignment]
            if len(args) >= 3:
                role_cfg.model = args[2] if args[2].lower() not in ("none", "default") else None
            return CommandResult(
                f"[green]✓[/green] Updated {role}: backend=[bold]{role_cfg.backend}[/bold], model=[bold]{role_cfg.model or '(default)'}[/bold]"
            )

        return CommandResult(
            "Usage: /config, /config permissions <mode>, /config max-cost <val>, or /config <role> <backend> [model]"
        )

    if cmd == "/tools":
        lines = ["[bold]Available Tools:[/bold]"]
        for name in list_tools():
            tool = get_tool(name)
            desc = getattr(tool, "description", "")
            lines.append(f"  • [bold cyan]{name}[/bold cyan]: {desc}")
        if config.mcp_servers:
            n_srv = len(config.mcp_servers)
            lines.append(f"\n[dim](+ MCP tools from {n_srv} configured server(s) - see 'agentflow --mcp-check')[/dim]")
        return CommandResult("\n".join(lines))

    if cmd == "/mcp":
        servers = config.mcp_servers or []
        if not servers:
            return CommandResult(
                "[dim]No MCP servers configured.[/dim] Add them under "
                "[cyan]mcp_servers:[/cyan] in agentflow.config.yaml."
            )
        enabled = [s for s in servers if s.enabled]
        disabled = [s for s in servers if not s.enabled]
        lines = ["[bold]MCP Servers:[/bold]"]
        if enabled:
            from ..mcp import MCPManager

            manager = MCPManager(enabled, cwd=cwd)
            try:
                manager.start()
                tools_by_server: dict[str, list[str]] = {}
                for t in manager.list_tools():
                    tools_by_server.setdefault(t.server_name, []).append(t.remote_name)
                for s in enabled:
                    if s.name in manager.errors:
                        lines.append(f"  • [red]✗ {s.name}[/red]: {manager.errors[s.name]}")
                    else:
                        tnames = sorted(tools_by_server.get(s.name, []))
                        aa = "all" if s.auto_approve == ["all"] else (", ".join(s.auto_approve) or "none")
                        lines.append(
                            f"  • [green]✓ {s.name}[/green] ({len(tnames)} tool(s), auto-approve: {aa})"
                        )
                        for tn in tnames:
                            lines.append(f"      [dim]mcp__{s.name}__{tn}[/dim]")
            except Exception as exc:
                return CommandResult(f"[red]MCP check failed:[/red] {exc}")
            finally:
                manager.close()
        for s in disabled:
            lines.append(f"  • [dim]- {s.name} (disabled)[/dim]")
        return CommandResult("\n".join(lines))

    if cmd == "/serve":
        from ..config import DEFAULT_CONFIG_PATH, active_config_path
        from .webserver import (
            DEFAULT_SERVE_HOST,
            DEFAULT_SERVE_PORT,
            current,
            start_web_server,
        )

        existing = current()
        if existing is not None and existing.thread.is_alive():
            return CommandResult(
                f"[dim]Web console already running at[/dim] [cyan]{existing.url}[/cyan]"
            )

        port = DEFAULT_SERVE_PORT
        host = DEFAULT_SERVE_HOST
        # args: [port] [host]  (port must be an int; host is anything else)
        for a in args:
            if a.isdigit():
                port = int(a)
            else:
                host = a
        if not (1 <= port <= 65535):
            return CommandResult(f"[red]Error:[/red] invalid port: {port}")

        cfg_path = active_config_path() or DEFAULT_CONFIG_PATH
        try:
            state, already = start_web_server(
                cwd=cwd,
                config_path=cfg_path,
                database_path=database_path,
                host=host,
                port=port,
            )
        except Exception as exc:
            return CommandResult(f"[red]Could not start web console:[/red] {exc}")

        note = (
            "already running"
            if already
            else "shares this session's runs - it keeps running until you exit the REPL"
        )
        hint = ""
        if host not in ("127.0.0.1", "localhost"):
            hint = f"\n[dim]Bound to {host} - reachable from other hosts on the network.[/dim]"
        return CommandResult(
            f"[green]✓[/green] Web console at [cyan]{state.url}[/cyan] [dim]({note})[/dim]{hint}"
        )

    if cmd == "/resume":
        if not args:
            return CommandResult("Usage: /resume <session_id>")
        target_id = args[0]
        runs = get_session_runs(target_id, path=database_path)
        if not runs:
            return CommandResult(
                f"Switched to session [bold]{target_id}[/bold] (no prior runs recorded).",
                new_session_id=target_id,
            )
        lines = [f"[bold]Resumed session {target_id}[/bold] ({len(runs)} prior runs):"]
        for idx, r in enumerate(runs, 1):
            goal = r.get("goal", "(no goal)")
            pushed = "pushed" if (r.get("pushed") and r["pushed"].get("pushed")) else "not pushed"
            lines.append(f"  {idx}. [dim]Run {r.get('run_id')}:[/dim] {goal} ({pushed})")
        return CommandResult("\n".join(lines), new_session_id=target_id)

    if cmd == "/clear":
        new_id = new_session_id()
        return CommandResult(
            f"Cleared screen. Started new session: [bold]{new_id}[/bold]",
            new_session_id=new_id,
            should_clear=True,
        )

    if cmd == "/cost":
        runs = get_session_runs(session_id, path=database_path)
        cost = session_cost(runs)
        return CommandResult(
            f"Session [bold]{session_id}[/bold] cumulative cost: [bold]${cost:.4f}[/bold] across {len(runs)} runs"
        )

    if cmd in ("/exit", "/quit"):
        return CommandResult("Exiting agentflow REPL.", should_exit=True)

    return CommandResult(f"[red]Unknown command:[/red] {cmd}. Type [cyan]/help[/cyan] for available commands.")
