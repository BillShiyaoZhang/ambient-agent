import os
import pytest
from httpx import ASGITransport, AsyncClient

from backend.models import ChatMessage
from backend.main import app
from backend.workspace_storage import WorkspaceStorage

@pytest.fixture(name="session")
def session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)
    yield storage

@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Ambient Agent is running"}

@pytest.mark.asyncio
async def test_database_initialization(session):
    # Create a message
    msg = ChatMessage(session_id="test-session", sender="user", content="Hello Agent")
    session.add(msg)
    session.commit()
    session.refresh(msg)
    
    # Read the message back
    assert msg.id is not None
    assert msg.sender == "user"
    assert msg.content == "Hello Agent"
    assert msg.timestamp is not None
