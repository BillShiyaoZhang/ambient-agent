import os

from fastapi.testclient import TestClient

from backend.graph_db import GraphDatabase
from backend.graph_query_engine import execute_graph_query
from backend.main import app


def test_execute_graph_query(tmp_path):
    # Set up temp workspace
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    db = GraphDatabase(workspace_dir)

    # Setup graph data
    db.create_node(node_id="t1", node_type="Task", properties={"title": "Task 1", "status": "pending"})
    db.create_node(node_id="t2", node_type="Task", properties={"title": "Task 2", "status": "completed"})
    db.create_node(node_id="e1", node_type="CalendarEvent", properties={"summary": "Meeting 1"})
    db.create_node(node_id="e2", node_type="CalendarEvent", properties={"summary": "Meeting 2"})

    db.create_edge(from_id="t1", to_id="e1", edge_type="ASSOCIATED_WITH", properties={"p1": "v1"})
    db.create_edge(from_id="t2", to_id="e2", edge_type="ASSOCIATED_WITH")

    # 1. Test Query by type
    q1 = {"type": "Task"}
    res1 = execute_graph_query(q1, db)
    assert len(res1) == 2
    ids = {r["id"] for r in res1}
    assert ids == {"t1", "t2"}

    # 2. Test Query by type and properties filter
    q2 = {"type": "Task", "properties": {"status": "completed"}}
    res2 = execute_graph_query(q2, db)
    assert len(res2) == 1
    assert res2[0]["id"] == "t2"

    # 3. Test Query with relations include
    q3 = {
        "type": "Task",
        "properties": {"status": "pending"},
        "include": [
            {
                "relation": "ASSOCIATED_WITH",
                "target_type": "CalendarEvent"
            }
        ]
    }
    res3 = execute_graph_query(q3, db)
    assert len(res3) == 1
    root = res3[0]
    assert root["id"] == "t1"
    assert len(root["relations"]) == 1
    rel = root["relations"][0]
    assert rel["edge_type"] == "ASSOCIATED_WITH"
    assert rel["properties"]["p1"] == "v1"
    assert rel["target"]["id"] == "e1"
    assert rel["target"]["properties"]["summary"] == "Meeting 1"

def test_graph_mutation_endpoint(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)

    # Force Main App backend configuration to reload / use this temp directory
    from backend import main
    # Re-initialize the GraphDatabase in main using the mocked env
    main.graph_db = GraphDatabase(workspace_dir)

    client = TestClient(app)

    # Create nodes first
    payload = {
        "actions": [
            {
                "action": "create_node",
                "id": "t-mut-1",
                "type": "Task",
                "properties": {"title": "Task Mut 1", "status": "pending"}
            },
            {
                "action": "create_node",
                "id": "e-mut-1",
                "type": "CalendarEvent",
                "properties": {"summary": "Event Mut 1"}
            },
            {
                "action": "create_edge",
                "from_id": "t-mut-1",
                "to_id": "e-mut-1",
                "type": "ASSOCIATED_WITH",
                "properties": {"note": "mutation check"}
            }
        ]
    }

    response = client.post("/api/graph/mutate", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "success"

    # Query database to check if nodes and edge exist
    db = main.graph_db
    node_t = db.get_node("t-mut-1")
    assert node_t is not None
    assert node_t["properties"]["title"] == "Task Mut 1"

    node_e = db.get_node("e-mut-1")
    assert node_e is not None

    edges = db.get_edges("t-mut-1")
    assert len(edges) == 1
    assert edges[0]["type"] == "ASSOCIATED_WITH"

    # Test update and delete
    payload2 = {
        "actions": [
            {
                "action": "update_node_property",
                "id": "t-mut-1",
                "properties": {"status": "completed"}
            },
            {
                "action": "delete_edge",
                "from_id": "t-mut-1",
                "to_id": "e-mut-1",
                "type": "ASSOCIATED_WITH"
            }
        ]
    }

    response2 = client.post("/api/graph/mutate", json=payload2)
    assert response2.status_code == 200

    db.load() # Refresh
    node_t = db.get_node("t-mut-1")
    assert node_t["properties"]["status"] == "completed"
    assert len(db.get_edges("t-mut-1")) == 0
