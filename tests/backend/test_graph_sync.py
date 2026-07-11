from fastapi.testclient import TestClient

from backend.graph_db import GraphDatabase
from backend.main import app


def test_websocket_graph_subscription(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)

    # Reload Graph DB instance in main
    main_db = GraphDatabase(workspace_dir)
    from backend import main
    main.graph_db = main_db

    client = TestClient(app)

    with client.websocket_connect("/ws/chat?session_id=sync-sess-1") as websocket:
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"
        # 1. Subscribe to Task nodes
        sub_msg = {
            "type": "graph_subscribe",
            "subscription_id": "sub-tasks",
            "query": {"type": "Task"}
        }
        websocket.send_json(sub_msg)

        # 2. Check for initial query result message
        data = websocket.receive_json()
        assert data["type"] == "graph_query_update"
        assert data["subscription_id"] == "sub-tasks"
        assert data["data"] == []  # initial database is empty

        # 3. Trigger mutation via REST API
        mutate_payload = {
            "actions": [
                {
                    "action": "create_node",
                    "id": "t-sync-1",
                    "type": "Task",
                    "properties": {"title": "Sync Task 1", "status": "pending"}
                }
            ]
        }
        response = client.post("/api/graph/mutate", json=mutate_payload)
        assert response.status_code == 200

        # 4. Check for update message on WebSocket
        data2 = websocket.receive_json()
        assert data2["type"] == "graph_query_update"
        assert data2["subscription_id"] == "sub-tasks"
        assert len(data2["data"]) == 1
        assert data2["data"][0]["id"] == "t-sync-1"
        assert data2["data"][0]["properties"]["title"] == "Sync Task 1"

        # 5. Unsubscribe
        unsub_msg = {
            "type": "graph_unsubscribe",
            "subscription_id": "sub-tasks"
        }
        websocket.send_json(unsub_msg)

        # 6. Trigger another mutation
        mutate_payload2 = {
            "actions": [
                {
                    "action": "create_node",
                    "id": "t-sync-2",
                    "type": "Task",
                    "properties": {"title": "Sync Task 2"}
                }
            ]
        }
        response2 = client.post("/api/graph/mutate", json=mutate_payload2)
        assert response2.status_code == 200

        # End of test
