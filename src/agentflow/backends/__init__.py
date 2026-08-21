from .antigravity import AntigravityBackend
from .base import Backend, HealthCheckResult
from .claude_code import ClaudeCodeBackend
from .openrouter import OpenRouterBackend

BACKENDS = {
    ClaudeCodeBackend.name: ClaudeCodeBackend,
    AntigravityBackend.name: AntigravityBackend,
    OpenRouterBackend.name: OpenRouterBackend,
}

__all__ = [
    "Backend",
    "HealthCheckResult",
    "ClaudeCodeBackend",
    "AntigravityBackend",
    "OpenRouterBackend",
    "BACKENDS",
]
