import os
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from backend.main import app, get_db, app_manager
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
    async def mock_route(content, existing_apps, db_session=None):
        return False, None, content
    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # Mock LLM API call
    async def mock_call_llm_api(provider, model, prompt):
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
        websocket.send_json({
            "sender": "user",
            "content": "Hello Agent"
        })
        
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
    async def mock_route(content, existing_apps, db_session=None):
        return False, None, content
    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # Mock LLM API call containing widget block
    async def mock_call_llm_api(provider, model, prompt):
        return """
        I've generated a weather widget on your workspace canvas.
        <ambient-widget id="weather-card" title="Local Weather">
        <html-content><div>Beijing Weather</div></html-content>
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
        websocket.send_json({
            "sender": "user",
            "content": "Give me weather details"
        })
        
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
        assert "<ambient-widget" not in reply["message"]["content"] # XML block must be stripped!
        
        # 4. Widget
        widget_msg = websocket.receive_json()
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "weather-card"
        assert widget_msg["widget"]["title"] == "Local Weather"
        assert "Beijing" in widget_msg["widget"]["html"]
        
        # Expect session status idle update
        status_idle = websocket.receive_json()
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"
        
    app.dependency_overrides.clear()
