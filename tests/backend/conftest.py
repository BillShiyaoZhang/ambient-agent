import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.llm_config import LLMConfigStore
from backend.llm_service import set_default_llm_store


@pytest.fixture(autouse=True)
def isolated_main_llm_configuration(tmp_path, monkeypatch):
    """Give integration tests an explicit registry instead of legacy env defaults."""
    previous = main.llm_config_store
    store = LLMConfigStore(str(tmp_path / "llm-workspace"))
    store.create_provider(
        {
            "id": "test-provider",
            "name": "Test Provider",
            "preset": "openai",
            "models": [{"id": "test-model", "capabilities": {"tool_calling": True}}],
        },
        {},
    )
    store.update_settings(
        {
            "default_model": {"provider_id": "test-provider", "model_id": "test-model"},
            "fast_model": {"provider_id": "test-provider", "model_id": "test-model"},
        }
    )
    monkeypatch.setattr(main, "llm_config_store", store)

    set_default_llm_store(store)
    yield store
    set_default_llm_store(previous)


@pytest.fixture
def graph_api_client(tmp_path, monkeypatch, isolated_main_llm_configuration):
    """Run Graph APIs with one temporary control-plane composition root."""
    from backend.agent.durable_workflow import DurableAgentWorkflow
    from backend.app_manager import AppManager
    from backend.app_store import AppStoreService
    from backend.backend_manager import BackendManager
    from backend.capabilities.policy import CapabilityAuthorizer
    from backend.graph_db import GraphDatabase
    from backend.run_service import RunCoordinator, RunStore
    from backend.workspace_storage import WorkspaceStorage

    workspace_dir = str(tmp_path / "workspace")
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)
    monkeypatch.setenv("APPS_DIR", str(tmp_path / "workspace" / "apps"))
    monkeypatch.setenv("GRAPH_DATABASE_BACKEND", "sqlite")
    manager = AppManager()
    app_store = AppStoreService(workspace_dir, manager)
    backend_manager = BackendManager()
    graph = GraphDatabase(workspace_dir)
    store = RunStore(workspace_dir)
    workflow = DurableAgentWorkflow(
        workspace_dir=workspace_dir,
        run_store=store,
        app_manager=manager,
        graph_db=graph,
        llm_config_store=isolated_main_llm_configuration,
        coding_agent_runner=None,
    )
    coordinator = RunCoordinator(store, app_store, manager, backend_manager)
    coordinator.register_internal_agent_executor(workflow)
    for name, value in {
        "WORKSPACE_DIR": workspace_dir,
        "app_manager": manager,
        "app_store": app_store,
        "backend_manager": backend_manager,
        "graph_db": graph,
        "_closed_graph_db": None,
        "db_storage": WorkspaceStorage(workspace_dir),
        "run_store": store,
        "run_coordinator": coordinator,
        "durable_agent_workflow": workflow,
        "active_running_sessions": set(),
        "capability_authorizer": CapabilityAuthorizer(
            manifest_loader=manager.get_manifest,
            node_type_loader=main._graph_node_type,
        ),
    }.items():
        monkeypatch.setattr(main, name, value)
    with TestClient(main.app) as client:
        yield client
