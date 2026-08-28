"""Email notifications for finished / blocked runs (stdlib smtplib)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .orchestrator import RunState

_sent: set[tuple[str, str]] = set()  # (run_id, event) dedupe, process-lifetime


def _run_link(config: Config, state: RunState, project_name: str | None = None) -> str | None:
    n = getattr(config, "notifications", None)
    if not n or not n.base_url:
        return None
    return f"{n.base_url.rstrip('/')}/runs/{state.run_id}"


def build_message(
    state: RunState,
    config: Config,
    event: str,
    *,
    project_name: str | None = None,
) -> EmailMessage:
    n = getattr(config, "notifications", None)
    email_from = ""
    email_to = ""
    if n:
        email_from = n.email_from or n.smtp_username or ""
        email_to = n.email_to or ""

    msg = EmailMessage()
    msg["Subject"] = f"agentflow — run {event}: {state.goal[:60]}"
    msg["From"] = email_from
    msg["To"] = email_to

    # Determine status
    if event == "blocked":
        status_line = "blocked"
    elif getattr(state, "stopped", False):
        status_line = "stopped"
    elif getattr(state, "pushed", None) and state.pushed.get("pushed"):
        commit = state.pushed.get("commit", "")
        status_line = f"pushed {commit}".strip()
    else:
        status_line = "completed-no-push"

    lines = [
        f"Goal: {state.goal}",
        f"Run ID: {state.run_id}",
    ]
    if getattr(state, "session_id", None):
        lines.append(f"Session ID: {state.session_id}")
    if project_name:
        lines.append(f"Project: {project_name}")
    lines.append(f"Status: {status_line}")

    if getattr(state, "finished_at", None) and getattr(state, "started_at", None):
        dur = state.finished_at - state.started_at
        lines.append(f"Duration: {dur:.1f}s")

    cost = state.total_cost() if hasattr(state, "total_cost") else 0.0
    lines.append(f"Total Cost: ${cost:.6f}")

    if getattr(state, "blockers", None):
        lines.append("\nBlockers:")
        for b in state.blockers:
            reason = b.get("reason", "unknown")
            detail = b.get("detail", "")
            lines.append(f"- [{reason}] {detail}")

    link = _run_link(config, state, project_name)
    if link:
        lines.append(f"\nRun Link: {link}")

    msg.set_content("\n".join(lines))
    return msg


def send(config: Config, msg: EmailMessage, password: str | None) -> None:
    n = getattr(config, "notifications", None)
    if not n or not n.smtp_host:
        raise ValueError("SMTP host is not configured")
    with smtplib.SMTP(n.smtp_host, n.smtp_port, timeout=20) as s:
        if n.smtp_use_tls:
            s.starttls(context=ssl.create_default_context())
        if n.smtp_username and password:
            s.login(n.smtp_username, password)
        s.send_message(msg)


def maybe_notify(
    state: RunState,
    config: Config,
    event: str,
    *,
    project_name: str | None = None,
    password: str | None = None,
    force: bool = False,
) -> str:
    """Returns 'sent' | 'skipped:<reason>' | 'error:<msg>'. Never raises."""
    n = getattr(config, "notifications", None)
    if not n or not n.enabled:
        return "skipped:disabled"
    if event not in n.notify_on and not force:
        return "skipped:event-not-selected"
    if not (n.email_to and n.smtp_host):
        return "skipped:incomplete-config"
    key = (state.run_id, event)
    if key in _sent and not force:
        return "skipped:already-sent"
    if password is None:
        from .credentials import smtp_password as _pw

        password = _pw()
    try:
        msg = build_message(state, config, event, project_name=project_name)
        send(config, msg, password)
        _sent.add(key)
        return "sent"
    except Exception as exc:
        return f"error:{exc}"


def send_test_email(config: Config, password: str | None = None) -> str:
    """Send a minimal test email using the provided configuration.

    Returns 'sent' | 'skipped:<reason>' | 'error:<msg>'. Never raises.
    """
    n = getattr(config, "notifications", None)
    if not n:
        return "skipped:disabled"
    if not (n.email_to and n.smtp_host):
        return "skipped:incomplete-config"
    if password is None:
        from .credentials import smtp_password as _pw

        password = _pw()

    email_from = n.email_from or n.smtp_username or "agentflow@localhost"
    msg = EmailMessage()
    msg["Subject"] = "agentflow — test email"
    msg["From"] = email_from
    msg["To"] = n.email_to
    msg.set_content(
        "This is a test notification from agentflow.\n"
        "If you received this, your SMTP notification settings are working correctly."
    )

    try:
        send(config, msg, password)
        return "sent"
    except Exception as exc:
        return f"error:{exc}"
