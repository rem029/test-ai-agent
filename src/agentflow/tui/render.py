"""Pure rendering helpers for the terminal UI."""

from __future__ import annotations

import difflib
import time
from typing import Any


def truncate_output(text: str, max_lines: int = 20) -> str:
    """Truncate long text to a maximum number of lines with an overflow notice."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    truncated = lines[:max_lines]
    remaining = len(lines) - max_lines
    return "\n".join(truncated) + f"\n… ({remaining} more lines)"


def format_diff(structured: dict[str, Any], max_lines: int = 60) -> str:
    """Format previous vs current content into a colored unified diff."""
    path = structured.get("path", "file")
    prev_text = structured.get("previous", "")
    curr_text = structured.get("current", "")
    prev_lines = prev_text.splitlines() if prev_text else []
    curr_lines = curr_text.splitlines() if curr_text else []

    diff_lines = list(
        difflib.unified_diff(
            prev_lines,
            curr_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    if not diff_lines:
        return "[dim](no diff)[/dim]"

    formatted_lines: list[str] = []
    total_lines = len(diff_lines)
    for line in diff_lines[:max_lines]:
        escaped = line.replace("[", "\\[")
        if line.startswith("+++") or line.startswith("---"):
            formatted_lines.append(f"[bold]{escaped}[/bold]")
        elif line.startswith("+"):
            formatted_lines.append(f"[green]{escaped}[/green]")
        elif line.startswith("-"):
            formatted_lines.append(f"[red]{escaped}[/red]")
        elif line.startswith("@@"):
            formatted_lines.append(f"[cyan]{escaped}[/cyan]")
        else:
            formatted_lines.append(f" {escaped}" if not escaped.startswith(" ") else escaped)

    if total_lines > max_lines:
        formatted_lines.append(f"[dim]… ({total_lines - max_lines} more lines)[/dim]")

    return "\n".join(formatted_lines)


def format_event(ev: dict[str, Any]) -> str | None:
    """Format a single persistent event dict into a rich markup string, or None to skip."""
    etype = ev.get("type", "")
    payload = ev.get("payload", {})

    if etype == "run_started":
        session_id = payload.get("session_id", "")
        goal = payload.get("goal", "")
        sess_str = f" [dim](session: {session_id})[/dim]" if session_id else ""
        return f"[bold green]●[/bold green] [bold]Run started[/bold]{sess_str}\n  [dim]Goal:[/dim] {goal}"

    if etype == "step_started":
        role = payload.get("role", "")
        iteration = payload.get("iteration", 0)
        iter_str = f" [dim](iteration {iteration})[/dim]" if iteration > 0 else ""
        return f"[bold cyan]▸ {role}[/bold cyan]{iter_str}"

    if etype == "text_delta":
        return payload.get("delta", "")

    if etype == "tool_call":
        name = payload.get("tool_name", "")
        args = payload.get("args", {})
        args_summary = ", ".join(f"{k}={v!r}" for k, v in args.items())
        return f"[cyan]⚙ {name}({args_summary})[/cyan]"

    if etype == "tool_result":
        name = payload.get("tool_name", "")
        status = payload.get("status", "OK")
        duration = payload.get("execution_time_ms", 0)
        result = payload.get("result", {})
        error = payload.get("error")

        structured = result.get("structured") if isinstance(result, dict) else None
        has_diff = (
            isinstance(structured, dict)
            and ("previous" in structured or "current" in structured)
        )

        if has_diff:
            diff_text = format_diff(structured)
            if status == "OK":
                return f"[green]✓[/green] [bold]{name}[/bold] [dim]({duration}ms)[/dim]\n{diff_text}"
            return f"[red]✗[/red] [bold]{name}[/bold] [dim]({duration}ms)[/dim]: {error or ''}\n{diff_text}"

        output = result.get("output", "") if isinstance(result, dict) else str(result)
        if status == "OK":
            out_str = f"\n{truncate_output(output)}" if output else ""
            return f"[green]✓[/green] [bold]{name}[/bold] [dim]({duration}ms)[/dim]{out_str}"

        err_text = error or output
        out_str = f"\n{truncate_output(err_text)}" if err_text else ""
        return f"[red]✗[/red] [bold]{name}[/bold] [dim]({duration}ms)[/dim]{out_str}"

    if etype == "step_finished":
        step = payload.get("step", {})
        role = step.get("role", "step")
        return f"[dim]— {role} finished —[/dim]"

    if etype == "blocker":
        btype = payload.get("type", "blocker")
        reason = payload.get("reason", "")
        fatal = payload.get("fatal", False)
        prefix = "[bold red]FATAL BLOCKER[/bold red]" if fatal else "[bold yellow]BLOCKER[/bold yellow]"
        return f"[red]⚠[/red] {prefix} ({btype}): {reason}"

    if etype == "user_message":
        kind = payload.get("kind", "steer")
        body = payload.get("body", "")
        return f"[bold blue]💬 User ({kind}):[/bold blue] {body}"

    if etype == "run_stopped":
        reason = payload.get("reason", "user stop signal")
        return f"[bold yellow]⏹ Run stopped ({reason})[/bold yellow]"

    if etype == "run_finished":
        stopped = payload.get("stopped", False)
        pushed = payload.get("pushed")
        if stopped:
            return "[bold yellow]⏹ Run stopped[/bold yellow]"
        if pushed and pushed.get("pushed"):
            return "[bold green]✓ Run finished and changes pushed[/bold green]"
        return "[bold green]✓ Run completed[/bold green]"

    if etype == "error":
        err = payload.get("error", "")
        return f"[bold red]Error:[/bold red] {err}"

    # Skipped / noise events
    return None


def session_cost(run_states: list[dict[str, Any]]) -> float:
    """Compute the cumulative cost across all runs in a session."""
    total = 0.0
    for r in run_states:
        for s in r.get("steps", []):
            usage = s.get("usage")
            if isinstance(usage, dict):
                cost = usage.get("cost_usd")
                if cost is not None:
                    total += float(cost)
            elif hasattr(usage, "cost_usd"):
                cost = getattr(usage, "cost_usd")
                if cost is not None:
                    total += float(cost)
    return total


def format_footer(state: dict[str, Any]) -> str:
    """Format a summary footer from a run state dictionary."""
    steps = state.get("steps", [])
    total_cost = 0.0
    total_in = 0
    total_out = 0
    backend_stats: dict[str, dict[str, Any]] = {}

    for s in steps:
        usage = s.get("usage") or {}
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        elif hasattr(usage, "__dict__"):
            usage = usage.__dict__

        backend = usage.get("backend") or "unknown"
        model = usage.get("model")
        b_key = f"{backend}:{model}" if model else backend

        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        cost = float(usage.get("cost_usd") or 0.0)

        total_cost += cost
        total_in += in_tok
        total_out += out_tok

        if b_key not in backend_stats:
            backend_stats[b_key] = {"in": 0, "out": 0, "cost": 0.0}
        backend_stats[b_key]["in"] += in_tok
        backend_stats[b_key]["out"] += out_tok
        backend_stats[b_key]["cost"] += cost

    started_at = float(state.get("started_at") or 0.0)
    finished_at = float(state.get("finished_at") or time.time())
    elapsed = max(0.0, finished_at - started_at) if started_at else 0.0

    if state.get("stopped"):
        status_str = "[yellow]STOPPED[/yellow]"
    elif state.get("blockers") and any(b.get("fatal") for b in state.get("blockers", [])):
        status_str = "[red]BLOCKED[/red]"
    elif state.get("pushed") and state["pushed"].get("pushed"):
        status_str = "[green]PUSHED[/green]"
    elif state.get("finished_at"):
        status_str = "[green]FINISHED[/green]"
    else:
        status_str = "[blue]RUNNING[/blue]"

    backend_parts = []
    for b_key, data in backend_stats.items():
        backend_parts.append(
            f"{b_key} (in={data['in']} out={data['out']} cost=${data['cost']:.4f})"
        )
    backend_summary = " | ".join(backend_parts) if backend_parts else "no token usage"

    line = "─" * 60
    return (
        f"{line}\n"
        f"Status: {status_str} | Elapsed: {elapsed:.1f}s | Total Cost: ${total_cost:.4f}\n"
        f"Usage: {backend_summary}\n"
        f"{line}"
    )
