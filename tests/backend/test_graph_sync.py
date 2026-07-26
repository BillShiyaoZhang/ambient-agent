from fastapi.testclient import TestClient

from backend.graph_db import GraphDatabase
from backend.main import app
from backend.run_service import RunCoordinator, RunStore


def test_websocket_graph_subscription(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)

    # Reload Graph DB instance in main
    main_db = GraphDatabase(workspace_dir)
    from backend import main

    isolated_run_store = RunStore(workspace_dir)
    isolated_coordinator = RunCoordinator(
        isolated_run_store,
        main.app_store,
        main.app_manager,
        main.backend_manager,
    )
    isolated_coordinator.register_internal_agent_executor(main.durable_agent_workflow)
    monkeypatch.setattr(main, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(main, "run_store", isolated_run_store)
    monkeypatch.setattr(main, "run_coordinator", isolated_coordinator)
    monkeypatch.setattr(main.durable_agent_workflow, "run_store", isolated_run_store)
    monkeypatch.setattr(main, "graph_db", main_db)
    monkeypatch.setattr(main.durable_agent_workflow, "graph_db", main_db)
    monkeypatch.setattr(main, "_closed_graph_db", main._closed_graph_db)
    monkeypatch.setattr(main, "active_running_sessions", set(main.active_running_sessions))
    monkeypatch.setattr(main.app_store, "generating_ids", set(main.app_store.generating_ids))
    main.app_manager.create_or_update_app(
        "sync-app",
        "Sync App",
        js="export default function App() {}",
        capabilities=[{"id": "graph.query", "scope": {"entities": ["Task"]}}],
    )
    manifest = main.app_manager.get_manifest("sync-app")
    assert manifest is not None

    # The mutation path is a durable Run, so the test must exercise the same
    # application lifespan that starts and stops its coordinator.
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat?session_id=sync-sess-1") as websocket:
            active_list = websocket.receive_json()
            assert active_list["type"] == "active_sessions_list"
            # 1. Subscribe to Task nodes
            sub_msg = {
                "type": "graph_subscribe",
                "subscription_id": "sub-tasks",
                "app_id": "sync-app",
                "manifest_revision": manifest.revision,
                "grants_digest": manifest.grants_digest,
                "query": {"type": "Task"},
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
                        "properties": {"title": "Sync Task 1", "status": "pending"},
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
            unsub_msg = {"type": "graph_unsubscribe", "subscription_id": "sub-tasks"}
            websocket.send_json(unsub_msg)

            # 6. Trigger another mutation
            mutate_payload2 = {
                "actions": [
                    {
                        "action": "create_node",
                        "id": "t-sync-2",
                        "type": "Task",
                        "properties": {"title": "Sync Task 2"},
                    }
                ]
            }
            response2 = client.post("/api/graph/mutate", json=mutate_payload2)
            assert response2.status_code == 200

            # End of test
