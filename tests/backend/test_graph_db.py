import asyncio
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.main as main_module
from backend.graph_db import GraphDatabase, GraphSchemaReadError


def _replace_schema_ids(db: GraphDatabase, schema_ids: list[str]) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM graph_schemas")
        conn.executemany(
            """
            INSERT INTO graph_schemas (id, name, description, properties, is_core, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    schema_id,
                    "Test schema",
                    None,
                    "{}",
                    0,
                    "2026-07-20T00:00:00+00:00",
                )
                for schema_id in schema_ids
            ],
        )


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


def test_list_schema_ids_is_ordered_bounded_and_does_not_parse_definitions(tmp_path):
    db = GraphDatabase(str(tmp_path / "workspace"))
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO graph_schemas (id, name, description, properties, is_core, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "Aardvark",
                "Aardvark",
                None,
                "not-valid-json",
                0,
                "2026-07-20T00:00:00+00:00",
            ),
        )

    assert db.list_schema_ids(limit=2, max_id_codepoints=128) == [
        "Aardvark",
        "Document",
    ]


def test_list_schema_ids_bounds_legacy_ids_before_python_materialization(tmp_path):
    db = GraphDatabase(str(tmp_path / "workspace"))
    oversized_legacy_id = "legacy-" + ("x" * 2_000_000)
    _replace_schema_ids(db, [oversized_legacy_id])

    result = db.list_schema_ids(limit=1, max_id_codepoints=129)

    assert result == [oversized_legacy_id[:129]]
    assert len(result[0]) == 129


def test_list_schema_ids_uses_query_only_read_only_connection(tmp_path, monkeypatch):
    db = GraphDatabase(str(tmp_path / "workspace"))
    _replace_schema_ids(db, ["Zulu", "Alpha-two", "Alpha-one", "Middle"])
    original_connect = sqlite3.connect
    connection_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    executed_statements: list[str] = []

    def recording_connect(*args, **kwargs):
        connection_calls.append((args, kwargs))
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(executed_statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", recording_connect)

    assert db.list_schema_ids(limit=3, max_id_codepoints=7) == [
        "Alpha-o",
        "Alpha-t",
        "Middle",
    ]
    assert len(connection_calls) == 1
    args, kwargs = connection_calls[0]
    assert str(args[0]).startswith("file:")
    assert "mode=ro" in str(args[0])
    assert kwargs["uri"] is True
    assert any("PRAGMA query_only=ON" in statement for statement in executed_statements)
    assert not any("journal_mode" in statement for statement in executed_statements)


@pytest.mark.parametrize("limit", [True, 1.5, "1", None])
def test_list_schema_ids_rejects_non_integer_limits(tmp_path, limit):
    db = GraphDatabase(str(tmp_path / "workspace"))

    with pytest.raises(TypeError, match="positive integer"):
        db.list_schema_ids(limit=limit, max_id_codepoints=128)


@pytest.mark.parametrize("limit", [0, -1])
def test_list_schema_ids_rejects_non_positive_limits(tmp_path, limit):
    db = GraphDatabase(str(tmp_path / "workspace"))

    with pytest.raises(ValueError, match="positive integer"):
        db.list_schema_ids(limit=limit, max_id_codepoints=128)


@pytest.mark.parametrize("max_id_codepoints", [True, 1.5, "128", None])
def test_list_schema_ids_rejects_non_integer_max_id_codepoints(
    tmp_path,
    max_id_codepoints,
):
    db = GraphDatabase(str(tmp_path / "workspace"))

    with pytest.raises(TypeError, match="max_id_codepoints must be a positive integer"):
        db.list_schema_ids(
            limit=1,
            max_id_codepoints=max_id_codepoints,
        )


@pytest.mark.parametrize("max_id_codepoints", [0, -1])
def test_list_schema_ids_rejects_non_positive_max_id_codepoints(
    tmp_path,
    max_id_codepoints,
):
    db = GraphDatabase(str(tmp_path / "workspace"))

    with pytest.raises(ValueError, match="max_id_codepoints must be a positive integer"):
        db.list_schema_ids(
            limit=1,
            max_id_codepoints=max_id_codepoints,
        )


def test_list_schema_ids_translates_sqlite_read_failures(tmp_path, monkeypatch):
    db = GraphDatabase(str(tmp_path / "workspace"))

    def failing_connection(*_args, **_kwargs):
        raise sqlite3.OperationalError("sensitive adapter detail")

    monkeypatch.setattr(sqlite3, "connect", failing_connection)

    with pytest.raises(GraphSchemaReadError) as exc_info:
        db.list_schema_ids(limit=1, max_id_codepoints=128)

    assert str(exc_info.value) == ""


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
    first_graph_db.list_schema_ids.return_value = ["Legacy"]
    second_graph_db.list_schema_ids.return_value = ["Task"]
    graph_factory = MagicMock(return_value=second_graph_db)
    audit_stream = object()
    app_snapshot = object()
    privacy_response = object()
    app_reader = MagicMock()
    app_reader.read.return_value = app_snapshot
    privacy_service = MagicMock()
    privacy_service.build.return_value = privacy_response
    monkeypatch.setattr(main_module, "graph_db", first_graph_db)
    monkeypatch.setattr(main_module, "_closed_graph_db", None)
    monkeypatch.setattr(main_module, "create_graph_database", graph_factory)
    monkeypatch.setattr(main_module.durable_agent_workflow, "graph_db", first_graph_db)
    monkeypatch.setattr(
        main_module,
        "WorkspaceAuditProjectionStream",
        MagicMock(return_value=audit_stream),
    )
    monkeypatch.setattr(
        main_module,
        "AppDeclarationSnapshotReader",
        MagicMock(return_value=app_reader),
    )
    monkeypatch.setattr(
        main_module,
        "PrivacyDataMapService",
        MagicMock(return_value=privacy_service),
    )
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
        assert main_module.build_privacy_data_map_response() is privacy_response

    first_graph_db.list_schema_ids.assert_not_called()
    second_graph_db.list_schema_ids.assert_called_once()
    schema_snapshot = privacy_service.build.call_args.args[2]
    assert schema_snapshot.schema_ids == ("Task",)

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
