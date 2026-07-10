import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlmodel import SQLModel, create_engine, Session
import os

from backend.agent.providers import OllamaProvider
from backend.agent.tools import ToolRegistry, registry as global_registry
from backend.models import LLMAuditLog
import backend.llm_service

TEST_DATABASE_URL = "sqlite:///./test_tool_calling.db"

@pytest.fixture(name="test_session")
def test_session_fixture():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)
    engine.dispose()
    if os.path.exists("./test_tool_calling.db"):
        os.remove("./test_tool_calling.db")

@pytest.mark.asyncio
async def test_tool_calling_loop(test_session, monkeypatch):
    # 1. Register a test tool in the global registry
    tool_executed = False
    received_app_id = None

    @global_registry.register
    def test_delete_app(app_id: str) -> str:
        """
        Delete a mock app.
        :param app_id: The ID of the app.
        """
        nonlocal tool_executed, received_app_id
        tool_executed = True
        received_app_id = app_id
        return "success_delete"

    # 2. Mock call_llm_api to simulate a 2-step tool loop:
    # First call: return a request to execute "test_delete_app"
    # Second call: return final text response
    mock_calls = []

    async def mock_call_llm_api(provider, model, messages, tools=None):
        mock_calls.append(list(messages))
        if len(mock_calls) == 1:
            return {
                "content": "I need to delete the app.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "test_delete_app",
                            "arguments": '{"app_id": "test-widget"}'
                        }
                    }
                ]
            }
        else:
            return {
                "content": "Successfully deleted the app.",
                "tool_calls": None
            }

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)

    # 3. Instantiate OllamaProvider and run generate
    provider = OllamaProvider(model="test-model")
    messages = [{"role": "user", "content": "Delete test-widget"}]
    
    # We pass the tools schema list
    tools_schema = global_registry.get_tool_schemas()
    
    response = await provider.generate(
        messages=messages,
        db_session=test_session,
        tools=tools_schema
    )

    # 4. Verify tool was executed
    assert tool_executed is True
    assert received_app_id == "test-widget"
    assert response == "Successfully deleted the app."

    # 5. Verify the messages history was correctly built in the loop:
    # First API call got 1 user message.
    assert len(mock_calls[0]) == 1
    assert mock_calls[0][0]["content"] == "Delete test-widget"

    # Second API call got:
    # - User message
    # - Assistant message requesting tool execution
    # - Tool response message
    assert len(mock_calls[1]) == 3
    assert mock_calls[1][0]["role"] == "user"
    assert mock_calls[1][1]["role"] == "assistant"
    assert mock_calls[1][1]["tool_calls"][0]["function"]["name"] == "test_delete_app"
    assert mock_calls[1][2]["role"] == "tool"
    assert mock_calls[1][2]["name"] == "test_delete_app"
    assert mock_calls[1][2]["content"] == "success_delete"

    # 6. Verify audit logging: both LLM calls should have been logged in LLMAuditLog
    from sqlmodel import select
    logs = test_session.exec(select(LLMAuditLog)).all()
    assert len(logs) == 2
    # Verify first log has tool request
    assert "test_delete_app" in logs[0].response or "call_1" in logs[0].response or "I need to delete the app." in logs[0].response
    # Verify second log has final response
    assert logs[1].response == "Successfully deleted the app."

    # Clean up registry to avoid polluting other tests
    global_registry.tools.pop("test_delete_app", None)
    global_registry.schemas.pop("test_delete_app", None)
