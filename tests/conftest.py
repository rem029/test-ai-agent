"""Global pytest fixtures and configuration for agentflow tests."""

from __future__ import annotations

from pathlib import Path
import pytest

import agentflow.config
import agentflow.database
import agentflow.web.app


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    """Ensure every test runs against an isolated temporary database and AGENTFLOW_HOME.

    Prevents tests from writing to ~/.agentflow/.
    """
    test_db = tmp_path / "test_isolated_agentflow.db"
    monkeypatch.setattr(agentflow.database, "DEFAULT_DATABASE_PATH", test_db)
    if hasattr(agentflow.web.app, "DEFAULT_DATABASE_PATH"):
        monkeypatch.setattr(agentflow.web.app, "DEFAULT_DATABASE_PATH", test_db)
    if hasattr(agentflow, "orchestrator") and hasattr(agentflow.orchestrator, "DEFAULT_DATABASE_PATH"):
        monkeypatch.setattr(agentflow.orchestrator, "DEFAULT_DATABASE_PATH", test_db)
    test_home = tmp_path / "agentflow_home"
    monkeypatch.setattr(agentflow.config, "AGENTFLOW_HOME", test_home)
    if hasattr(agentflow, "orchestrator") and hasattr(agentflow.orchestrator, "AGENTFLOW_HOME"):
        monkeypatch.setattr(agentflow.orchestrator, "AGENTFLOW_HOME", test_home)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AGENTFLOW_SMTP_PASSWORD", raising=False)
    yield test_db
