"""Parse tool requests from agent responses."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any

from pydantic import BaseModel

from .base import ToolError


class ParsedToolRequest(BaseModel):
    """A parsed tool request from an agent response."""

    name: str
    args: dict[str, Any]


def _extract_xml_tool_calls(text: str) -> list[ParsedToolRequest]:
    """Extract tool calls from <tool_call> XML blocks."""
    calls: list[ParsedToolRequest] = []
    pattern = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
    for match in pattern.finditer(text):
        inner = match.group(1).strip()
        if not inner.startswith("<"):
            # Might be JSON inside XML tags.
            try:
                data = json.loads(inner)
                calls.append(ParsedToolRequest(name=data.get("name", ""), args=data.get("args", {})))
                continue
            except json.JSONDecodeError:
                pass
        try:
            root = ET.fromstring(inner)
        except ET.ParseError:
            # Skip malformed XML blocks instead of aborting the whole parse.
            continue

        name = root.tag if root.tag != "tool_call" else root.findtext("name", "").strip()
        args_node = root.find("args")
        args: dict[str, Any] = {}
        if args_node is not None:
            for child in args_node:
                args[child.tag] = _coerce_text(child.text)
        calls.append(ParsedToolRequest(name=name, args=args))
    return calls


def _extract_json_tool_calls(text: str) -> list[ParsedToolRequest]:
    """Extract tool calls from fenced JSON blocks."""
    calls: list[ParsedToolRequest] = []
    pattern = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
    for match in pattern.finditer(text):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            calls.append(ParsedToolRequest(name=data.get("name", ""), args=data.get("args", {})))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    calls.append(
                        ParsedToolRequest(name=item.get("name", ""), args=item.get("args", {}))
                    )
    return calls


def _coerce_text(value: str | None) -> Any:
    """Coerce XML text to int/float/bool/None where possible."""
    if value is None:
        return None
    text = value.strip()
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null" or lowered == "none":
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def parse_tool_requests(text: str) -> list[ParsedToolRequest]:
    """Parse all tool requests from an agent response.

    Supports both <tool_call> XML blocks and ```json fenced blocks. XML blocks
    take precedence if present.
    """
    xml_calls = _extract_xml_tool_calls(text)
    if xml_calls:
        return xml_calls
    return _extract_json_tool_calls(text)
