"""Tests for agentflow.utils module."""

from agentflow.utils import format_duration


def test_format_duration_milliseconds():
    assert format_duration(0.0) == "0ms"
    assert format_duration(0.42) == "420ms"
    assert format_duration(0.999) == "999ms"


def test_format_duration_seconds():
    assert format_duration(1.0) == "1.0s"
    assert format_duration(12.5) == "12.5s"
    assert format_duration(59.9) == "59.9s"


def test_format_duration_minutes_and_hours():
    assert format_duration(60.0) == "1m 0s"
    assert format_duration(75.0) == "1m 15s"
    assert format_duration(3600.0) == "1h 0m 0s"
    assert format_duration(3665.0) == "1h 1m 5s"


def test_format_duration_negative():
    assert format_duration(-5.0) == "0s"
