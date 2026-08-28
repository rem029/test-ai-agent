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


def test_openrouter_key_flag_sets_invocation_environment(monkeypatch):
    mock_config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("agentflow.cli.load_config", return_value=mock_config):
        with patch("agentflow.cli.run_workflow") as mock_run:
            mock_run.return_value = type("State", (), {"pushed": {"pushed": True}})()
            ret = main(["--openrouter-key", "test-key", "test goal"])

    assert ret == 0
    assert os.environ["OPENROUTER_API_KEY"] == "test-key"


def test_set_openrouter_key_persists_to_selected_config(tmp_path):
    config_path = tmp_path / "agentflow.config.yaml"

    ret = main(["--config", str(config_path), "--set-openrouter-key", "saved-key"])

    assert ret == 0
    assert "openrouter_api_key: saved-key" in config_path.read_text()


def test_cli_say_flag(capsys):
    from agentflow.database import get_pending_messages

    # Missing goal/body
    ret_fail = main(["--say", "run-test-say"])
    assert ret_fail == 1
    err = capsys.readouterr().err
    assert "message body is required" in err.lower()

    # Success with unknown run warning
    ret_ok = main(["--say", "run-test-say", "Add unit tests"])
    assert ret_ok == 0
    captured = capsys.readouterr()
    assert "Message sent to run run-test-say" in captured.out
    assert "Warning: no run 'run-test-say' found" in captured.err

    msgs = get_pending_messages("run-test-say")
    assert len(msgs) == 1
    assert msgs[0]["body"] == "Add unit tests"
    assert msgs[0]["kind"] == "steer"


def test_cli_note_flag(capsys):
    from agentflow.database import get_pending_messages

    # Missing goal/body
    ret_fail = main(["--note", "run-test-note"])
    assert ret_fail == 1
    err = capsys.readouterr().err
    assert "note body is required" in err.lower()

    # Success with unknown run warning
    ret_ok = main(["--note", "run-test-note", "Check formatting"])
    assert ret_ok == 0
    captured = capsys.readouterr()
    assert "Note sent to run run-test-note" in captured.out
    assert "Warning: no run 'run-test-note' found" in captured.err

    msgs = get_pending_messages("run-test-note")
    assert len(msgs) == 1
    assert msgs[0]["body"] == "Check formatting"
    assert msgs[0]["kind"] == "note"


def test_cli_stop_flag(capsys):
    from agentflow.database import has_stop_signal

    ret = main(["--stop", "run-test-stop"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Stop signal sent to run run-test-stop" in captured.out
    assert "Warning: no run 'run-test-stop' found" in captured.err

    assert has_stop_signal("run-test-stop") is True


def test_cli_handles_run_in_progress_error(capsys):
    from agentflow.orchestrator import RunInProgressError

    mock_config = Config(
        review=RoleConfig(backend="claude-code"),
        build=RoleConfig(backend="claude-code"),
        verify=RoleConfig(backend="claude-code"),
    )
    with patch("agentflow.cli.load_config", return_value=mock_config):
        with patch("agentflow.cli.run_workflow", side_effect=RunInProgressError("/path/to/repo")):
            ret = main(["another task"])

    assert ret == 1
    captured = capsys.readouterr()
    assert "Error: a run is already in progress for /path/to/repo" in captured.err


def test_cli_show_memory(capsys, tmp_path):
    from agentflow.memory import write_global_memory, write_project_memory

    # 1. No memory configured
    ret1 = main(["--show-memory"])
    assert ret1 == 0
    out1 = capsys.readouterr().out
    assert "(no memory configured)" in out1

    # 2. With memory configured
    write_global_memory("Always run pytest before commit.")
    write_project_memory(os.getcwd(), "Use uv environment.")
    ret2 = main(["--show-memory"])
    assert ret2 == 0
    out2 = capsys.readouterr().out
    assert "## Standing instructions & project memory" in out2
    assert "### Global\nAlways run pytest before commit." in out2
    assert "### This project\nUse uv environment." in out2


def test_cli_edit_memory_invokes_editor(capsys, monkeypatch):
    from agentflow.memory import global_memory_path, project_memory_path

    monkeypatch.setenv("EDITOR", "my-editor")

    with patch("subprocess.call", return_value=0) as mock_subproc:
        ret_glob = main(["--edit-memory", "global"])
        assert ret_glob == 0
        g_path = global_memory_path()
        assert g_path.exists()
        mock_subproc.assert_called_once_with(["my-editor", str(g_path)])

    with patch("subprocess.call", return_value=0) as mock_subproc:
        ret_proj = main(["--edit-memory", "project"])
        assert ret_proj == 0
        p_path = project_memory_path(os.getcwd())
        assert p_path.exists()
        mock_subproc.assert_called_once_with(["my-editor", str(p_path)])


def test_cli_edit_memory_missing_editor_fails(capsys, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    with patch("shutil.which", return_value=None):
        ret = main(["--edit-memory", "global"])
        assert ret == 1
        captured = capsys.readouterr()
        assert "Error: no editor found" in captured.err
        assert "memory.md" in captured.err



