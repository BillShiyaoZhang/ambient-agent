import os
from tempfile import TemporaryDirectory

import pytest

# App import initializes durable stores; isolate them before that side effect.
_test_workspace = TemporaryDirectory(prefix="ambient-pytest-workspace-")
os.environ["WORKSPACE_DIR"] = _test_workspace.name

from backend.main import app_manager


@pytest.fixture(autouse=True)
def isolate_jev_router_configuration(monkeypatch):
    """Backend tests opt in to Jev explicitly, independent of local .env."""
    for name in (
        "TYPESAFE_API_KEY",
        "JEV_ROUTER_MODEL",
        "JEV_ROUTER_TIMEOUT_SECONDS",
        "JEV_ROUTER_MIN_PROBABILITY",
        "JEV_ROUTER_MIN_MARGIN",
        "JEV_ROUTER_MAX_STATE_CHARS",
        "JEV_ROUTER_CONTEXT_VERSION",
        "JEV_DECISION_MODEL",
        "JEV_DECISION_STAGE_MODES",
        "JEV_DECISION_TIMEOUT_SECONDS",
        "JEV_DECISION_MIN_PROBABILITY",
        "JEV_DECISION_MIN_MARGIN",
        "JEV_DECISION_MAX_STATE_CHARS",
        "JEV_DECISION_MAX_CANDIDATES",
        "JEV_DECISION_MAX_QUESTIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JEV_ROUTER_MODE", "off")
    monkeypatch.setenv("JEV_DECISION_MODE", "off")


@pytest.fixture(autouse=True)
def isolate_apps_dir(tmp_path, monkeypatch):
    """
    Globally isolates apps directory for all backend tests to prevent
    pollution of production app directories (e.g. weather-card).
    Uses a unique subfolder name 'global_apps' to avoid collisions
    with individual test fixtures using 'apps'.
    """
    temp_dir = tmp_path / "global_apps"
    temp_dir.mkdir(exist_ok=True)

    # Patch environment and the global app_manager's apps_dir
    monkeypatch.setenv("APPS_DIR", str(temp_dir))
    monkeypatch.setattr(app_manager, "apps_dir", str(temp_dir))

    return temp_dir
