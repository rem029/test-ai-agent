"""Session-scoped permission broker for TUI tool approvals."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PermissionRequest:
    tool_name: str
    args: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result_box: list[str] = field(default_factory=lambda: ["deny"])


class SessionPermissionBroker:
    """Brokers tool confirmation requests between orchestrator threads and the REPL UI."""

    def __init__(self, allowed_tools: set[str] | None = None) -> None:
        self._allowed_tools: set[str] = set(allowed_tools or ())
        self._queue: queue.Queue[PermissionRequest] = queue.Queue()
        self._pending: list[PermissionRequest] = []
        self._lock = threading.Lock()

    @property
    def allowed_tools(self) -> set[str]:
        with self._lock:
            return set(self._allowed_tools)

    def handler(self, tool_name: str, args: dict[str, Any]) -> str:
        """Called from worker threads to request permission before executing a tool."""
        with self._lock:
            if tool_name in self._allowed_tools:
                return "allow"
            req = PermissionRequest(tool_name=tool_name, args=args)
            self._pending.append(req)

        self._queue.put(req)
        req.event.wait()

        with self._lock:
            if req in self._pending:
                self._pending.remove(req)
            answer = req.result_box[0]
            if answer == "allow_session":
                self._allowed_tools.add(tool_name)
        return answer

    def poll(self) -> PermissionRequest | None:
        """Non-blocking poll for pending requests."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def respond(self, request: PermissionRequest, answer: str) -> None:
        """Respond to a pending permission request and wake up the waiting thread."""
        with self._lock:
            if answer == "allow_session":
                self._allowed_tools.add(request.tool_name)
            request.result_box[0] = answer
            request.event.set()

    def cancel_all(self) -> None:
        """Cancel all pending requests with a default 'deny' answer."""
        with self._lock:
            while not self._queue.empty():
                try:
                    req = self._queue.get_nowait()
                    req.result_box[0] = "deny"
                    req.event.set()
                except queue.Empty:
                    break
            for req in list(self._pending):
                req.result_box[0] = "deny"
                req.event.set()
            self._pending.clear()
