from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.harness import AgentOrchestrator
from backend.agent.router import IntentRouter
from backend.agent.tools import ToolRegistry
from backend.models import ChatSession
from backend.workspace_storage import WorkspaceStorage


@pytest.mark.asyncio
async def test_intent_router(monkeypatch):
    """The router classifies into IntentPlan and surfaces ambiguity as clarify."""
    from backend.agent.intent_plan import IntentKind
    from backend.router_context import RouterContext

    async def mock_call_api(provider, model, messages, tools=None):
        user_message = messages[-1]["content"]
        # Simulate the LLM's function-call payload for each known phrase
        if "Hello" in user_message:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": '{"kind": "converse", "rationale": "chitchat"}',
                        },
                    }
                ],
            }
        elif "待办" in user_message:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": '{"kind": "widget_create", "app_id": "todo-app-1234", "instruction": "给我创建一个待办 widget", "rationale": "build new"}',
                        },
                    }
                ],
            }
        elif "weather" in user_message:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": '{"kind": "widget_create", "app_id": "weather-app-5678", "instruction": "build a new widget to show weather", "rationale": "build new"}',
                        },
                    }
                ],
            }
        elif "Make clock-app-1234 look glassmorphic" in user_message:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": '{"kind": "widget_modify", "app_id": "clock-app-1234", "instruction": "Make clock-app-1234 look glassmorphic", "rationale": "modify existing"}',
                        },
                    }
                ],
            }
        elif "把时钟修改一下" in user_message:
            # LLM picks widget_modify with the base name (ambiguous)
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": '{"kind": "widget_modify", "app_id": "clock-app", "instruction": "把时钟修改一下", "rationale": "modify clock"}',
                        },
                    }
                ],
            }
        else:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": '{"kind": "converse", "rationale": "default"}',
                        },
                    }
                ],
            }

    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call_api)

    # 1. Conversational path
    plan = await IntentRouter.route("Hello, how are you?", [])
    assert plan.kind == IntentKind.CONVERSE
    assert plan.app_id is None
    assert plan.instruction == "Hello, how are you?"

    # 2. Explicit slash command
    plan = await IntentRouter.route("/app calculator-app Add a new divide button", [])
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "calculator-app"
    assert plan.instruction == "Add a new divide button"

    # 3. Chinese creation phrase
    plan = await IntentRouter.route("给我创建一个待办 widget", [])
    assert plan.kind == IntentKind.WIDGET_CREATE
    assert "todo-app-" in (plan.app_id or "")
    assert plan.instruction == "给我创建一个待办 widget"

    # 4. English creation pattern
    plan = await IntentRouter.route("build a new widget to show weather", [])
    assert plan.kind == IntentKind.WIDGET_CREATE
    assert "weather-app-" in (plan.app_id or "")

    # 5. Existing app modification mention — single match
    plan = await IntentRouter.route(
        "Make clock-app-1234 look glassmorphic",
        RouterContext(app_manifests=[{"id": "clock-app-1234", "title": "My Clock"}]),
    )
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "clock-app-1234"

    # 6. Existing app mention — multiple matches → downgrade to clarify
    plan = await IntentRouter.route(
        "把时钟修改一下",
        RouterContext(
            app_manifests=[
                {"id": "clock-app-1234", "title": "First Clock"},
                {"id": "clock-app-5678", "title": "Second Clock"},
            ]
        ),
    )
    assert plan.kind == IntentKind.CLARIFY
    assert plan.app_id is None
    assert "我发现您有多个同类型应用" in plan.clarification_message


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

    # Mock IntentRouter.route directly to avoid calling the real LLM endpoint
    from backend.agent.intent_plan import IntentKind, IntentPlan

    mock_route = AsyncMock(
        return_value=IntentPlan(
            kind=IntentKind.CONVERSE,
            rationale="chitchat",
            instruction="Who are you?",
        )
    )
    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # Mock database session
    db_session = MagicMock(spec=WorkspaceStorage)
    db_session.get.return_value = ChatSession(id="sess-1", title="Test")
    db_session.get_messages.return_value = []

    # Mock app manager
    app_manager = MagicMock()
    app_manager.list_apps.return_value = []

    orchestrator = AgentOrchestrator(db_session=db_session, app_manager=app_manager)

    on_update = AsyncMock()

    agent_msg, widget = await orchestrator.handle_message(
        session_id="sess-1", content="Who are you?", on_update=on_update
    )

    assert agent_msg.content == "Hello! I am here to help you."
    assert agent_msg.role == "agent"
    assert widget is None
