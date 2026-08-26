"""Tests for agentflow CLI argument parsing and config file handling."""

from unittest.mock import patch
import io
import os

import pytest

from agentflow.cli import main
from agentflow.config import Config, RoleConfig


def test_missing_explicit_config_fails():
    """--config pointing to a nonexistent file must exit 1 and print error."""
    real_isfile = os.path.isfile

    def fake_isfile(path):
        if path == "missing.yaml":
            return False
        return real_isfile(path)

    stderr = io.StringIO()
    with patch("os.path.isfile", side_effect=fake_isfile):
        with patch("sys.stderr", stderr):
            ret = main(["--config", "missing.yaml", "some goal"])

    assert ret == 1, f"Expected exit code 1, got {ret}"
    assert "Error: config file not found: missing.yaml" in stderr.getvalue()


def test_no_config_falls_back():
    """Without --config, use DEFAULT_CONFIG_PATH and do not error if it's missing."""
    mock_config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    with patch("agentflow.cli.load_config", return_value=mock_config) as mock_load:
        with patch("agentflow.cli.run_workflow") as mock_run:
            mock_run.return_value = type(
                "State", (), {"pushed": {"pushed": True}}
            )()
            ret = main(["some goal"])

    # load_config should have been called with the default path
    from agentflow.cli import DEFAULT_CONFIG_PATH

    mock_load.assert_called_once_with(DEFAULT_CONFIG_PATH)
    # run_workflow should have been called, so exit is from there (0 in this case)
    assert ret == 0, f"Expected 0, got {ret}"


def test_check_with_missing_config_fails():
    """--check with explicit --config that doesn't exist should also exit 1."""
    real_isfile = os.path.isfile

    def fake_isfile(path):
        if path == "nonexistent.yaml":
            return False
        return real_isfile(path)

    stderr = io.StringIO()
    with patch("os.path.isfile", side_effect=fake_isfile):
        with patch("sys.stderr", stderr):
            ret = main(["--config", "nonexistent.yaml", "--check"])

    assert ret == 1
    assert "Error: config file not found: nonexistent.yaml" in stderr.getvalue()


def test_list_models_prints_models(capsys):
    ret = main(["--list-models", "claude-code"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "claude-3-7-sonnet" in captured.out
    assert "Claude Subscription" in captured.out


def test_cli_role_model_override():
    mock_config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    with patch("agentflow.cli.load_config", return_value=mock_config):
        with patch("agentflow.cli.run_workflow") as mock_run:
            mock_run.return_value = type("State", (), {"pushed": {"pushed": True}})()
            ret = main([
                "--build-backend", "openrouter",
                "--build-model", "deepseek/deepseek-chat",
                "test goal"
            ])

    assert ret == 0
    mock_run.assert_called_once()
    passed_config = mock_run.call_args[0][1]
    assert passed_config.build.backend == "openrouter"
    assert passed_config.build.model == "deepseek/deepseek-chat"

