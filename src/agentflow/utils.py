"""Utility helper functions for agentflow."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string.

    Examples:
        format_duration(0.42) -> "420ms"
        format_duration(12.5) -> "12.5s"
        format_duration(75.0) -> "1m 15s"
        format_duration(3665.0) -> "1h 1m 5s"
    """
    if seconds < 0:
        return "0s"
    if seconds < 1.0:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"

    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"
