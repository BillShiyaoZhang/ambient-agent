def test_websocket_graph_subscription(graph_api_client):
    from backend import main

    client = graph_api_client
    main.app_manager.create_or_update_app(
        "sync-app",
        "Sync App",
        js="export default function App() {}",
        capabilities=[{"id": "graph.query", "scope": {"entities": ["Task"]}}],
    )
    manifest = main.app_manager.get_manifest("sync-app")
    assert manifest is not None

    # graph_api_client owns the application lifespan and its durable coordinator.
    assert (
        client.post(
            "/api/sessions",
            json={"id": "sync-sess-1", "title": "Graph sync", "language": "en"},
        ).status_code
        == 200
    )
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
        assert response.status_code == 200, response.text

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
