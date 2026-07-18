import pytest
from fastapi.testclient import TestClient

from backend.main import app, app_manager, get_db
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)

    # Isolate apps directory for app_manager inside this test
    old_apps_dir = app_manager.apps_dir
    app_manager.apps_dir = storage.apps_dir

    yield storage

    # Restore original apps directory
    app_manager.apps_dir = old_apps_dir


def test_websocket_opencode_routing(test_session, monkeypatch):
    # Mock run_opencode_agent_acp to avoid running a real command line process
    async def mock_run_opencode_agent_acp(app_id, instruction, language="zh", on_update=None):
        # Stream a mocked update
        await on_update("Mocked OpenCode progress update")
        # Simulate creating/modifying the app files on disk
        app_manager.create_or_update_app(
            app_id=app_id,
            title="Test Timer App",
            js="export default function App() { return ambient.html`<div class='timer'>12:34</div>`; }",
        )
        return "Mocked OpenCode success: stopwatch files updated on disk."

    monkeypatch.setattr("backend.main.run_opencode_agent_acp", mock_run_opencode_agent_acp)

    # Override get_db dependency
    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as websocket:
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"
        # Send a message starting with /app to trigger OpenCode routing
        websocket.send_json({"sender": "user", "content": "/app test-timer Add standard start/stop buttons"})

        # 1. Expect acknowledgment
        ack = websocket.receive_json()
        assert ack["type"] == "ack"

        # Expect session status running update
        status_running = websocket.receive_json()
        assert status_running["type"] == "session_status_update"
        assert status_running["status"] == "running"

        # 2. Expect the status message about OpenCode starting
        status = websocket.receive_json()
        assert status["type"] == "reply"
        assert any(x in status["message"]["content"] for x in ["Starting OpenCode agent", "启动 OpenCode 开发者智能体"])
        assert status["message"]["id"] == -1

        # 3. Expect the mocked progress update
        progress = websocket.receive_json()
        assert progress["type"] == "reply"
        assert "Mocked OpenCode progress update" in progress["message"]["content"]
        assert progress["message"]["id"] == -1

        # 4. Expect the final execution log reply
        reply = websocket.receive_json()
        assert reply["type"] == "reply"
        assert "Mocked OpenCode success" in reply["message"]["content"]

        # 5. Expect the updated widget to be sent
        widget_msg = websocket.receive_json()
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "test-timer"
        assert widget_msg["widget"]["title"] == "Test Timer App"
        assert "12:34" in widget_msg["widget"]["js"]

        # Expect session status idle update
        status_idle = websocket.receive_json()
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    app.dependency_overrides.clear()
