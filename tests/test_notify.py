"""Tests for agentflow email notifications module (src/agentflow/notify.py)."""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch
import pytest

import agentflow.notify as notify
from agentflow.config import Config, NotificationConfig, RoleConfig
from agentflow.orchestrator import RunState


@pytest.fixture(autouse=True)
def reset_sent_set():
    notify._sent.clear()
    yield
    notify._sent.clear()


class FakeSMTP:
    def __init__(self, host, port=587, timeout=20):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.starttls_called = False
        self.tls_context = None
        self.login_called = False
        self.username = None
        self.password = None
        self.sent_messages = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.closed = True

    def starttls(self, context=None):
        self.starttls_called = True
        self.tls_context = context

    def login(self, username, password):
        self.login_called = True
        self.username = username
        self.password = password

    def send_message(self, msg):
        self.sent_messages.append(msg)


def _make_config(
    *,
    enabled: bool = True,
    email_to: str | None = "alerts@example.com",
    email_from: str | None = "noreply@example.com",
    smtp_host: str | None = "smtp.example.com",
    smtp_port: int = 587,
    smtp_username: str | None = "user@example.com",
    smtp_use_tls: bool = True,
    notify_on: list = None,
    base_url: str | None = "https://agentui.app.rem029.com",
) -> Config:
    return Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
        notifications=NotificationConfig(
            enabled=enabled,
            email_to=email_to,
            email_from=email_from,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_use_tls=smtp_use_tls,
            notify_on=notify_on if notify_on is not None else ["finished", "blocked"],
            base_url=base_url,
        ),
    )


def test_build_message_content_and_headers():
    config = _make_config(base_url="https://agentui.app.rem029.com")
    state = RunState(
        run_id="20260829-123456-abcdef12",
        goal="Fix issue in authentication middleware",
        started_at=1000.0,
        config={},
        session_id="session-123",
        finished_at=1045.2,
        pushed={"branch": "main", "commit": "deadbeef12345678", "pushed": True},
    )
    state.steps.append({
        "role": "review",
        "mode": "read",
        "iteration": 0,
        "success": True,
        "text": "Plan",
        "usage": {"backend": "mock", "model": "m", "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.005},
    })
    state.blockers.append({
        "reason": "budget",
        "detail": "Approaching limit",
        "fatal": False,
        "step_index": 0,
        "ts": 1010.0,
    })

    msg = notify.build_message(state, config, "finished", project_name="my-project")

    assert msg["Subject"] == "agentflow — run finished: Fix issue in authentication middleware"
    assert msg["From"] == "noreply@example.com"
    assert msg["To"] == "alerts@example.com"

    body = msg.get_content()
    assert "Goal: Fix issue in authentication middleware" in body
    assert "Run ID: 20260829-123456-abcdef12" in body
    assert "Session ID: session-123" in body
    assert "Project: my-project" in body
    assert "Status: pushed deadbeef12345678" in body
    assert "Duration: 45.2s" in body
    assert "Total Cost: $0.005000" in body
    assert "Blockers:" in body
    assert "- [budget] Approaching limit" in body
    assert "Run Link: https://agentui.app.rem029.com/runs/20260829-123456-abcdef12" in body


def test_build_message_fallback_sender_and_status_variants():
    # Fallback to smtp_username when email_from is None
    config = _make_config(email_from=None, smtp_username="user@domain.com", base_url=None)
    
    # 1. Blocked status
    state_blocked = RunState(
        run_id="run-blocked",
        goal="Task 1",
        started_at=100.0,
        config={},
    )
    msg_blocked = notify.build_message(state_blocked, config, "blocked")
    assert msg_blocked["From"] == "user@domain.com"
    assert "Status: blocked" in msg_blocked.get_content()
    assert "Run Link:" not in msg_blocked.get_content()

    # 2. Stopped status
    state_stopped = RunState(
        run_id="run-stopped",
        goal="Task 2",
        started_at=100.0,
        config={},
        stopped=True,
    )
    msg_stopped = notify.build_message(state_stopped, config, "finished")
    assert "Status: stopped" in msg_stopped.get_content()

    # 3. Completed-no-push status
    state_no_push = RunState(
        run_id="run-no-push",
        goal="Task 3",
        started_at=100.0,
        config={},
    )
    msg_no_push = notify.build_message(state_no_push, config, "finished")
    assert "Status: completed-no-push" in msg_no_push.get_content()


def test_maybe_notify_skipped_conditions():
    state = RunState(run_id="run-1", goal="goal", started_at=100.0, config={})

    # 1. notifications is None
    cfg_none = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
        notifications=None,
    )
    assert notify.maybe_notify(state, cfg_none, "finished") == "skipped:disabled"

    # 2. notifications.enabled is False
    cfg_disabled = _make_config(enabled=False)
    assert notify.maybe_notify(state, cfg_disabled, "finished") == "skipped:disabled"

    # 3. event not in notify_on
    cfg_events = _make_config(notify_on=["finished"])
    assert notify.maybe_notify(state, cfg_events, "blocked") == "skipped:event-not-selected"
    # force bypasses notify_on check
    with patch("agentflow.notify.send") as mock_send:
        assert notify.maybe_notify(state, cfg_events, "blocked", force=True) == "sent"
        mock_send.assert_called_once()

    # 4. incomplete config (missing smtp_host or email_to)
    cfg_no_host = _make_config(smtp_host=None)
    assert notify.maybe_notify(state, cfg_no_host, "finished") == "skipped:incomplete-config"
    cfg_no_to = _make_config(email_to=None)
    assert notify.maybe_notify(state, cfg_no_to, "finished") == "skipped:incomplete-config"


def test_maybe_notify_send_and_deduplication(monkeypatch):
    fake_smtp = FakeSMTP("smtp.example.com", 587)
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: fake_smtp)

    config = _make_config(notify_on=["finished", "blocked"])
    state = RunState(run_id="run-dedup", goal="dedup test", started_at=100.0, config={})

    # First call: sends
    res1 = notify.maybe_notify(state, config, "finished", password="secret-password")
    assert res1 == "sent"
    assert fake_smtp.starttls_called is True
    assert fake_smtp.login_called is True
    assert fake_smtp.username == "user@example.com"
    assert fake_smtp.password == "secret-password"
    assert len(fake_smtp.sent_messages) == 1

    # Second call with same (run_id, event): skipped already-sent
    res2 = notify.maybe_notify(state, config, "finished", password="secret-password")
    assert res2 == "skipped:already-sent"
    assert len(fake_smtp.sent_messages) == 1

    # Force send sends even if in _sent
    res_forced = notify.maybe_notify(state, config, "finished", password="secret-password", force=True)
    assert res_forced == "sent"
    assert len(fake_smtp.sent_messages) == 2

    # Different event for same run_id: sends
    res_blocked = notify.maybe_notify(state, config, "blocked", password="secret-password")
    assert res_blocked == "sent"
    assert len(fake_smtp.sent_messages) == 3


def test_maybe_notify_error_handling(monkeypatch):
    def failing_smtp(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "Cannot connect to SMTP server")

    monkeypatch.setattr(smtplib, "SMTP", failing_smtp)
    config = _make_config()
    state = RunState(run_id="run-err", goal="error test", started_at=100.0, config={})

    res = notify.maybe_notify(state, config, "finished")
    assert res.startswith("error:")
    assert "Cannot connect" in res
    # Ensure error does not record in _sent
    assert ("run-err", "finished") not in notify._sent


def test_send_tls_and_login_options(monkeypatch):
    fake_smtp = FakeSMTP("smtp.example.com", 25)
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: fake_smtp)

    # TLS disabled, no username/password
    config = _make_config(smtp_use_tls=False, smtp_username=None)
    state = RunState(run_id="run-notls", goal="no tls", started_at=100.0, config={})

    res = notify.maybe_notify(state, config, "finished")
    assert res == "sent"
    assert fake_smtp.starttls_called is False
    assert fake_smtp.login_called is False


def test_send_test_email(monkeypatch):
    fake_smtp = FakeSMTP("smtp.example.com", 587)
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: fake_smtp)

    # 1. Disabled/None
    cfg_none = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
        notifications=None,
    )
    assert notify.send_test_email(cfg_none) == "skipped:disabled"

    # 2. Incomplete
    cfg_inc = _make_config(smtp_host=None)
    assert notify.send_test_email(cfg_inc) == "skipped:incomplete-config"

    # 3. Successful test email
    cfg_valid = _make_config()
    res = notify.send_test_email(cfg_valid, password="pw")
    assert res == "sent"
    assert len(fake_smtp.sent_messages) == 1
    sent_msg = fake_smtp.sent_messages[0]
    assert sent_msg["Subject"] == "agentflow — test email"
    assert "test notification" in sent_msg.get_content()

    # 4. Error during send
    def failing_smtp(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, "Auth failed")

    monkeypatch.setattr(smtplib, "SMTP", failing_smtp)
    res_err = notify.send_test_email(cfg_valid, password="bad")
    assert res_err.startswith("error:")


def test_orchestrator_no_notifications_never_constructs_smtp(tmp_path, monkeypatch):
    from agentflow.backends.base import RunResult, Usage
    from agentflow.orchestrator import run_workflow

    class SentinelSMTP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("smtplib.SMTP should never be constructed when notifications are disabled!")

    monkeypatch.setattr(smtplib, "SMTP", SentinelSMTP)

    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
        notifications=None,
    )

    backend = MagicMock()
    backend.run.side_effect = [
        RunResult(success=True, text="Plan", usage=Usage("mock", "m", 10, 10, 0.001), raw={}),
        RunResult(success=True, text="Build done\nVERIFY_RESULT: PASS", usage=Usage("mock", "m", 10, 10, 0.001), raw={}),
        RunResult(success=True, text="VERIFY_RESULT: PASS", usage=Usage("mock", "m", 10, 10, 0.001), raw={}),
    ]

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._commit_and_push", return_value={"pushed": True}):
            with patch("agentflow.orchestrator._repo_context", return_value=""):
                state = run_workflow("test goal", config, str(repo), database_path=db_path)

    assert state.finished_at is not None


def test_orchestrator_notifications_on_finish_and_fatal_blocker(tmp_path, monkeypatch):
    from agentflow.backends.base import RunResult, Usage
    from agentflow.database import list_events
    from agentflow.orchestrator import run_workflow

    fake_smtp = FakeSMTP("smtp.example.com", 587)
    monkeypatch.setattr(smtplib, "SMTP", lambda *args, **kwargs: fake_smtp)

    repo = tmp_path / "repo"
    repo.mkdir()
    db_path = tmp_path / "test.db"

    config = _make_config(notify_on=["finished", "blocked"])
    config.max_cost_usd = 0.05

    # Review step costs 0.10, triggering fatal budget abort
    backend = MagicMock()
    backend.run.side_effect = [
        RunResult(success=True, text="Plan", usage=Usage("mock", "m", 100, 100, 0.10), raw={}),
    ]

    with patch("agentflow.orchestrator.BACKENDS") as mock_backends:
        mock_backends.__getitem__ = MagicMock(return_value=lambda model: backend)
        with patch("agentflow.orchestrator._repo_context", return_value=""):
            state = run_workflow("expensive goal", config, str(repo), database_path=db_path)

    assert state.finished_at is not None
    assert len(state.blockers) == 1
    assert state.blockers[0]["fatal"] is True

    # Both "blocked" and "finished" messages sent
    assert len(fake_smtp.sent_messages) == 2
    subjects = [m["Subject"] for m in fake_smtp.sent_messages]
    assert any("run blocked" in s for s in subjects)
    assert any("run finished" in s for s in subjects)

    # Check notification events in database
    events = list_events(state.run_id, path=db_path)
    notif_events = [e for e in events if e["type"] == "notification"]
    assert len(notif_events) == 2
    event_types = [e["payload"]["event"] for e in notif_events]
    assert "blocked" in event_types
    assert "finished" in event_types
    for e in notif_events:
        assert e["payload"]["result"] == "sent"

