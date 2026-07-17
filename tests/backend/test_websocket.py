import pytest
from fastapi.testclient import TestClient

from backend.main import app, app_manager, get_db
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)

    # Isolate apps directory for app_manager inside tests
    old_apps_dir = app_manager.apps_dir
    app_manager.apps_dir = storage.apps_dir

    yield storage

    # Restore original apps dir
    app_manager.apps_dir = old_apps_dir


def test_websocket_chat_flow(test_session, monkeypatch):
    # Mock IntentRouter.route to bypass LLM classification in websocket test
    async def mock_route(content, existing_apps=None, db_session=None, **_kwargs):
        from backend.agent.intent_plan import IntentKind, IntentPlan

        return IntentPlan(
            kind=IntentKind.CONVERSE,
            rationale="chitchat",
            instruction=content,
        )

    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # Mock LLM API call
    async def mock_call_llm_api(provider, model, prompt, tools=None):
        return "I am your Ambient Agent. You said: 'Hello Agent'"

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)

    # Override get_db dependency to use test database session
    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    # Connect to WebSocket
    with client.websocket_connect("/ws/chat") as websocket:
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"
        # Send a chat message
        websocket.send_json({"sender": "user", "content": "Hello Agent"})

        # 1. Expect an acknowledgment containing the saved message from the DB
        ack = websocket.receive_json()
        assert ack["type"] == "ack"
        assert ack["message"]["sender"] == "user"
        assert ack["message"]["content"] == "Hello Agent"
        assert ack["message"]["id"] is not None

        # Expect session status running update
        status_running = websocket.receive_json()
        assert status_running["type"] == "session_status_update"
        assert status_running["status"] == "running"

        # 2. Expect thinking indicator
        thinking = websocket.receive_json()
        assert thinking["type"] == "reply"
        assert thinking["message"]["id"] == -1
        assert "Thinking" in thinking["message"]["content"]

        # 3. Expect a reply from the agent
        reply = websocket.receive_json()
        assert reply["type"] == "reply"
        assert reply["message"]["sender"] == "agent"
        assert "Hello Agent" in reply["message"]["content"]

        # Expect session status idle update
        status_idle = websocket.receive_json()
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    # Clean up dependency overrides
    app.dependency_overrides.clear()


def test_websocket_widget_trigger_flow(test_session, monkeypatch):
    # Mock IntentRouter.route to bypass LLM classification in websocket test
    async def mock_route(content, existing_apps=None, db_session=None, **_kwargs):
        from backend.agent.intent_plan import IntentKind, IntentPlan

        return IntentPlan(
            kind=IntentKind.CONVERSE,
            rationale="chitchat",
            instruction=content,
        )

    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    async def mock_call_llm_api(provider, model, prompt, tools=None):
        return """
        I've generated a weather widget on your workspace canvas.
        <ambient-widget id="weather-card" title="Local Weather">
        <js-script>
        export default function App() {
            return ambient.html`<div>Beijing Weather</div>`;
        }
        </js-script>
        </ambient-widget>
        """

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as websocket:
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"
        websocket.send_json({"sender": "user", "content": "Give me weather details"})

        # 1. ACK
        ack = websocket.receive_json()
        assert ack["type"] == "ack"

        # Expect session status running update
        status_running = websocket.receive_json()
        assert status_running["type"] == "session_status_update"
        assert status_running["status"] == "running"

        # 2. Expect thinking indicator
        thinking = websocket.receive_json()
        assert thinking["type"] == "reply"
        assert thinking["message"]["id"] == -1
        assert "Thinking" in thinking["message"]["content"]

        # 3. Reply
        reply = websocket.receive_json()
        assert reply["type"] == "reply"
        assert "weather widget" in reply["message"]["content"]
        assert "<ambient-widget" not in reply["message"]["content"]  # XML block must be stripped!

        # 4. Widget
        widget_msg = websocket.receive_json()
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "weather-card"
        assert widget_msg["widget"]["title"] == "Local Weather"
        assert "Beijing" in widget_msg["widget"]["js"]

        # Expect session status idle update
        status_idle = websocket.receive_json()
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    app.dependency_overrides.clear()


def test_websocket_mcp_call_flow(test_session, monkeypatch):
    # Override get_db dependency
    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    # Create a mock app in app_manager
    from backend.app_manifest import AppManifest

    manifest = AppManifest(
        manifest_version=1,
        id="mcp-app",
        title="MCP App",
        description="",
        app_version="0.1.0",
        intents=(),
        schema_refs=(),
        backend_type="mcp",
        mcp_server={"command": ["python"], "args": ["-m", "echo"]},
    )

    # Mock get_manifest call
    def mock_get_manifest(app_id):
        if app_id == "mcp-app":
            return manifest
        return None

    monkeypatch.setattr(app_manager, "get_manifest", mock_get_manifest)

    # Mock the StdioJsonRpcClient to prevent spawning actual python command
    class MockClient:
        async def call(self, method, params):
            return {"echo": params}

    # Retrieve backend_manager instance
    from backend.main import backend_manager

    async def mock_get_or_start_mcp_client(app_id, manifest, send_ws_message_func):
        # Trigger permission request to verify that flow works
        approved = await backend_manager.request_permission(
            app_id,
            "mcp_spawn",
            {"command": manifest.mcp_server["command"], "args": manifest.mcp_server["args"]},
            send_ws_message_func,
        )
        if not approved:
            raise Exception("Denied")
        return MockClient()

    monkeypatch.setattr(backend_manager, "get_or_start_mcp_client", mock_get_or_start_mcp_client)

    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as websocket:
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"

        # Send mcp_call_tool message
        websocket.send_json(
            {
                "type": "mcp_call_tool",
                "app_id": "mcp-app",
                "name": "test_tool",
                "arguments": {"x": 1},
                "call_id": "call-123",
            }
        )

        # Expect permission request message
        perm_req = websocket.receive_json()
        assert perm_req["type"] == "backend_permission_request"
        assert perm_req["permission_type"] == "mcp_spawn"
        request_id = perm_req["request_id"]

        # Respond to permission request
        websocket.send_json({"type": "backend_permission_response", "request_id": request_id, "approved": True})

        # Expect mcp_call_response message
        call_res = websocket.receive_json()
        assert call_res["type"] == "mcp_call_response"
        assert call_res["call_id"] == "call-123"
        assert call_res["result"] == {"echo": {"name": "test_tool", "arguments": {"x": 1}}}

    app.dependency_overrides.clear()
