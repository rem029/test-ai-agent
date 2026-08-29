"""agentflow CLI entrypoint.

Usage model (see PLAN.md, Interface section): cd into the target repo, then
run `agentflow "<goal>"`, same pattern as `claude`.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import sys

from .backends import BACKENDS
from .config import Config, CredentialsConfig, DEFAULTS, DEFAULT_CONFIG_PATH, dump_config, load_config
from .models import get_all_models
from .orchestrator import RunInProgressError, run_workflow
from .tools import list_tools


def _build_backend(role_name: str, role_config):
    backend_cls = BACKENDS[role_config.backend]
    return backend_cls(model=role_config.model)


def print_models(backend_filter: str | None = None) -> int:
    all_models = get_all_models()
    backends = [backend_filter] if backend_filter in all_models else list(all_models.keys())
    print("=== Available Models & Pricing ===")
    for b in backends:
        models = all_models.get(b, [])
        print(f"\n[Backend: {b}] ({len(models)} models)")
        for m in models:
            rec = " ★ (recommended)" if m.get("recommended") else ""
            pricing = m.get("pricing", "N/A")
            desc = m.get("description", "")
            print(f"  • {m['id']:<35} {pricing:<30} {desc}{rec}")
    return 0


def run_checks(config_path: str) -> int:

    config = load_config(config_path)

    seen: dict[str, object] = {}
    for role_name, role_config in config.roles().items():
        key = f"{role_config.backend}:{role_config.model or ''}"
        if key not in seen:
            seen[key] = (role_name, _build_backend(role_name, role_config))

    ok = True
    for _, (role_name, backend) in seen.items():
        result = backend.health_check()
        status = "OK" if result.ok else "FAIL"
        print(f"[{status}] {role_name} -> {result.backend}: {result.detail}")
        ok = ok and result.ok

    return 0 if ok else 1


def _apply_overrides(config, args):
    if args.permissions:
        config.permissions = args.permissions
    if args.max_cost_usd is not None:
        config.max_cost_usd = args.max_cost_usd
    if args.review_backend:
        config.review.backend = args.review_backend
    if args.review_model is not None:
        config.review.model = args.review_model or None
    if args.build_backend:
        config.build.backend = args.build_backend
    if args.build_model is not None:
        config.build.model = args.build_model or None
    if args.verify_backend:
        config.verify.backend = args.verify_backend
    if args.verify_model is not None:
        config.verify.model = args.verify_model or None
    return config


def main(argv: list[str] | None = None) -> int:
    from .dotenv import load_env

    load_env()

    parser = argparse.ArgumentParser(prog="agentflow")
    parser.add_argument("goal", nargs="?", help="The goal to work toward")
    parser.add_argument(
        "--config",
        default=None,
        help=f"Path to backend config (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that each configured backend is installed/authenticated",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the installed agentflow version and exit",
    )
    parser.add_argument(
        "--list-models",
        nargs="?",
        const="all",
        metavar="BACKEND",
        help="List available models and pricing for all or a specific backend (openrouter, claude-code, antigravity)",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List available tools that agents can invoke",
    )
    parser.add_argument(
        "--mcp-check",
        action="store_true",
        help="Connect to each configured MCP server and report its tools",
    )
    parser.add_argument(
        "--list-sessions",
        action="store_true",
        help="List active sessions for the current repository",
    )
    parser.add_argument(
        "--show-memory",
        action="store_true",
        help="Print composed memory (global and project) for current repository",
    )
    parser.add_argument(
        "--edit-memory",
        choices=["global", "project"],
        help="Open editor on global or project memory file",
    )
    parser.add_argument(
        "--say",
        metavar="RUN_ID",
        help="Send a steer message to an active or pending run (message body in positional goal)",
    )
    parser.add_argument(
        "--note",
        metavar="RUN_ID",
        help="Attach a note to an active or pending run (note body in positional goal)",
    )
    parser.add_argument(
        "--stop",
        metavar="RUN_ID",
        help="Send a stop signal to halt an active run",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume an existing session with a follow-up turn",
    )
    parser.add_argument(
        "--permissions",
        choices=["auto", "prompt", "deny"],
        help="Tool permission policy for mutating operations (auto, prompt, deny)",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        help="Maximum cumulative budget in USD for this workflow run",
    )
    parser.add_argument("--review-backend", choices=list(BACKENDS), help="Override review backend")
    parser.add_argument("--review-model", help="Override review model")
    parser.add_argument("--build-backend", choices=list(BACKENDS), help="Override build backend")
    parser.add_argument("--build-model", help="Override build model")
    parser.add_argument("--verify-backend", choices=list(BACKENDS), help="Override verify backend")
    parser.add_argument("--verify-model", help="Override verify model")
    parser.add_argument(
        "--openrouter-key",
        metavar="KEY",
        help="Use an OpenRouter API key for this invocation without writing it to disk",
    )
    parser.add_argument(
        "--set-openrouter-key",
        metavar="KEY",
        help="Save an OpenRouter API key to .env and exit",
    )
    parser.add_argument(
        "--project",
        action="append",
        metavar="PATH",
        help="Project root the web UI can target (repeatable; default: current directory)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the local admin web UI",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the web UI to when using --serve (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8420,
        help="Port to bind the web UI to when using --serve (default: 8420)",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email using configured notifications settings and exit",
    )
    args = parser.parse_args(argv)

    if args.openrouter_key:
        os.environ["OPENROUTER_API_KEY"] = args.openrouter_key

    # Compute the effective config path (user-provided or default)
    config_path = args.config if args.config is not None else DEFAULT_CONFIG_PATH

    # If user explicitly passed --config but the path does not exist, error out
    if (
        args.config is not None
        and not os.path.isfile(args.config)
        and args.set_openrouter_key is None
    ):
        print(f"Error: config file not found: {args.config}", file=sys.stderr)
        return 1

    if args.set_openrouter_key is not None:
        try:
            cfg = load_config(config_path)
        except Exception:
            cfg = Config(
                review=DEFAULTS["review"],
                build=DEFAULTS["build"],
                verify=DEFAULTS["verify"],
            )
        if cfg.credentials is None:
            cfg.credentials = CredentialsConfig()
        cfg.credentials.openrouter_api_key = args.set_openrouter_key
        dump_config(cfg, config_path)
        print(f"OpenRouter API key saved to {config_path}")
        return 0

    if args.version:
        print(importlib.metadata.version("agentflow"))
        return 0

    if args.test_email:
        from . import notify

        config = load_config(config_path)
        res = notify.send_test_email(config)
        print(f"Test email: {res}")
        return 0 if res == "sent" else 1

    if args.list_models is not None:
        backend_filter = None if args.list_models == "all" else args.list_models
        return print_models(backend_filter)

    if args.list_tools:
        print("=== Available Tools ===")
        for name in list_tools():
            print(f"  • {name}")
        try:
            cfg = None
            try:
                cfg = load_config(args.config or DEFAULT_CONFIG_PATH)
            except Exception:
                cfg = None
            if cfg and cfg.mcp_servers:
                enabled_servers = [s for s in cfg.mcp_servers if s.enabled]
                if enabled_servers:
                    from .mcp import MCPManager, discover_mcp_tools

                    manager = MCPManager(enabled_servers, cwd=os.getcwd())
                    try:
                        manager.start()
                        mcp_tools = discover_mcp_tools(manager)
                        print("\n=== MCP Tools ===")
                        for name in sorted(mcp_tools):
                            print(f"  • {name}")
                        if manager.errors:
                            print("\n=== MCP Errors ===")
                            for sname, err in manager.errors.items():
                                print(f"  • {sname}: {err}")
                    finally:
                        manager.close()
        except Exception:
            pass
        return 0

    if args.mcp_check:
        try:
            config = load_config(config_path)
        except Exception as exc:
            print(f"Error loading config: {exc}", file=sys.stderr)
            return 1

        if not config.mcp_servers:
            print("No MCP servers configured.")
            return 0

        enabled_servers = [s for s in config.mcp_servers if s.enabled]
        from .mcp import MCPManager

        manager = MCPManager(enabled_servers, cwd=os.getcwd())
        try:
            manager.start()
            tools_by_server: dict[str, list[str]] = {s.name: [] for s in enabled_servers}
            for tool in manager.list_tools():
                tools_by_server.setdefault(tool.server_name, []).append(tool.remote_name)

            for s in config.mcp_servers:
                if not s.enabled:
                    continue
                if s.name in manager.errors:
                    print(f"[ERROR] {s.name}: {manager.errors[s.name]}")
                else:
                    remote_tools = tools_by_server.get(s.name, [])
                    tools_str = f": {', '.join(remote_tools)}" if remote_tools else ""
                    print(f"[OK] {s.name}: {len(remote_tools)} tool(s){tools_str}")
        finally:
            manager.close()

        return 1 if any(s.name in manager.errors for s in enabled_servers) else 0

    if args.list_sessions:
        import time
        from .database import list_sessions

        sessions = list_sessions(os.getcwd())
        print("=== Saved Sessions ===")
        if not sessions:
            print("  (no sessions found)")
        for s in sessions:
            print(f"  • {s['session_id']}: {s.get('title', '')} (updated {time.ctime(s['updated_at'])})")
        return 0

    if args.show_memory:
        from .memory import compose_memory

        mem = compose_memory(os.getcwd())
        if mem:
            print(mem)
        else:
            print("(no memory configured)")
        return 0

    if args.edit_memory:
        import shutil
        import subprocess
        from .memory import global_memory_path, project_memory_path

        path = (
            global_memory_path()
            if args.edit_memory == "global"
            else project_memory_path(os.getcwd())
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        editor = os.environ.get("EDITOR")
        if not editor:
            if shutil.which("nano"):
                editor = "nano"
            elif shutil.which("vi"):
                editor = "vi"

        if not editor:
            print(
                f"Error: no editor found ($EDITOR unset, nano/vi not on PATH). Memory file: {path}",
                file=sys.stderr,
            )
            return 1

        subprocess.call([editor, str(path)])
        return 0

    if args.say:
        if not args.goal:
            print("Error: message body is required when using --say", file=sys.stderr)
            return 1
        from .database import add_pending_message, load_run

        if load_run(args.say, os.getcwd()) is None:
            print(
                f"Warning: no run '{args.say}' found for this repo; message stored anyway",
                file=sys.stderr,
            )
        add_pending_message(args.say, args.goal, kind="steer")
        print(f"Message sent to run {args.say}")
        return 0

    if args.note:
        if not args.goal:
            print("Error: note body is required when using --note", file=sys.stderr)
            return 1
        from .database import add_pending_message, load_run

        if load_run(args.note, os.getcwd()) is None:
            print(
                f"Warning: no run '{args.note}' found for this repo; message stored anyway",
                file=sys.stderr,
            )
        add_pending_message(args.note, args.goal, kind="note")
        print(f"Note sent to run {args.note}")
        return 0

    if args.stop:
        from .database import add_control_signal, load_run

        if load_run(args.stop, os.getcwd()) is None:
            print(
                f"Warning: no run '{args.stop}' found for this repo; message stored anyway",
                file=sys.stderr,
            )
        add_control_signal(args.stop, "stop")
        print(f"Stop signal sent to run {args.stop}")
        return 0

    if args.serve:
        if args.project:
            for p in args.project:
                if not os.path.isdir(p):
                    print(f"Error: project path not found: {p}", file=sys.stderr)
                    return 1
        projects = [os.path.abspath(p) for p in (args.project or [])] or [os.getcwd()]
        from .web.app import create_app
        import uvicorn

        uvicorn.run(
            create_app(cwd=projects[0], config_path=config_path, projects=projects),
            host=args.host,
            port=args.port,
        )
        return 0

    if args.check:
        return run_checks(config_path)

    if args.resume:
        from .database import get_session

        if not get_session(args.resume):
            print(f"Error: session not found: {args.resume}", file=sys.stderr)
            return 1

    config = load_config(config_path)
    _apply_overrides(config, args)

    if not args.goal:
        from .tui import run_repl

        return run_repl(config, cwd=os.getcwd(), session_id=args.resume)

    try:
        state = run_workflow(args.goal, config, cwd=os.getcwd(), session_id=args.resume)
    except RunInProgressError as err:
        print(f"Error: a run is already in progress for {err.cwd}", file=sys.stderr)
        return 1

    return 0 if state.pushed and state.pushed.get("pushed") else 1


if __name__ == "__main__":
    sys.exit(main())
