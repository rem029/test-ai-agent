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
        lines = [
            "[bold]Available REPL Commands:[/bold]",
            "  • [cyan]/help[/cyan] - Show this help message",
            "  • [cyan]/model[/cyan] - Show active models and pricing catalog",
            "  • [cyan]/model <role> <model-id>[/cyan] - Set model for a role (review/build/verify)",
            "  • [cyan]/config[/cyan] - Show current configuration",
            "  • [cyan]/config permissions <auto|prompt|deny>[/cyan] - Set tool permissions policy",
            "  • [cyan]/config max-cost <amount|none>[/cyan] - Set maximum cost budget in USD",
            "  • [cyan]/config <role> <backend> [model][/cyan] - Set backend and optional model for a role",
            "  • [cyan]/tools[/cyan] - List all available tools and descriptions",
            "  • [cyan]/resume <session_id>[/cyan] - Switch to a prior session and view history",
            "  • [cyan]/clear[/cyan] - Clear screen and start a fresh session",
            "  • [cyan]/cost[/cyan] - Show cumulative token cost for the active session",
            "  • [cyan]/exit[/cyan], [cyan]/quit[/cyan] - Exit the REPL",
        ]
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
        return CommandResult("\n".join(lines))

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
