import os

from backend.app_manager import AppManager
from backend.graph_db import GraphDatabase
from backend.router_context import GraphSnapshot, RouterContext


def _make_db(tmp_path) -> GraphDatabase:
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    db = GraphDatabase(workspace_dir)
    # Erase any seed core schemas are auto-created by GraphDatabase._seed_core_schemas
    return db


def test_graph_snapshot_type_counts(tmp_path):
    db = _make_db(tmp_path)
    db.create_node(node_id="t1", node_type="Task", properties={"title": "T1", "status": "pending"})
    db.create_node(node_id="t2", node_type="Task", properties={"title": "T2", "status": "completed"})
    db.create_node(node_id="e1", node_type="CalendarEvent", properties={"summary": "E1"})

    snap = GraphSnapshot.from_db(db, recent_per_type=5)

    assert "Task" in snap.type_counts
    assert snap.type_counts["Task"] == 2
    assert snap.type_counts["CalendarEvent"] == 1
    assert snap.node_count == 3


def test_graph_snapshot_recent_per_type_capped(tmp_path):
    db = _make_db(tmp_path)
    # Create 8 Task nodes
    for i in range(8):
        db.create_node(node_id=f"t{i}", node_type="Task", properties={"title": f"T{i}"})

    snap = GraphSnapshot.from_db(db, recent_per_type=3)

    assert snap.type_counts["Task"] == 8
    assert len(snap.recent_nodes_by_type["Task"]) == 3
    # Ordered by created_at desc -> last 3 inserted come first (t5, t6, t7)
    assert [n["id"] for n in snap.recent_nodes_by_type["Task"]] == ["t7", "t6", "t5"]


def test_graph_snapshot_schema_manifest_present(tmp_path):
    db = _make_db(tmp_path)
    snap = GraphSnapshot.from_db(db)
    ids = {s["id"] for s in snap.schema_manifest}
    # Core schemas auto-seeded
    assert {"Task", "Event", "Note"}.issubset(ids)


def test_router_context_build(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    monkeypatch.setenv("APPS_DIR", str(apps_dir))

    db = _make_db(tmp_path)
    db.create_node(node_id="t1", node_type="Task", properties={"title": "T1", "status": "pending"})

    # Make an app
    am = AppManager()
    am.apps_dir = str(apps_dir)
    am.create_or_update_app("todo-app-abcd", "Todo", "<html></html>", "css", "js")

    ctx = RouterContext.build(
        app_manager=am,
        graph_db=db,
        session_messages=[
            {"role": "user", "content": "hello"},
            {"role": "agent", "content": "hi"},
        ],
        recent_messages_count=2,
    )

    assert isinstance(ctx.graph_snapshot, GraphSnapshot)
    assert ctx.app_manifests[0]["id"] == "todo-app-abcd"
    assert ctx.graph_snapshot.type_counts["Task"] == 1
    assert len(ctx.session_recent) == 2


def test_router_context_recent_messages_truncated(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    db = _make_db(tmp_path)

    monkeypatch.setenv("APPS_DIR", str(tmp_path / "apps"))

    ctx = RouterContext.build(
        app_manager=AppManager(),
        graph_db=db,
        session_messages=[{"role": "user", "content": f"m{i}"} for i in range(50)],
        recent_messages_count=5,
    )
    assert len(ctx.session_recent) == 5
    # Last 5 retained
    assert [m["content"] for m in ctx.session_recent] == ["m45", "m46", "m47", "m48", "m49"]


def test_router_context_render_for_prompt(tmp_path):
    db = _make_db(tmp_path)
    db.create_node(node_id="t1", node_type="Task", properties={"title": "T1"})

    ctx = RouterContext(
        app_manifests=[
            {
                "id": "todo-app-x",
                "title": "Todo",
                "description": "Manage things to do.",
                "intents": ["add todo item", "remove todo item"],
                "schema_refs": ["Task"],
            }
        ],
        graph_snapshot=GraphSnapshot.from_db(db),
        session_recent=[],
    )

    text = ctx.render_for_prompt()
    assert "todo-app-x" in text
    assert "Manage things to do." in text
    assert "add todo item, remove todo item" in text
    assert "Task" in text
    assert "t1" in text
