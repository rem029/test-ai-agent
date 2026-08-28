"""Tests for agentflow memory module (src/agentflow/memory.py)."""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from agentflow.memory import (
    MAX_MEMORY_CHARS,
    _home,
    _read,
    _write,
    compose_memory,
    global_memory_path,
    project_memory_path,
    read_global_memory,
    read_project_memory,
    write_global_memory,
    write_project_memory,
)


def test_path_helpers(tmp_path):
    assert global_memory_path() == _home() / "memory.md"
    cwd = str(tmp_path / "repo1")
    p1 = project_memory_path(cwd)
    assert p1.name == "memory.md"
    assert p1.parent.parent == _home() / "projects"


def test_project_memory_path_stability_and_uniqueness(tmp_path):
    repo_a = str(tmp_path / "repo_a")
    repo_b = str(tmp_path / "repo_b")

    path_a1 = project_memory_path(repo_a)
    path_a2 = project_memory_path(repo_a)
    path_b = project_memory_path(repo_b)

    assert path_a1 == path_a2
    assert path_a1 != path_b

    # Stability across relative vs absolute path
    os.makedirs(repo_a, exist_ok=True)
    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        rel_path = project_memory_path("repo_a")
        assert rel_path == path_a1
    finally:
        os.chdir(orig_cwd)


def test_read_missing_or_unreadable(tmp_path):
    non_existent = tmp_path / "missing.md"
    assert _read(non_existent) == ""

    # Directory instead of file
    dir_path = tmp_path / "some_dir"
    dir_path.mkdir()
    assert _read(dir_path) == ""


def test_write_and_read_roundtrip(tmp_path):
    cwd = str(tmp_path / "repo_roundtrip")

    # Global
    assert read_global_memory() == ""
    write_global_memory("Always write unit tests.")
    assert read_global_memory() == "Always write unit tests."

    # Project
    assert read_project_memory(cwd) == ""
    write_project_memory(cwd, "Use uv run pytest.")
    assert read_project_memory(cwd) == "Use uv run pytest."


def test_write_file_permissions_and_truncation(tmp_path):
    target = tmp_path / "perm_test" / "memory.md"
    long_text = "A" * (MAX_MEMORY_CHARS + 500)
    _write(target, long_text)

    assert target.exists()
    mode = stat.S_IMODE(target.stat().st_mode)
    assert oct(mode) == oct(0o600)

    content = target.read_text(encoding="utf-8")
    assert len(content) == MAX_MEMORY_CHARS
    assert content == "A" * MAX_MEMORY_CHARS


def test_compose_memory_empty(tmp_path):
    cwd = str(tmp_path / "repo_empty")
    assert compose_memory(cwd) == ""

    # Whitespace-only memory is treated as empty
    write_global_memory("   \n\t  ")
    write_project_memory(cwd, "   \n")
    assert compose_memory(cwd) == ""


def test_compose_memory_global_only(tmp_path):
    cwd = str(tmp_path / "repo_global_only")
    write_global_memory("Global instruction line 1\nGlobal instruction line 2  ")

    composed = compose_memory(cwd)
    expected = (
        "## Standing instructions & project memory\n\n"
        "### Global\n"
        "Global instruction line 1\nGlobal instruction line 2"
    )
    assert composed == expected


def test_compose_memory_project_only(tmp_path):
    cwd = str(tmp_path / "repo_project_only")
    write_project_memory(cwd, "Project convention text")

    composed = compose_memory(cwd)
    expected = (
        "## Standing instructions & project memory\n\n"
        "### This project\n"
        "Project convention text"
    )
    assert composed == expected


def test_compose_memory_both(tmp_path):
    cwd = str(tmp_path / "repo_both")
    write_global_memory("Global rule 1\nGlobal rule 2")
    write_project_memory(cwd, "Project rule A\nProject rule B")

    composed = compose_memory(cwd)
    expected = (
        "## Standing instructions & project memory\n\n"
        "### Global\n"
        "Global rule 1\nGlobal rule 2\n\n"
        "### This project\n"
        "Project rule A\nProject rule B"
    )
    assert composed == expected
