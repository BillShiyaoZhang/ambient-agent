import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import SQLModel, create_engine, Session
from backend.main import app, get_db
from backend.models import LLMAuditLog
from backend.llm_service import generate_agent_response

TEST_DATABASE_URL = "sqlite:///./test_llm_audit.db"

@pytest.fixture(name="test_session")
def test_session_fixture():
    import os
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()
    if os.path.exists("./test_llm_audit.db"):
        os.remove("./test_llm_audit.db")

@pytest.mark.asyncio
async def test_llm_audit_logging(test_session, monkeypatch):
    # Mock the actual LLM call to return a fixed string
    # We will patch backend.llm_service.call_llm_api
    mock_response = "Mocked LLM reply containing no widget."
    
    async def mock_call_llm_api(provider, model, prompt):
        return mock_response
        
    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)
    
    # Run the generate service
    response = await generate_agent_response(
        user_message="Hello!",
        provider="ollama",
        model="llama3",
        session=test_session
    )
    
    assert response == mock_response
    
    # Verify that an audit log was written to the DB
    from sqlmodel import select
    logs = test_session.exec(select(LLMAuditLog)).all()
    assert len(logs) == 1
    assert logs[0].provider == "ollama"
    assert logs[0].model == "llama3"
    assert "Hello!" in logs[0].prompt
    assert logs[0].response == mock_response
    assert logs[0].timestamp is not None

@pytest.mark.asyncio
async def test_audit_logs_api(test_session):
    # Add a mock log
    log_entry = LLMAuditLog(
        provider="openai",
        model="gpt-4o",
        prompt="Tell me a joke",
        response="Joke content"
    )
    test_session.add(log_entry)
    test_session.commit()
    
    def override_get_db():
        yield test_session
        
    app.dependency_overrides[get_db] = override_get_db
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/audit-logs")
        
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["provider"] == "openai"
    assert data[0]["model"] == "gpt-4o"
    assert data[0]["prompt"] == "Tell me a joke"
    assert data[0]["response"] == "Joke content"
    
    app.dependency_overrides.clear()
