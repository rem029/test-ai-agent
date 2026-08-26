# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Individual software developers using local multi-agent AI workflows (Claude Code, Google Antigravity, OpenRouter) to build and verify software tasks.

## Product Purpose
agentflow orchestrates multi-agent dev workflows (review, build, verify, iterate, push) with pluggable backends per role and provides a local admin web UI for monitoring runs, starting tasks, and editing configurations.

## Positioning
A deterministic Python-driven orchestrator with zero LLM in the coordination loop, per-step cost & token tracking, local file persistence, and a lightweight web interface (FastAPI + Jinja2 + htmx) without heavy frontend build tools or external CDN dependencies.

## Operating Context
Personal local dev environments and hosted code-server/Coolify setups (reverse-proxied to port 4200). Single-user operation, bound to local or container network.

## Capabilities and Constraints
- Review, build, and verify workflows with auto-iteration on failure.
- Live progress polling via htmx fragments against local JSON run state.
- One-at-a-time task execution with thread concurrency lock.
- Backend configuration editing with Pydantic validation before writing YAML.
- Zero client-side build step; vendored htmx.

## Product Principles
- Clarity and scanability over decorative complexity.
- Production-grade craft with robust dark/light color schemes and accessible contrasts.
- Immediate visual feedback for run state (running, pushed, failed, cost, iteration breakdown).
- Lightweight and fast with zero CDN dependencies.
