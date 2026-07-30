import os

import pytest
from fastapi.testclient import TestClient

from backend.graph_db import GraphDatabase
from backend.graph_query_engine import execute_graph_query
from backend.graph_subscription import MAX_SUBSCRIPTIONS_PER_TARGET, SubscriptionManager
from backend.main import app


def test_execute_graph_query(tmp_path):
    # Set up temp workspace
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    db = GraphDatabase(workspace_dir)

    # Setup graph data
    db.create_node(node_id="t1", node_type="Task", properties={"title": "Task 1", "status": "pending"})
    db.create_node(node_id="t2", node_type="Task", properties={"title": "Task 2", "status": "completed"})
    db.create_node(node_id="e1", node_type="Event", properties={"title": "Meeting 1"})
    db.create_node(node_id="e2", node_type="Event", properties={"title": "Meeting 2"})

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
        "include": [{"relation": "ASSOCIATED_WITH", "target_type": "Event"}],
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
    assert rel["target"]["properties"]["title"] == "Meeting 1"


def test_execute_graph_query_uses_the_adapter_contract() -> None:
    class NonSqliteGraph:
        nodes = {
            "t1": {"id": "t1", "type": "Task", "properties": {"status": "pending"}},
            "e1": {"id": "e1", "type": "Event", "properties": {"title": "Meeting"}},
        }

        def list_nodes(self, node_type=None):
            return [node for node in self.nodes.values() if node_type is None or node["type"] == node_type]

        def get_edges(self, node_id):
            return [
                {
                    "from_id": "t1",
                    "to_id": "e1",
                    "type": "ASSOCIATED_WITH",
                    "properties": {"source": "adapter"},
                }
            ]

        def get_node(self, node_id):
            return self.nodes.get(node_id)

        def get_conn(self):
            raise AssertionError("query engine must not open a SQLite connection")

    result = execute_graph_query(
        {
            "type": "Task",
            "properties": {"status": "pending"},
            "include": [{"relation": "ASSOCIATED_WITH", "target_type": "Event"}],
        },
        NonSqliteGraph(),
    )

    assert result == [
        {
            "id": "t1",
            "type": "Task",
            "properties": {"status": "pending"},
            "relations": [
                {
                    "edge_type": "ASSOCIATED_WITH",
                    "properties": {"source": "adapter"},
                    "target": {"id": "e1", "type": "Event", "properties": {"title": "Meeting"}},
                }
            ],
        }
    ]


def test_graph_query_and_subscription_budgets_are_enforced(tmp_path):
    database = GraphDatabase(str(tmp_path / "workspace"))
    for index in range(3):
        database.create_node(
            node_id=f"task-{index}",
            node_type="Task",
            properties={"title": f"Task {index}", "status": "pending"},
        )

    assert len(execute_graph_query({"type": "Task", "limit": 2}, database)) == 2
    with pytest.raises(ValueError, match="limit"):
        execute_graph_query({"type": "Task", "limit": 0}, database)
    with pytest.raises(ValueError, match="include"):
        execute_graph_query({"type": "Task", "include": ["invalid"]}, database)

    manager = SubscriptionManager()
    target = object()
    for index in range(MAX_SUBSCRIPTIONS_PER_TARGET):
        manager.register(target, f"subscription-{index}", {"type": "Task", "limit": 1}, database)
    with pytest.raises(ValueError, match="subscription limit"):
        manager.register(target, "one-too-many", {"type": "Task", "limit": 1}, database)
    assert len(manager.active_subscriptions[target]) == MAX_SUBSCRIPTIONS_PER_TARGET

    invalid_target = object()
    with pytest.raises(ValueError, match="limit"):
        manager.register(invalid_target, "invalid-query", {"type": "Task", "limit": 0}, database)
    assert invalid_target not in manager.active_subscriptions


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
        "idempotency_key": f"graph-endpoint-create-v1:{tmp_path}",
        "actions": [
            {
                "action": "create_node",
                "id": "t-mut-1",
                "type": "Task",
                "properties": {"title": "Task Mut 1", "status": "pending"},
            },
            {
                "action": "create_node",
                "id": "e-mut-1",
                "type": "Event",
                "properties": {"title": "Event Mut 1"},
            },
            {
                "action": "create_edge",
                "from_id": "t-mut-1",
                "to_id": "e-mut-1",
                "type": "ASSOCIATED_WITH",
                "properties": {"note": "mutation check"},
            },
        ],
    }

    response = client.post("/api/graph/mutate", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["status"] == "success"
    durable_run = main.run_store.get_run(res_data["run_id"], include_events=True)
    assert durable_run["status"] == "succeeded"
    assert durable_run["state"]["phase"] == "done"
    assert any(event["type"] == "interaction_requested" for event in durable_run["events"])
    assert any(event["type"] == "interaction_resolved" for event in durable_run["events"])

    duplicate = client.post("/api/graph/mutate", json=payload).json()
    assert duplicate["status"] == "success"
    assert duplicate["run_id"] == res_data["run_id"]
    assert duplicate["ticket_id"] == res_data["ticket_id"]

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
            {"action": "update_node_property", "id": "t-mut-1", "properties": {"status": "completed"}},
            {"action": "delete_edge", "from_id": "t-mut-1", "to_id": "e-mut-1", "type": "ASSOCIATED_WITH"},
        ]
    }

    response2 = client.post("/api/graph/mutate", json=payload2)
    assert response2.status_code == 200

    db.load()  # Refresh
    node_t = db.get_node("t-mut-1")
    assert node_t["properties"]["status"] == "completed"
    assert len(db.get_edges("t-mut-1")) == 0


def test_graph_mutation_endpoint_rejects_invalid_and_oversized_batches(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)

    from backend import main

    main.graph_db = GraphDatabase(workspace_dir)
    client = TestClient(app)

    invalid = client.post(
        "/api/graph/mutate",
        json={"actions": [{"action": "not-supported"}]},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_graph_mutation"

    too_many = client.post(
        "/api/graph/mutate",
        json={"actions": [{"action": "create_node", "id": f"node-{index}"} for index in range(501)]},
    )
    assert too_many.status_code == 422

    too_large = client.post(
        "/api/graph/mutate",
        json={
            "actions": [
                {
                    "action": "create_node",
                    "id": "oversized",
                    "properties": {"payload": "x" * (1024 * 1024)},
                }
            ]
        },
    )
    assert too_large.status_code == 422


def test_graph_mutation_idempotency_precedes_state_dependent_preflight(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)

    from backend import main

    main.graph_db = GraphDatabase(workspace_dir)
    client = TestClient(app)

    generated_payload = {
        "idempotency_key": f"generated-id:{tmp_path}",
        "actions": [
            {
                "action": "create_node",
                "type": "Task",
                "properties": {"title": "Generated identity"},
            }
        ],
    }
    created = client.post("/api/graph/mutate", json=generated_payload)
    duplicate_create = client.post("/api/graph/mutate", json=generated_payload)
    assert created.status_code == duplicate_create.status_code == 200
    assert duplicate_create.json()["run_id"] == created.json()["run_id"]
    assert duplicate_create.json()["ticket_id"] == created.json()["ticket_id"]
    generated_id = created.json()["actions"][0]["id"]
    assert generated_id

    delete_payload = {
        "idempotency_key": f"post-delete:{tmp_path}",
        "actions": [{"action": "delete_node", "id": generated_id}],
    }
    deleted = client.post("/api/graph/mutate", json=delete_payload)
    duplicate_delete = client.post("/api/graph/mutate", json=delete_payload)
    assert deleted.status_code == duplicate_delete.status_code == 200
    assert duplicate_delete.json()["run_id"] == deleted.json()["run_id"]
    assert duplicate_delete.json()["ticket_id"] == deleted.json()["ticket_id"]
    assert main.graph_db.get_node(generated_id) is None


def test_graph_mutation_wait_timeout_returns_pollable_pending_contract(monkeypatch):
    from backend import main

    async def pending_mutation(*_args, **_kwargs):
        return {"id": "run-still-active", "status": "running", "_wait_timed_out": True}

    monkeypatch.setattr(main, "_run_approved_graph_mutation", pending_mutation)
    response = TestClient(app).post(
        "/api/graph/mutate",
        json={"actions": [{"action": "create_node", "id": "pending-node"}]},
    )

    assert response.status_code == 202
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "status": "pending",
        "run_id": "run-still-active",
        "poll_url": "/api/runs/run-still-active",
        "message": "Graph mutation is still running; poll the durable Run for its terminal result",
    }
