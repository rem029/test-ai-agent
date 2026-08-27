"""Tests for code analysis tools."""

from __future__ import annotations
from pathlib import Path

import pytest

from agentflow.tools import ToolContext
from agentflow.tools.code_analysis import ImportAnalysisTool, LintTool, TypeCheckTool


@pytest.fixture
def ctx(tmp_path):
    path = tmp_path / "work"
    path.mkdir()
    return ToolContext(cwd=str(path))


def test_lint_valid_python(ctx):
    file = Path(ctx.cwd) / "valid.py"
    file.write_text("x = 1\n")
    result = LintTool().run({"path": "valid.py"}, context=ctx)
    assert result.success is True


def test_lint_invalid_syntax(ctx):
    file = Path(ctx.cwd) / "broken.py"
    file.write_text("def foo(\n")
    result = LintTool().run({"path": "broken.py"}, context=ctx)
    assert result.success is False


def test_import_analysis(ctx):
    file = Path(ctx.cwd) / "sample.py"
    file.write_text("import os\nfrom pathlib import Path\n")
    result = ImportAnalysisTool().run({"path": "sample.py"}, context=ctx)
    assert result.success is True
    assert "import os" in result.output
    assert "from pathlib import Path" in result.output


def test_type_check_runs_without_crash(ctx):
    file = Path(ctx.cwd) / "sample.py"
    file.write_text("x: int = 1\n")
    result = TypeCheckTool().run({"path": "sample.py"}, context=ctx)
    # TypeCheck may succeed or fail depending on available tools; just ensure
    # it runs and returns a boolean result.
    assert isinstance(result.success, bool)
    assert result.output or result.error
