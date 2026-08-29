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


_NAME_RE = re.compile(r"^[A-Za-z_]\w*$")
_OPEN_TOOL_CALL_RE = re.compile(r"<[^>]*?tool_calls?\b[^>]*>", re.IGNORECASE)
_CLOSE_TOOL_CALL_RE = re.compile(r"</[^>]*?tool_calls?\b[^>]*>", re.IGNORECASE)

_INVOKE_NAME_ATTR_RE = re.compile(r"<[^>]*?invoke\b[^>]*?name\s*=\s*[\"']([A-Za-z_]\w*)[\"'][^>]*>", re.IGNORECASE)
_INVOKE_NAME_ATTR_ANY_RE = re.compile(r"invoke\s+name\s*=\s*[\"']([A-Za-z_]\w*)[\"']", re.IGNORECASE)
_INVOKE_BARE_NAME_RE = re.compile(r"<[^>]*?invoke[^>]*?>\s*([A-Za-z_]\w*)\s*<", re.IGNORECASE)
_FUNCTION_SEP_NAME_RE = re.compile(r"function\s*[｜|]\s*sep\s*[｜|]\s*([A-Za-z_]\w*)", re.IGNORECASE)

_STRIP_CLOSED_TOOL_CALL_RE = re.compile(
    r"<[^>/]*?tool_calls?\b[^>]*>.*?(?:<[^>]*?/[^>]*?(?:tool_calls?|DSML)\b[^>]*>|<[^>]*?(?:tool_calls?|DSML)\b[^>]*?/[^>]*>)",
    re.DOTALL | re.IGNORECASE,
)
_STRIP_UNCLOSED_TOOL_CALL_RE = re.compile(
    r"<[^>/]*?tool_calls?\b[^>]*>.*$", re.DOTALL | re.IGNORECASE
)
_STRIP_CLOSED_INVOKE_RE = re.compile(
    r"<[^>/]*?invoke\b[^>]*>.*?(?:<[^>]*?/[^>]*?invoke\b[^>]*>|<[^>]*?invoke\b[^>]*?/[^>]*>)",
    re.DOTALL | re.IGNORECASE,
)
_STRIP_FILE_BLOCK_RE = re.compile(r"```FILE:[^\n]*\n.*?(?:```|$)", re.DOTALL)
_STRIP_LEFTOVER_TAGS_RE = re.compile(
    r"</?[^>]*?(?:DSML|tool_calls?|invoke|parameter)[^>]*>", re.IGNORECASE
)




def _extract_balanced_json_objects(text: str) -> list[str]:
    """Find all top-level balanced {...} substrings respecting string quotes and escapes."""
    results: list[str] = []
    in_string = False
    escape = False
    depth = 0
    start = -1

    for i, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif char == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start != -1:
                        results.append(text[start : i + 1])
                        start = -1

    return results


def _parse_single_unit(unit: str) -> ParsedToolRequest | None:
    """Parse a single tool call unit/block into a ParsedToolRequest."""
    json_candidates = _extract_balanced_json_objects(unit)

    # 1. JSON object with string "name" key anywhere in the block -> use its name and args
    for raw in json_candidates:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                raw_name = data.get("name")
                if isinstance(raw_name, str) and _NAME_RE.match(raw_name):
                    args = data.get("args")
                    return ParsedToolRequest(
                        name=raw_name,
                        args=args if isinstance(args, dict) else {},
                    )
        except (json.JSONDecodeError, TypeError):
            continue

    # 2. invoke name="X"
    name: str | None = None
    m2 = _INVOKE_NAME_ATTR_ANY_RE.search(unit)
    if m2 and _NAME_RE.match(m2.group(1)):
        name = m2.group(1)

    # 3. <...invoke...> Name < (bare-word name inside invoke tags - Format B)
    if not name:
        m3 = _INVOKE_BARE_NAME_RE.search(unit)
        if m3 and _NAME_RE.match(m3.group(1)):
            name = m3.group(1)

    # 4. function | sep | Name (deepseek-native bonus)
    if not name:
        m4 = _FUNCTION_SEP_NAME_RE.search(unit)
        if m4 and _NAME_RE.match(m4.group(1)):
            name = m4.group(1)

    if not name:
        return None

    # Get ARGS: first balanced {...} yielding a dict that is not the {"name": ...} wrapper
    args: dict[str, Any] = {}
    for raw in json_candidates:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                args = data
                break
        except (json.JSONDecodeError, TypeError):
            continue

    return ParsedToolRequest(name=name, args=args)


def _extract_loose_tool_calls(text: str) -> list[ParsedToolRequest]:
    """Extract tool calls from loose/DSML tool_call blocks in various formats."""
    calls: list[ParsedToolRequest] = []
    pos = 0

    while pos < len(text):
        open_match = _OPEN_TOOL_CALL_RE.search(text, pos)
        if not open_match:
            break

        start_content = open_match.end()
        close_match = _CLOSE_TOOL_CALL_RE.search(text, start_content)
        if close_match:
            block = text[start_content : close_match.start()]
            pos = close_match.end()
        else:
            block = text[start_content:]
            pos = len(text)

        # Check if block contains multiple invoke sub-blocks
        invoke_name_matches = list(_INVOKE_NAME_ATTR_RE.finditer(block))
        if invoke_name_matches:
            for i, match in enumerate(invoke_name_matches):
                start_sub = match.start()
                end_sub = invoke_name_matches[i + 1].start() if i + 1 < len(invoke_name_matches) else len(block)
                sub_unit = block[start_sub:end_sub]
                req = _parse_single_unit(sub_unit)
                if req:
                    calls.append(req)
            continue

        bare_invoke_matches = list(_INVOKE_BARE_NAME_RE.finditer(block))
        if len(bare_invoke_matches) > 1:
            for i, match in enumerate(bare_invoke_matches):
                start_sub = match.start()
                end_sub = bare_invoke_matches[i + 1].start() if i + 1 < len(bare_invoke_matches) else len(block)
                sub_unit = block[start_sub:end_sub]
                req = _parse_single_unit(sub_unit)
                if req:
                    calls.append(req)
            continue

        req = _parse_single_unit(block)
        if req:
            calls.append(req)

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


def strip_tool_blocks(text: str) -> str:
    """Strip tool-call blocks, DSML tags, standalone JSON tool calls, and FILE blocks from prose text."""
    if not text:
        return ""

    # 1. Strip closed tool_call / tool_calls blocks
    s = _STRIP_CLOSED_TOOL_CALL_RE.sub("", text)

    # 2. Strip unclosed trailing tool_call / tool_calls block
    s = _STRIP_UNCLOSED_TOOL_CALL_RE.sub("", s)

    # 3. Strip standalone closed invoke blocks
    s = _STRIP_CLOSED_INVOKE_RE.sub("", s)

    # 4. Strip FILE blocks (closed and trailing unclosed)
    s = _STRIP_FILE_BLOCK_RE.sub("", s)

    # 5. Filter bare lines that are a single JSON object with valid "name" key, and strip leftover markup tags
    lines = s.split("\n")
    kept_lines: list[str] = []
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                if isinstance(data, dict):
                    raw_name = data.get("name")
                    if isinstance(raw_name, str) and _NAME_RE.match(raw_name):
                        continue
            except (json.JSONDecodeError, TypeError):
                pass

        # Defensive: strip leftover markup tags
        stripped_line = _STRIP_LEFTOVER_TAGS_RE.sub("", line)
        if stripped_line.strip() or not line.strip():
            kept_lines.append(stripped_line)

    result = "\n".join(kept_lines)
    # Collapse 3+ blank-line runs to max 1 blank line (max 2 consecutive newlines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()

