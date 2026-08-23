"""Command-line interface for agentflow: a multi-agent software development workflow orchestrator."""

import argparse
import sys
import importlib.metadata

from .backends import BACKENDS
from .config import DEFAULT_CONFIG_PATH, load_config
from .orchestrator import run_workflow


def _build_backend(config):
    """Build the backend instance from configuration."""
    backend_name = config.get("backend", "anthropic")
    backend_cls = BACKENDS.get(backend_name)
    if not backend_cls:
        print(f"Error: Unknown backend '{backend_name}'. Available backends: {', '.join(BACKENDS.keys())}", file=sys.stderr)
        sys.exit(1)
    return backend_cls(config.get(backend_name, {}))


def run_checks(config):
    """Run health checks on all configured backends."""
    for name, backend_cls in BACKENDS.items():
        backend_config = config.get(name, {})
        if not backend_config:
            print(f"  {name}: not configured (skipped)")
            continue
        try:
            backend = backend_cls(backend_config)
            backend.check()
            print(f"  {name}: OK")
        except Exception as e:
            print(f"  {name}: FAIL ({e})")


def main(argv=None):
    """Main entry point for the agentflow CLI."""
    parser = argparse.ArgumentParser(
        description="agentflow: Multi-agent software development workflow orchestrator"
    )
    parser.add_argument("--version", action="store_true", help="Print the installed agentflow version and exit")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to config file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check backend connectivity and configuration",
    )
    parser.add_argument("goal", nargs="?", help="The software development goal to accomplish")

    args = parser.parse_args(argv)

    if args.version:
        print(importlib.metadata.version("agentflow"))
        return 0

    config = load_config(args.config)

    if args.check:
        run_checks(config)
        return 0

    if args.goal:
        backend = _build_backend(config)
        run_workflow(goal=args.goal, backend=backend, config=config)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
