import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlmodel import Session

from backend.agent.router import IntentRouter
from backend.agent.tools import ToolRegistry, registry as global_registry
from backend.agent.harness import AgentOrchestrator
from backend.models import ChatMessage, ChatSession

def test_intent_router():
    # 1. Test conversational path
    is_coding, app_id, instr = IntentRouter.route("Hello, how are you?", [])
    assert not is_coding
    assert app_id is None
    assert instr == "Hello, how are you?"

    # 2. Test explicit slash command
    is_coding, app_id, instr = IntentRouter.route("/app calculator-app Add a new divide button", [])
    assert is_coding
    assert app_id == "calculator-app"
    assert instr == "Add a new divide button"

    # 3. Test Chinese creation phrase
    is_coding, app_id, instr = IntentRouter.route("给我创建一个待办 widget", [])
    assert is_coding
    assert "todo-app-" in app_id
    assert instr == "给我创建一个待办 widget"

    # 4. Test English creation pattern
    is_coding, app_id, instr = IntentRouter.route("build a new widget to show weather", [])
    assert is_coding
    assert "weather-app-" in app_id
    assert instr == "build a new widget to show weather"

    # 5. Test existing app modification mention
    existing = [{"id": "clock-app-1234", "title": "My Clock"}]
    is_coding, app_id, instr = IntentRouter.route("Make clock-app-1234 look glassmorphic", existing)
    assert is_coding
    assert app_id == "clock-app-1234"
    assert instr == "Make clock-app-1234 look glassmorphic"

    # 6. Test existing app mention using mapped terms
    is_coding, app_id, instr = IntentRouter.route("把时钟修改一下", existing)
    assert is_coding
    assert app_id == "clock-app-1234"

def test_tool_registry():
    reg = ToolRegistry()

    @reg.register
    def dummy_tool(name: str, count: int = 1) -> str:
        """
        A dummy test tool.
        :param name: The dummy name.
        :param count: How many items.
        """
        return f"Hello {name} x{count}"

    schemas = reg.get_tool_schemas()
    assert len(schemas) == 1
    func_schema = schemas[0]["function"]
    assert func_schema["name"] == "dummy_tool"
    assert func_schema["description"] == "A dummy test tool."
    
    params = func_schema["parameters"]["properties"]
    assert "name" in params
    assert params["name"]["type"] == "string"
    assert params["count"]["type"] == "integer"
    assert "name" in func_schema["parameters"]["required"]

@pytest.mark.asyncio
async def test_agent_orchestrator_conversational(monkeypatch):
    # Mock LLM provider
    mock_provider = AsyncMock()
    mock_provider.generate.return_value = "Hello! I am here to help you."
    
    # Mock the get_llm_provider function to return our mock provider
    monkeypatch.setattr("backend.agent.harness.get_llm_provider", lambda p, m: mock_provider)
    
    # Mock database session
    db_session = MagicMock(spec=Session)
    db_session.get.return_value = ChatSession(id="sess-1", title="Test")
    
    # Mock app manager
    app_manager = MagicMock()
    app_manager.list_apps.return_value = []
    
    orchestrator = AgentOrchestrator(db_session=db_session, app_manager=app_manager)
    
    on_update = AsyncMock()
    
    agent_msg, widget = await orchestrator.handle_message(
        session_id="sess-1",
        content="Who are you?",
        on_update=on_update
    )
    
    assert agent_msg.content == "Hello! I am here to help you."
    assert agent_msg.role == "agent"
    assert widget is None
    on_update.assert_called_with("🤔 Thinking...")
