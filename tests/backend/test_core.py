import os
import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel, create_engine, Session

# Import models early so SQLModel registers them on metadata
from backend.models import ChatMessage
from backend.main import app

TEST_DATABASE_URL = "sqlite://"

@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()

@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Ambient Agent is running"}

@pytest.mark.asyncio
async def test_database_initialization(session):
    # Create a message
    msg = ChatMessage(sender="user", content="Hello Agent")
    session.add(msg)
    session.commit()
    session.refresh(msg)
    
    # Read the message back
    assert msg.id is not None
    assert msg.sender == "user"
    assert msg.content == "Hello Agent"
    assert msg.timestamp is not None
