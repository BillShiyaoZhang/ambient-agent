import os
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session
from backend.main import app, get_db
from backend.models import ChatMessage

TEST_DATABASE_URL = "sqlite:///./test_websocket.db"

# Set up test database fixture for testing HTTP/WS requests
@pytest.fixture(name="test_session")
def test_session_fixture():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()
    if os.path.exists("./test_websocket.db"):
        os.remove("./test_websocket.db")

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
        websocket.send_json({
            "sender": "user",
            "content": "Give me weather details"
        })
        
        # 1. ACK
        ack = websocket.receive_json()
        assert ack["type"] == "ack"
        
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
        
        # 3. Widget
        widget_msg = websocket.receive_json()
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "weather-card"
        assert widget_msg["widget"]["title"] == "Local Weather"
        assert "Beijing" in widget_msg["widget"]["html"]
        
    app.dependency_overrides.clear()
