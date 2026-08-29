"""Background web-console server for the REPL `/serve` command."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time


@dataclass
class ServeState:
    host: str
    port: int
    url: str
    thread: threading.Thread


_STATE: ServeState | None = None
_LOCK = threading.Lock()

DEFAULT_SERVE_PORT = 8420
DEFAULT_SERVE_HOST = "127.0.0.1"


def current() -> ServeState | None:
    return _STATE


def start_web_server(
    *,
    cwd: str,
    config_path: str,
    database_path: Path | None,
    host: str = DEFAULT_SERVE_HOST,
    port: int = DEFAULT_SERVE_PORT,
) -> tuple[ServeState, bool]:
    """Start the web console in a daemon thread. Returns (state, already_running).

    Idempotent: a second call returns the existing state with already_running=True
    (the host/port args are ignored once one is running).
    """
    global _STATE
    with _LOCK:
        if _STATE is not None and _STATE.thread.is_alive():
            return _STATE, True

        import uvicorn
        from ..web.app import create_app

        app = create_app(
            cwd=cwd,
            config_path=config_path,
            database_path=database_path,
            projects=[cwd],
        )
        uv_config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(uv_config)

        # Bind the socket synchronously here so a port-in-use error surfaces to
        # the caller instead of dying silently in the thread.
        # (uvicorn.Server has no public sync bind; instead we let the thread
        #  start and check server.started with a short timeout.)
        thread = threading.Thread(target=server.run, name="agentflow-web", daemon=True)
        thread.start()

        # Wait briefly for startup; server.started flips True once serving.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if getattr(server, "started", False):
                break
            if not thread.is_alive() or getattr(server, "should_exit", False):
                raise RuntimeError(
                    f"web server failed to start on {host}:{port} "
                    f"(port already in use?)"
                )
            time.sleep(0.05)
        else:
            # didn't confirm start but thread alive - assume slow start, proceed
            pass

        display_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
        url = f"http://{display_host}:{port}"
        _STATE = ServeState(host=host, port=port, url=url, thread=thread)
        return _STATE, False
