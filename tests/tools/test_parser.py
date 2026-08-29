"""Tests for the tool request parser."""

from __future__ import annotations

import pytest

from agentflow.tools.parser import ParsedToolRequest, parse_tool_requests


def test_parse_xml_tool_call():
    text = """
<tool_call>
  <ReadFile>
    <args>
      <path>src/foo.py</path>
      <start_line>1</start_line>
      <end_line>10</end_line>
    </args>
  </ReadFile>
</tool_call>
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ReadFile"
    assert calls[0].args == {"path": "src/foo.py", "start_line": 1, "end_line": 10}


def test_parse_multiple_xml_tool_calls():
    text = """
<tool_call>
  <ReadFile>
    <args><path>foo.py</path></args>
  </ReadFile>
</tool_call>
<tool_call>
  <Shell>
    <args><command>echo hi</command></args>
  </Shell>
</tool_call>
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 2
    assert calls[0].name == "ReadFile"
    assert calls[1].name == "Shell"


def test_parse_json_tool_call():
    text = """
```json
{"name": "Shell", "args": {"command": "echo hello"}}
```
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "Shell"
    assert calls[0].args == {"command": "echo hello"}


def test_parse_no_tool_calls():
    assert parse_tool_requests("Just a plain response.") == []


def test_xml_takes_precedence_over_json():
    text = """
<tool_call>
  <ReadFile><args><path>foo.py</path></args></ReadFile>
</tool_call>
```json
{"name": "Shell", "args": {"command": "echo hi"}}
```
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ReadFile"


def test_invalid_xml_is_skipped():
    text = "<tool_call><ReadFile><args><path>foo.py</path><<bad>></args></ReadFile></tool_call>"
    calls = parse_tool_requests(text)
    assert len(calls) == 0


def test_parse_dsml_tool_call_symmetric():
    text = """
<｜DSML｜tool_call>
{"name": "ListDirectory", "args": {"path": ".", "recursive": true}}
</｜DSML｜tool_call>
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ListDirectory"
    assert calls[0].args == {"path": ".", "recursive": True}


def test_parse_dsml_tool_call_asymmetric_plain_open():
    text = """
<tool_call>
{"name": "ListDirectory", "args": {"path": ".", "recursive": true}}
</｜DSML｜tool_call>
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ListDirectory"
    assert calls[0].args == {"path": ".", "recursive": True}


def test_parse_dsml_tool_call_asymmetric_dsml_open():
    text = """
<｜DSML｜tool_call>
{"name": "ReadFile", "args": {"path": "src/app.py"}}
</tool_call>
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ReadFile"
    assert calls[0].args == {"path": "src/app.py"}


def test_parse_dsml_tool_call_unclosed_trailing():
    text = """I will inspect the workspace now.
<｜DSML｜tool_call>
{"name": "ListDirectory", "args": {"path": "."}}"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ListDirectory"
    assert calls[0].args == {"path": "."}


def test_parse_dsml_multiple_tool_calls():
    text = """
<｜DSML｜tool_call>
{"name": "ReadFile", "args": {"path": "a.txt"}}
</｜DSML｜tool_call>
Some text in between
<｜DSML｜tool_call>
{"name": "WriteFile", "args": {"path": "b.txt", "content": "hello"}}
</｜DSML｜tool_call>
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 2
    assert calls[0].name == "ReadFile"
    assert calls[0].args == {"path": "a.txt"}
    assert calls[1].name == "WriteFile"
    assert calls[1].args == {"path": "b.txt", "content": "hello"}


def test_parse_dsml_ascii_pipe_delimiters():
    text = """
<|DSML|tool_call>
{"name": "Shell", "args": {"command": "pytest"}}
</|DSML|tool_call>
"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "Shell"
    assert calls[0].args == {"command": "pytest"}


def test_parse_format_a_verbatim():
    text = """<｜DSML｜tool_call>
{"name": "ListDirectory", "args": {"path": "."}}
</｜DSML｜tool_call>"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ListDirectory"
    assert calls[0].args == {"path": "."}


def test_parse_format_b_verbatim():
    text = """<｜DSML｜tool_call>
<｜DSML｜invoke>ReadFile</｜DSML｜invoke>
<｜DSML｜invoke>{"path": ".agentflow-test-todo/index.html"}</｜DSML｜invoke>
</｜DSML｜tool_call>"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ReadFile"
    assert calls[0].args == {"path": ".agentflow-test-todo/index.html"}


def test_parse_format_c_verbatim():
    text = """<｜DSML｜tool_calls>
<｜DSML｜invoke name="ListDirectory">
<｜DSML｜parameter>args</｜DSML｜parameter>
<｜DSML｜parameter>{"path": "."}</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜tool_calls>"""
    calls = parse_tool_requests(text)
    assert len(calls) == 1
    assert calls[0].name == "ListDirectory"
    assert calls[0].args == {"path": "."}


def test_parse_multiple_invokes_in_tool_calls_wrapper():
    text = """<｜DSML｜tool_calls>
<｜DSML｜invoke name="ReadFile">
<｜DSML｜parameter>{"path": "a.txt"}</｜DSML｜parameter>
</｜DSML｜invoke>
<｜DSML｜invoke name="WriteFile">
<｜DSML｜parameter>{"path": "b.txt", "content": "hello"}</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜tool_calls>"""
    calls = parse_tool_requests(text)
    assert len(calls) == 2
    assert calls[0].name == "ReadFile"
    assert calls[0].args == {"path": "a.txt"}
    assert calls[1].name == "WriteFile"
    assert calls[1].args == {"path": "b.txt", "content": "hello"}


def test_parse_broken_close_tag_variant():
    text = """<｜DSML｜tool_calls>
<｜DSML｜invoke name="ListDirectory">
<｜DSML｜parameter>{"path": "."}</｜DSML｜parameter>
</｜DSML｜parameter>
<｜DSML｜invoke name="ReadFile">
<｜DSML｜parameter>{"path": "a.txt"}</｜DSML｜parameter>
</｜DSML｜invoke>
</｜DSML｜tool_calls>"""
    calls = parse_tool_requests(text)
    assert len(calls) == 2
    assert calls[0].name == "ListDirectory"
    assert calls[0].args == {"path": "."}
    assert calls[1].name == "ReadFile"
    assert calls[1].args == {"path": "a.txt"}


