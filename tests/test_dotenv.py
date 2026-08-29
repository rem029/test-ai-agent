"""Tests for agentflow.dotenv (load_dotenv, set_dotenv_var)."""

from __future__ import annotations

import os
from agentflow.dotenv import load_dotenv, set_dotenv_var


def test_load_dotenv_missing_file():
    result = load_dotenv("nonexistent_file_path.env")
    assert result == {}


def test_load_dotenv_parsing(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_KEY_PLAIN", raising=False)
    monkeypatch.delenv("TEST_KEY_QUOTED_DOUBLE", raising=False)
    monkeypatch.delenv("TEST_KEY_QUOTED_SINGLE", raising=False)
    monkeypatch.delenv("TEST_KEY_EXPORT", raising=False)
    monkeypatch.delenv("TEST_KEY_SPACES", raising=False)
    monkeypatch.delenv("TEST_KEY_EMPTY", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        """# A comment line
TEST_KEY_PLAIN=simple_val

# Another comment
export TEST_KEY_EXPORT=exported_val
TEST_KEY_QUOTED_DOUBLE="double quoted value"
TEST_KEY_QUOTED_SINGLE='single quoted value'
   TEST_KEY_SPACES   =   trimmed value
TEST_KEY_EMPTY=
INVALID_LINE_NO_EQUALS
=NO_KEY
"""
    )

    parsed = load_dotenv(env_file)

    assert parsed == {
        "TEST_KEY_PLAIN": "simple_val",
        "TEST_KEY_EXPORT": "exported_val",
        "TEST_KEY_QUOTED_DOUBLE": "double quoted value",
        "TEST_KEY_QUOTED_SINGLE": "single quoted value",
        "TEST_KEY_SPACES": "trimmed value",
        "TEST_KEY_EMPTY": "",
    }

    assert os.environ["TEST_KEY_PLAIN"] == "simple_val"
    assert os.environ["TEST_KEY_EXPORT"] == "exported_val"
    assert os.environ["TEST_KEY_QUOTED_DOUBLE"] == "double quoted value"
    assert os.environ["TEST_KEY_QUOTED_SINGLE"] == "single quoted value"
    assert os.environ["TEST_KEY_SPACES"] == "trimmed value"
    assert os.environ["TEST_KEY_EMPTY"] == ""


def test_load_dotenv_respects_existing_env_unless_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PRESET", "from_shell")
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_PRESET=from_dotenv\nTEST_OTHER=other_val\n")

    parsed = load_dotenv(env_file, override=False)
    assert parsed["TEST_PRESET"] == "from_dotenv"
    assert os.environ["TEST_PRESET"] == "from_shell"
    assert os.environ["TEST_OTHER"] == "other_val"

    load_dotenv(env_file, override=True)
    assert os.environ["TEST_PRESET"] == "from_dotenv"


def test_set_dotenv_var_creates_file_with_0600(tmp_path, monkeypatch):
    monkeypatch.delenv("NEW_SECRET", raising=False)
    env_file = tmp_path / ".env"

    assert not env_file.exists()
    set_dotenv_var("NEW_SECRET", "secret_val_123", path=env_file)

    assert env_file.exists()
    assert oct(env_file.stat().st_mode & 0o777) == oct(0o600)
    assert env_file.read_text() == "NEW_SECRET=secret_val_123\n"
    assert os.environ["NEW_SECRET"] == "secret_val_123"


def test_set_dotenv_var_upserts_and_preserves_comments_and_other_vars(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        """# Header comment
EXISTING_VAR=foo
# Key comment
KEY_TO_REPLACE=old_val
export OTHER_EXPORT=bar
"""
    )

    set_dotenv_var("KEY_TO_REPLACE", "new_val", path=env_file)
    content = env_file.read_text()

    assert content == (
        """# Header comment
EXISTING_VAR=foo
# Key comment
KEY_TO_REPLACE=new_val
export OTHER_EXPORT=bar
"""
    )
    assert os.environ["KEY_TO_REPLACE"] == "new_val"

    # Now replace export OTHER_EXPORT
    set_dotenv_var("OTHER_EXPORT", "updated_export", path=env_file)
    assert "OTHER_EXPORT=updated_export\n" in env_file.read_text()
    assert os.environ["OTHER_EXPORT"] == "updated_export"

    # Now append a brand new key
    set_dotenv_var("BRAND_NEW", "brand_new_val", path=env_file)
    assert "BRAND_NEW=brand_new_val\n" in env_file.read_text()
    assert os.environ["BRAND_NEW"] == "brand_new_val"
