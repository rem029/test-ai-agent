"""Unit tests for strip_tool_blocks in agentflow.tools.parser."""

from __future__ import annotations

from agentflow.tools import strip_tool_blocks
from agentflow.tools.parser import strip_tool_blocks as strip_direct


def test_strip_plain_prose():
    assert strip_tool_blocks("") == ""
    assert strip_tool_blocks("Hello world") == "Hello world"
    prose = "I will implement the function in `src/foo.py` and run tests."
    assert strip_tool_blocks(prose) == prose


def test_strip_closed_tool_calls():
    raw = (
        "Here is my plan:\n\n"
        "<tool_call>\n"
        '{"name": "ReadFile", "args": {"path": "src/app.py"}}\n'
        "</tool_call>\n\n"
        "Let me know if this works."
    )
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "Here is my plan:\n\nLet me know if this works."


def test_strip_multiple_tool_calls():
    raw = (
        "Starting execution.\n"
        "<tool_call>\n"
        '{"name": "ReadFile", "args": {"path": "a.py"}}\n'
        "</tool_call>\n"
        "Middle text.\n"
        "<tool_call>\n"
        '{"name": "ReadFile", "args": {"path": "b.py"}}\n'
        "</tool_call>\n"
        "Done."
    )
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "Starting execution.\n\nMiddle text.\n\nDone."


def test_strip_dsml_tool_calls():
    raw = (
        "Analyzing repo:\n"
        "<｜tool_calls｜><｜invoke:ReadFile｜>```json\n"
        '{"path": "foo.py"}\n'
        "```<｜/invoke｜><｜/tool_calls｜>\n"
        "Finished."
    )
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "Analyzing repo:\n\nFinished."

    raw2 = (
        "Checking:\n"
        "<｜DSML｜tool_call>\n"
        '{"name": "ListDirectory", "args": {}}\n'
        "</｜DSML｜>\n"
        "All good."
    )
    cleaned2 = strip_tool_blocks(raw2)
    assert cleaned2 == "Checking:\n\nAll good."


def test_strip_unclosed_trailing_tool_call():
    raw = (
        "I am reading the file now.\n\n"
        "<tool_call>\n"
        '{"name": "ReadFile", "args": {"path": "main.py"'
    )
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "I am reading the file now."


def test_strip_standalone_invoke():
    raw = (
        "Invoking tool:\n"
        '<invoke name="ReadFile">\n'
        '{"path": "test.py"}\n'
        "</invoke>\n"
        "End."
    )
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "Invoking tool:\n\nEnd."



def test_strip_bare_json_tool_lines():
    raw = (
        "Let me call the tool:\n"
        '{"name": "WriteFile", "args": {"path": "x.py", "content": "123"}}\n'
        "Continuing with work."
    )
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "Let me call the tool:\nContinuing with work."

    # Non-tool JSON is not stripped
    raw_json = (
        "The response payload is:\n"
        '{"status": 200, "message": "ok"}\n'
        "Saved."
    )
    cleaned_json = strip_tool_blocks(raw_json)
    assert '{"status": 200, "message": "ok"}' in cleaned_json


def test_strip_leftover_markup_tags():
    raw = (
        "Results:\n"
        "<parameter name='path'>src/lib.py</parameter>\n"
        "</tool_call>\n"
        "<DSML>Some leftover</DSML>\n"
        "Summary."
    )
    cleaned = strip_tool_blocks(raw)
    assert "<parameter" not in cleaned
    assert "</tool_call>" not in cleaned
    assert "<DSML>" not in cleaned
    assert "src/lib.py" in cleaned
    assert "Summary." in cleaned


def test_strip_file_blocks():
    raw = (
        "I have created the module:\n\n"
        "```FILE: src/agentflow/example.py\n"
        "def example():\n"
        "    return 42\n"
        "```\n\n"
        "Please review the changes."
    )
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "I have created the module:\n\nPlease review the changes."
    assert "def example" not in cleaned

    # Unclosed trailing FILE block
    raw_trailing = (
        "Writing code:\n"
        "```FILE: src/foo.py\n"
        "x = 10\n"
    )
    cleaned_trailing = strip_tool_blocks(raw_trailing)
    assert cleaned_trailing == "Writing code:"


def test_collapse_multiple_blank_lines():
    raw = "Line 1\n\n\n\n\nLine 2\n\n\n\nLine 3"
    cleaned = strip_tool_blocks(raw)
    assert cleaned == "Line 1\n\nLine 2\n\nLine 3"
