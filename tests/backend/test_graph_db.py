import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.main as main_module
from backend.graph_db import GraphDatabase


def test_graph_db_crud(tmp_path):
    # Set up temp workspace
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)

    db = GraphDatabase(workspace_dir)

    # 1. Test Node Creation
    node1 = db.create_node(
        node_id="task-1", node_type="Task", properties={"title": "Buy groceries", "status": "pending"}
    )
    assert node1["id"] == "task-1"
    assert node1["type"] == "Task"
    assert node1["properties"]["title"] == "Buy groceries"

    node2 = db.create_node(
        node_id="event-1", node_type="Event", properties={"title": "Shopping trip", "start_time": "2026-07-12"}
    )
    assert node2["id"] == "event-1"

    # Verify node retrieval
    retrieved = db.get_node("task-1")
    assert retrieved is not None
    assert retrieved["properties"]["status"] == "pending"

    # 2. Test Property Update
    updated = db.update_node_property("task-1", {"status": "completed", "priority": "high"})
    assert updated["properties"]["status"] == "completed"
    assert updated["properties"]["priority"] == "high"
    assert updated["properties"]["title"] == "Buy groceries"  # should preserve other properties

    # 3. Test Edge Creation
    edge = db.create_edge(from_id="task-1", to_id="event-1", edge_type="ASSOCIATED_WITH", properties={"weight": 1})
    assert edge["from_id"] == "task-1"
    assert edge["to_id"] == "event-1"
    assert edge["type"] == "ASSOCIATED_WITH"
    assert edge["properties"]["weight"] == 1

    # 4. Test Persistence
    db.save()

    graph_file = os.path.join(workspace_dir, "graph.json")
    assert os.path.exists(graph_file)

    # Load into a new DB instance to verify loading
    db2 = GraphDatabase(workspace_dir)
    assert db2.get_node("task-1") is not None
    assert db2.get_node("task-1")["properties"]["status"] == "completed"

    edges = db2.get_edges("task-1")
    assert len(edges) == 1
    assert edges[0]["to_id"] == "event-1"

    # 5. Test Cascade Deletion
    # Deleting task-1 should delete the edge connecting it to event-1
    db2.delete_node("task-1")
    assert db2.get_node("task-1") is None
    assert len(db2.get_edges("task-1")) == 0
    assert len(db2.get_edges("event-1")) == 0

    # 6. Test direct edge deletion
    # Create another connection
    node3 = db2.create_node(node_id="user-1", node_type="Person", properties={"name": "Alice"})
    edge2 = db2.create_edge(from_id="user-1", to_id="event-1", edge_type="ATTENDING")
    assert len(db2.get_edges("user-1")) == 1

    db2.delete_edge(from_id="user-1", to_id="event-1", edge_type="ATTENDING")
    assert len(db2.get_edges("user-1")) == 0


def test_sqlite_graph_adapter_exposes_idempotent_close(tmp_path):
    db = GraphDatabase(str(tmp_path))

    assert db.close() is None
    assert db.close() is None


@pytest.mark.asyncio
async def test_application_lifespan_closes_composition_root_graph_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_db = MagicMock()
    monkeypatch.setattr(main_module, "_closed_graph_db", None)
    monkeypatch.setattr(main_module, "graph_db", graph_db)
    monkeypatch.setattr(main_module, "migrate_old_data", lambda _workspace: None)
    monkeypatch.setattr(main_module.db_storage, "cleanup_audit_logs", lambda: 0)
    monkeypatch.setattr(main_module, "recover_interrupted_coding_agent_promotions", lambda _apps_dir: None)
    monkeypatch.setattr(main_module.run_store, "retained_staging_paths", lambda **_kwargs: set())
    monkeypatch.setattr(main_module, "cleanup_orphaned_coding_agent_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.run_store, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(main_module.run_coordinator, "start", AsyncMock())
    monkeypatch.setattr(main_module.run_coordinator, "shutdown", AsyncMock())
    monkeypatch.setattr(main_module.coding_agent_config_store.runtime, "shutdown", AsyncMock())
    monkeypatch.setattr(main_module.backend_manager, "shutdown", AsyncMock())

    async with main_module.lifespan(main_module.app):
        pass

    graph_db.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_application_lifespan_recreates_graph_adapter_after_prior_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_graph_db = MagicMock()
    second_graph_db = MagicMock()
    graph_factory = MagicMock(return_value=second_graph_db)
    monkeypatch.setattr(main_module, "graph_db", first_graph_db)
    monkeypatch.setattr(main_module, "_closed_graph_db", None)
    monkeypatch.setattr(main_module, "create_graph_database", graph_factory)
    monkeypatch.setattr(main_module.durable_agent_workflow, "graph_db", first_graph_db)
    monkeypatch.setattr(main_module, "migrate_old_data", lambda _workspace: None)
    monkeypatch.setattr(main_module.db_storage, "cleanup_audit_logs", lambda: 0)
    monkeypatch.setattr(main_module, "recover_interrupted_coding_agent_promotions", lambda _apps_dir: None)
    monkeypatch.setattr(main_module.run_store, "retained_staging_paths", lambda **_kwargs: set())
    monkeypatch.setattr(main_module, "cleanup_orphaned_coding_agent_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.run_store, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(main_module.run_coordinator, "start", AsyncMock())
    monkeypatch.setattr(main_module.run_coordinator, "shutdown", AsyncMock())
    monkeypatch.setattr(main_module.coding_agent_config_store.runtime, "shutdown", AsyncMock())
    monkeypatch.setattr(main_module.backend_manager, "shutdown", AsyncMock())

    async with main_module.lifespan(main_module.app):
        assert main_module.graph_db is first_graph_db

    async with main_module.lifespan(main_module.app):
        assert main_module.graph_db is second_graph_db
        assert main_module.durable_agent_workflow.graph_db is second_graph_db

    graph_factory.assert_called_once_with(main_module.WORKSPACE_DIR)
    first_graph_db.close.assert_called_once_with()
    second_graph_db.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_application_lifespan_releases_resources_when_app_context_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_db = MagicMock()
    coordinator_shutdown = AsyncMock()
    coding_runtime_shutdown = AsyncMock()
    backend_shutdown = AsyncMock()
    monkeypatch.setattr(main_module, "_closed_graph_db", None)
    monkeypatch.setattr(main_module, "graph_db", graph_db)
    monkeypatch.setattr(main_module, "migrate_old_data", lambda _workspace: None)
    monkeypatch.setattr(main_module.db_storage, "cleanup_audit_logs", lambda: 0)
    monkeypatch.setattr(main_module, "recover_interrupted_coding_agent_promotions", lambda _apps_dir: None)
    monkeypatch.setattr(main_module.run_store, "retained_staging_paths", lambda **_kwargs: set())
    monkeypatch.setattr(main_module, "cleanup_orphaned_coding_agent_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.run_store, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(main_module.run_coordinator, "start", AsyncMock())
    monkeypatch.setattr(main_module.run_coordinator, "shutdown", coordinator_shutdown)
    monkeypatch.setattr(main_module.coding_agent_config_store.runtime, "shutdown", coding_runtime_shutdown)
    monkeypatch.setattr(main_module.backend_manager, "shutdown", backend_shutdown)

    with pytest.raises(RuntimeError, match="app context failed"):
        async with main_module.lifespan(main_module.app):
            raise RuntimeError("app context failed")

    coordinator_shutdown.assert_awaited_once_with()
    coding_runtime_shutdown.assert_awaited_once_with()
    backend_shutdown.assert_awaited_once_with()
    graph_db.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_application_lifespan_attempts_all_cleanup_when_first_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_db = MagicMock()
    coordinator_shutdown = AsyncMock(side_effect=RuntimeError("coordinator shutdown failed"))
    coding_runtime_shutdown = AsyncMock()
    backend_shutdown = AsyncMock()
    monkeypatch.setattr(main_module, "_closed_graph_db", None)
    monkeypatch.setattr(main_module, "graph_db", graph_db)
    monkeypatch.setattr(main_module, "migrate_old_data", lambda _workspace: None)
    monkeypatch.setattr(main_module.db_storage, "cleanup_audit_logs", lambda: 0)
    monkeypatch.setattr(main_module, "recover_interrupted_coding_agent_promotions", lambda _apps_dir: None)
    monkeypatch.setattr(main_module.run_store, "retained_staging_paths", lambda **_kwargs: set())
    monkeypatch.setattr(main_module, "cleanup_orphaned_coding_agent_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.run_store, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(main_module.run_coordinator, "start", AsyncMock())
    monkeypatch.setattr(main_module.run_coordinator, "shutdown", coordinator_shutdown)
    monkeypatch.setattr(main_module.coding_agent_config_store.runtime, "shutdown", coding_runtime_shutdown)
    monkeypatch.setattr(main_module.backend_manager, "shutdown", backend_shutdown)

    with pytest.raises(RuntimeError, match="coordinator shutdown failed"):
        async with main_module.lifespan(main_module.app):
            pass

    coordinator_shutdown.assert_awaited_once_with()
    coding_runtime_shutdown.assert_awaited_once_with()
    backend_shutdown.assert_awaited_once_with()
    graph_db.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_application_lifespan_cleans_up_when_coordinator_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_db = MagicMock()
    coordinator_shutdown = AsyncMock()
    coding_runtime_shutdown = AsyncMock()
    backend_shutdown = AsyncMock()
    monkeypatch.setattr(main_module, "_closed_graph_db", None)
    monkeypatch.setattr(main_module, "graph_db", graph_db)
    monkeypatch.setattr(main_module, "migrate_old_data", lambda _workspace: None)
    monkeypatch.setattr(main_module.db_storage, "cleanup_audit_logs", lambda: 0)
    monkeypatch.setattr(main_module, "recover_interrupted_coding_agent_promotions", lambda _apps_dir: None)
    monkeypatch.setattr(main_module.run_store, "retained_staging_paths", lambda **_kwargs: set())
    monkeypatch.setattr(main_module, "cleanup_orphaned_coding_agent_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.run_store, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(
        main_module.run_coordinator,
        "start",
        AsyncMock(side_effect=RuntimeError("coordinator start failed")),
    )
    monkeypatch.setattr(main_module.run_coordinator, "shutdown", coordinator_shutdown)
    monkeypatch.setattr(main_module.coding_agent_config_store.runtime, "shutdown", coding_runtime_shutdown)
    monkeypatch.setattr(main_module.backend_manager, "shutdown", backend_shutdown)

    with pytest.raises(RuntimeError, match="coordinator start failed"):
        async with main_module.lifespan(main_module.app):
            pass

    coordinator_shutdown.assert_awaited_once_with()
    coding_runtime_shutdown.assert_awaited_once_with()
    backend_shutdown.assert_awaited_once_with()
    graph_db.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_application_lifespan_preserves_cleanup_cancellation_over_regular_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_db = MagicMock()
    coordinator_shutdown = AsyncMock(side_effect=RuntimeError("coordinator shutdown failed"))
    coding_runtime_shutdown = AsyncMock(side_effect=asyncio.CancelledError())
    backend_shutdown = AsyncMock()
    monkeypatch.setattr(main_module, "_closed_graph_db", None)
    monkeypatch.setattr(main_module, "graph_db", graph_db)
    monkeypatch.setattr(main_module, "migrate_old_data", lambda _workspace: None)
    monkeypatch.setattr(main_module.db_storage, "cleanup_audit_logs", lambda: 0)
    monkeypatch.setattr(main_module, "recover_interrupted_coding_agent_promotions", lambda _apps_dir: None)
    monkeypatch.setattr(main_module.run_store, "retained_staging_paths", lambda **_kwargs: set())
    monkeypatch.setattr(main_module, "cleanup_orphaned_coding_agent_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.run_store, "list_runs", lambda **_kwargs: [])
    monkeypatch.setattr(main_module.run_coordinator, "start", AsyncMock())
    monkeypatch.setattr(main_module.run_coordinator, "shutdown", coordinator_shutdown)
    monkeypatch.setattr(main_module.coding_agent_config_store.runtime, "shutdown", coding_runtime_shutdown)
    monkeypatch.setattr(main_module.backend_manager, "shutdown", backend_shutdown)

    with pytest.raises(asyncio.CancelledError):
        async with main_module.lifespan(main_module.app):
            pass

    coordinator_shutdown.assert_awaited_once_with()
    coding_runtime_shutdown.assert_awaited_once_with()
    backend_shutdown.assert_awaited_once_with()
    graph_db.close.assert_called_once_with()
