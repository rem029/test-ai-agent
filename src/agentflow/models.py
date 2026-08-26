"""Model catalog, discovery, and pricing for agentflow backends.

Provides curated model selections and pricing metadata for OpenRouter,
Claude Code, and Antigravity, plus dynamic fetching from OpenRouter when
available.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

# Curated models with known pricing ($ per 1M tokens) and descriptions.
CURATED_MODELS: dict[str, list[dict[str, Any]]] = {
    "openrouter": [
        {
            "id": "deepseek/deepseek-chat",
            "name": "DeepSeek V3 (Chat)",
            "pricing": "$0.14 / $0.28 per M",
            "prompt_cost": 0.14,
            "completion_cost": 0.28,
            "description": "Smart, highly capable generalist at ultra-low cost",
            "recommended": True,
        },
        {
            "id": "deepseek/deepseek-v4-flash",
            "name": "DeepSeek Flash",
            "pricing": "$0.05 / $0.10 per M",
            "prompt_cost": 0.05,
            "completion_cost": 0.10,
            "description": "Fastest and lowest cost DeepSeek model",
            "recommended": True,
        },
        {
            "id": "qwen/qwen-2.5-coder-32b-instruct",
            "name": "Qwen 2.5 Coder 32B",
            "pricing": "$0.07 / $0.14 per M",
            "prompt_cost": 0.07,
            "completion_cost": 0.14,
            "description": "Specialized high-performing coding model",
            "recommended": True,
        },
        {
            "id": "google/gemini-2.0-flash-001",
            "name": "Gemini 2.0 Flash",
            "pricing": "$0.10 / $0.40 per M",
            "prompt_cost": 0.10,
            "completion_cost": 0.40,
            "description": "Fast, high-reasoning multimodal model",
            "recommended": True,
        },
        {
            "id": "google/gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "pricing": "$0.15 / $0.60 per M",
            "prompt_cost": 0.15,
            "completion_cost": 0.60,
            "description": "Latest flash-tier Gemini model",
        },
        {
            "id": "meta-llama/llama-3.3-70b-instruct",
            "name": "Llama 3.3 70B Instruct",
            "pricing": "$0.12 / $0.30 per M",
            "prompt_cost": 0.12,
            "completion_cost": 0.30,
            "description": "Top-tier open weights model for reasoning",
        },
        {
            "id": "anthropic/claude-3.5-haiku",
            "name": "Claude 3.5 Haiku",
            "pricing": "$0.80 / $4.00 per M",
            "prompt_cost": 0.80,
            "completion_cost": 4.00,
            "description": "Fast Anthropic model via OpenRouter API",
        },
        {
            "id": "anthropic/claude-3.5-sonnet",
            "name": "Claude 3.5 Sonnet",
            "pricing": "$3.00 / $15.00 per M",
            "prompt_cost": 3.00,
            "completion_cost": 15.00,
            "description": "Industry-leading coding benchmark model",
        },
    ],
    "claude-code": [
        {
            "id": "claude-3-7-sonnet",
            "name": "Claude 3.7 Sonnet (Latest)",
            "pricing": "Claude Subscription (included)",
            "description": "Latest hybrid reasoning Sonnet model",
            "recommended": True,
        },
        {
            "id": "claude-3-5-sonnet",
            "name": "Claude 3.5 Sonnet",
            "pricing": "Claude Subscription (included)",
            "description": "Standard coding & reasoning workhorse",
            "recommended": True,
        },
        {
            "id": "claude-3-5-haiku",
            "name": "Claude 3.5 Haiku",
            "pricing": "Claude Subscription (included)",
            "description": "Fast & lightweight Claude model",
        },
        {
            "id": "claude-3-opus",
            "name": "Claude 3 Opus",
            "pricing": "Claude Subscription (included)",
            "description": "Deep reasoning model",
        },
    ],
    "antigravity": [
        {
            "id": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "pricing": "Google Subscription / OAuth",
            "description": "High speed, long context, low latency",
            "recommended": True,
        },
        {
            "id": "gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "pricing": "Google Subscription / OAuth",
            "description": "Advanced reasoning and coding capabilities",
            "recommended": True,
        },
        {
            "id": "gemini-2.0-flash",
            "name": "Gemini 2.0 Flash",
            "pricing": "Google Subscription / OAuth",
            "description": "Fast multimodal Google model",
        },
        {
            "id": "gemini-1.5-pro",
            "name": "Gemini 1.5 Pro",
            "pricing": "Google Subscription / OAuth",
            "description": "Large 2M context window model",
        },
    ],
}


def fetch_openrouter_models(api_key: str | None = None) -> list[dict[str, Any]]:
    """Fetch live models list from OpenRouter API if reachable; fall back to curated models."""
    curated = list(CURATED_MODELS["openrouter"])
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    try:
        resp = httpx.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                live_models = []
                curated_ids = {m["id"] for m in curated}
                for item in data:
                    model_id = item.get("id", "")
                    prompt_price = float(item.get("pricing", {}).get("prompt", 0) or 0) * 1_000_000
                    comp_price = float(item.get("pricing", {}).get("completion", 0) or 0) * 1_000_000
                    pricing_str = f"${prompt_price:.2f} / ${comp_price:.2f} per M"

                    entry = {
                        "id": model_id,
                        "name": item.get("name", model_id),
                        "pricing": pricing_str,
                        "prompt_cost": prompt_price,
                        "completion_cost": comp_price,
                        "description": item.get("description", "")[:100],
                        "recommended": model_id in curated_ids,
                    }
                    live_models.append(entry)

                # Prioritize recommended models
                live_models.sort(key=lambda m: (not m.get("recommended", False), m["id"]))
                return live_models
    except Exception:
        pass

    return curated


def get_models_for_backend(backend: str) -> list[dict[str, Any]]:
    """Get list of available/recommended models for a backend with pricing."""
    if backend == "openrouter":
        return fetch_openrouter_models()
    return CURATED_MODELS.get(backend, [])


def get_all_models() -> dict[str, list[dict[str, Any]]]:
    """Get all models grouped by backend."""
    return {
        "openrouter": get_models_for_backend("openrouter"),
        "claude-code": CURATED_MODELS["claude-code"],
        "antigravity": CURATED_MODELS["antigravity"],
    }
