import os
import pytest
import shutil
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session
from backend.main import app, get_db, app_manager
from backend.models import ChatMessage

TEST_DATABASE_URL = "sqlite:///./test_opencode.db"

@pytest.fixture(name="test_session")
def test_session_fixture():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()
    if os.path.exists("./test_opencode.db"):
        os.remove("./test_opencode.db")
        
    # Clean up test apps created during test
    test_app_dir = os.path.join("backend", "apps", "test-timer")
    if os.path.exists(test_app_dir):
        shutil.rmtree(test_app_dir)

def test_websocket_opencode_routing(test_session, monkeypatch):
    # Mock run_opencode_agent to avoid running a real command line process
    def mock_run_opencode_agent(app_id, instruction):
        # Simulate creating/modifying the app files on disk
        app_manager.create_or_update_app(
            app_id=app_id,
            title="Test Timer App",
            html="<title>Test Timer App</title><div class='timer'>12:34</div>",
            css=".timer { color: green; }",
            js="// Timer controller"
        )
        return "Mocked OpenCode success: stopwatch files updated on disk."
        
    monkeypatch.setattr("backend.main.run_opencode_agent", mock_run_opencode_agent)

    # Override get_db dependency
    def override_get_db():
        yield test_session
        
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    
    with client.websocket_connect("/ws/chat") as websocket:
        # Send a message starting with /app to trigger OpenCode routing
        websocket.send_json({
            "sender": "user",
            "content": "/app test-timer Add standard start/stop buttons"
        })
        
        # 1. Expect acknowledgment
        ack = websocket.receive_json()
        assert ack["type"] == "ack"
        
        # 2. Expect the status message about OpenCode starting
        status = websocket.receive_json()
        assert status["type"] == "reply"
        assert "🛠️ Starting OpenCode agent" in status["message"]["content"]
        assert status["message"]["id"] == -1
        
        # 3. Expect the final execution log reply
        reply = websocket.receive_json()
        assert reply["type"] == "reply"
        assert "Mocked OpenCode success" in reply["message"]["content"]
        
        # 4. Expect the updated widget to be sent
        widget_msg = websocket.receive_json()
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "test-timer"
        assert widget_msg["widget"]["title"] == "Test Timer App"
        assert "12:34" in widget_msg["widget"]["html"]

    app.dependency_overrides.clear()
