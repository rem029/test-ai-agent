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
from .config import DEFAULT_CONFIG_PATH, load_config
from .models import get_all_models
from .orchestrator import run_workflow


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


def main(argv: list[str] | None = None) -> int:
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
        help="Save an OpenRouter API key to the selected agentflow config and exit",
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
        from .config import dump_config

        dump_config(load_config(config_path), config_path, openrouter_api_key=args.set_openrouter_key)
        print(f"OpenRouter API key saved to {config_path}")
        return 0

    if args.version:
        print(importlib.metadata.version("agentflow"))
        return 0

    if args.list_models is not None:
        backend_filter = None if args.list_models == "all" else args.list_models
        return print_models(backend_filter)

    if args.serve:
        from .web.app import create_app
        import uvicorn

        uvicorn.run(create_app(cwd=os.getcwd(), config_path=config_path), host=args.host, port=args.port)
        return 0

    if args.check or not args.goal:
        return run_checks(config_path)

    config = load_config(config_path)
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

    state = run_workflow(args.goal, config, cwd=os.getcwd())
    return 0 if state.pushed and state.pushed.get("pushed") else 1


if __name__ == "__main__":
    sys.exit(main())
