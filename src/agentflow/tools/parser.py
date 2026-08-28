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


_LOOSE_TOOL_CALL_RE = re.compile(
    r"<[^\n>]*?tool_call\s*>\s*(\{.*?\})\s*<\s*/[^\n>]*?tool_call\s*>",
    re.DOTALL | re.IGNORECASE,
)
_UNCLOSED_TOOL_CALL_RE = re.compile(
    r"<[^\n>]*?tool_call\s*>\s*(\{.*\})\s*$",
    re.DOTALL | re.IGNORECASE,
)


def _extract_loose_tool_calls(text: str) -> list[ParsedToolRequest]:
    """Extract tool calls from loose/DSML tool_call blocks containing JSON payloads."""
    calls: list[ParsedToolRequest] = []
    last_end = 0
    for match in _LOOSE_TOOL_CALL_RE.finditer(text):
        last_end = match.end()
        raw_json = match.group(1).strip()
        try:
            data = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("name") and isinstance(data["name"], str):
            args = data.get("args")
            calls.append(
                ParsedToolRequest(
                    name=data["name"],
                    args=args if isinstance(args, dict) else {},
                )
            )

    # Check for an unclosed trailing block after the last matched closed block
    remainder = text[last_end:]
    unclosed_match = _UNCLOSED_TOOL_CALL_RE.search(remainder)
    if unclosed_match:
        raw_json = unclosed_match.group(1).strip()
        try:
            data = json.loads(raw_json)
            if isinstance(data, dict) and data.get("name") and isinstance(data["name"], str):
                args = data.get("args")
                calls.append(
                    ParsedToolRequest(
                        name=data["name"],
                        args=args if isinstance(args, dict) else {},
                    )
                )
        except (json.JSONDecodeError, TypeError):
            pass

    return calls


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

    Supports loose/DSML tool call blocks, standard <tool_call> XML blocks,
    and ```json fenced blocks.
    """
    loose_calls = _extract_loose_tool_calls(text)
    if loose_calls:
        return loose_calls
    xml_calls = _extract_xml_tool_calls(text)
    if xml_calls:
        return xml_calls
    return _extract_json_tool_calls(text)
