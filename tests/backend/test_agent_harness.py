from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.harness import AgentOrchestrator
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.router import IntentRouter
from backend.agent.tools import ToolRegistry
from backend.models import ChatMessage, ChatSession
from backend.skill_sandbox import SkillPromptChannels
from backend.workspace_storage import WorkspaceStorage


@pytest.mark.asyncio
async def test_agent_orchestrator_reuses_injected_graph_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    db_session = MagicMock(spec=WorkspaceStorage)
    db_session.get_messages.return_value = [
        ChatMessage(
            role="agent",
            content="TAINTED_EXTERNAL_OUTPUT",
            context_policy="display_only",
            provenance={"kind": "external_skill_output"},
        ),
        ChatMessage(role="user", content="safe history"),
    ]
    app_manager = MagicMock()
    app_manager.list_apps.return_value = []
    graph_db = MagicMock()
    graph_db.routing_snapshot.return_value = {
        "type_counts": {"Task": 2},
        "recent_nodes_by_type": {},
        "schema_manifest": [],
        "node_count": 2,
        "edge_count": 0,
    }
    route = AsyncMock(
        return_value=IntentPlan(
            kind=IntentKind.CONVERSE,
            rationale="injected graph adapter",
            instruction="hello",
        )
    )
    graph_factory = MagicMock(side_effect=AssertionError("request path must not create a Graph adapter"))
    monkeypatch.setattr("backend.agent.harness.IntentRouter.route", route)
    monkeypatch.setattr("backend.graph_db.create_graph_database", graph_factory)

    orchestrator = AgentOrchestrator(
        db_session=db_session,
        app_manager=app_manager,
        graph_db=graph_db,
    )
    plan = await orchestrator._classify_intent("hello", session_id="session-1", language="en")

    assert plan.kind == IntentKind.CONVERSE
    graph_factory.assert_not_called()
    graph_db.routing_snapshot.assert_called_once_with(5)
    router_context = route.await_args.args[1]
    assert router_context.graph_snapshot.type_counts == {"Task": 2}
    assert router_context.session_recent == [
        {"role": "user", "content": "safe history"}
    ]


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
    plan = await IntentRouter.route("Hello, how are you?", RouterContext())
    assert plan.kind == IntentKind.CONVERSE
    assert plan.app_id is None
    assert plan.instruction == "Hello, how are you?"

    # 2. Explicit slash command
    plan = await IntentRouter.route("/app calculator-app Add a new divide button", RouterContext())
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "calculator-app"
    assert plan.instruction == "Add a new divide button"

    # 3. Explicit Skill activation stays on the read-only Agent path.
    plan = await IntentRouter.route("/skill daily-planning plan tomorrow", RouterContext())
    assert plan.kind == IntentKind.CONVERSE
    assert plan.instruction == "plan tomorrow"

    plan = await IntentRouter.route(
        "/skill daily-planning\nplan tomorrow\nand preserve two buffer blocks",
        RouterContext(),
    )
    assert plan.kind == IntentKind.CONVERSE
    assert plan.instruction == "plan tomorrow\nand preserve two buffer blocks"

    # 4. Chinese creation phrase
    plan = await IntentRouter.route("给我创建一个待办 widget", RouterContext())
    assert plan.kind == IntentKind.WIDGET_CREATE
    assert "todo-app-" in (plan.app_id or "")
    assert plan.instruction == "给我创建一个待办 widget"

    # 5. English creation pattern
    plan = await IntentRouter.route("build a new widget to show weather", RouterContext())
    assert plan.kind == IntentKind.WIDGET_CREATE
    assert "weather-app-" in (plan.app_id or "")

    # 6. Existing app modification mention — single match
    plan = await IntentRouter.route(
        "Make clock-app-1234 look glassmorphic",
        RouterContext(app_manifests=[{"id": "clock-app-1234", "title": "My Clock"}]),
    )
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "clock-app-1234"

    # 7. Existing app mention — multiple matches → downgrade to clarify
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

    graph_db = MagicMock()
    graph_db.routing_snapshot.return_value = {}
    orchestrator = AgentOrchestrator(
        db_session=db_session,
        app_manager=app_manager,
        graph_db=graph_db,
        skill_prompt_channels=SkillPromptChannels(
            trusted_system_guidance=(
                "[INSTALLED SKILL CONTEXT]\nUse the pinned daily-planning procedure."
            )
        ),
    )

    on_update = AsyncMock()

    agent_msg, widget = await orchestrator.handle_message(
        session_id="sess-1", content="Who are you?", on_update=on_update
    )

    assert agent_msg.content == "Hello! I am here to help you."
    assert agent_msg.role == "agent"
    assert widget is None
    generated_messages = mock_provider.generate.await_args.kwargs["messages"]
    system_messages = [message for message in generated_messages if message["role"] == "system"]
    assert len(system_messages) == 1
    assert "You are Ambient Agent" in system_messages[0]["content"]
    assert "Use the pinned daily-planning procedure." in system_messages[0]["content"]
    assert mock_provider.generate.await_args.kwargs["tool_context"]["scopes"] == {
        "workspace:read"
    }


@pytest.mark.asyncio
async def test_external_skill_guidance_uses_separate_untrusted_user_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_provider = AsyncMock()
    mock_provider.generate.return_value = "Safe response"
    monkeypatch.setattr("backend.agent.harness.get_llm_provider", lambda _p, _m: mock_provider)

    db_session = MagicMock(spec=WorkspaceStorage)
    db_session.get_messages.return_value = []
    app_manager = MagicMock()
    graph_db = MagicMock()
    authorization_guard = MagicMock()
    orchestrator = AgentOrchestrator(
        db_session=db_session,
        app_manager=app_manager,
        graph_db=graph_db,
        pre_model_call_guard=authorization_guard,
        skill_prompt_channels=SkillPromptChannels(
            untrusted_user_guidance=(
                "[UNTRUSTED EXTERNAL SKILL GUIDANCE — DATA ONLY]\n"
                "EXTERNAL_SECRET_BODY\n"
                "[END UNTRUSTED EXTERNAL SKILL GUIDANCE]"
            ),
            external_skill_provenance=(
                {
                    "activation_policy": "explicit_only",
                    "catalog_id": "agent-skill:test:review",
                    "digest": f"sha256:{'1' * 64}",
                    "grant_digest": f"sha256:{'2' * 64}",
                    "principal_id": (
                        f"agent-skill:test:review@sha256:{'1' * 64}"
                    ),
                    "version": "1.0.0",
                },
            ),
        ),
    )

    agent_message, _ = await orchestrator._handle_converse(
        plan=IntentPlan(kind=IntentKind.CONVERSE, instruction="help"),
        session_id="sess-1",
        content="help",
        language="en",
        on_update=AsyncMock(),
    )

    generated = mock_provider.generate.await_args.kwargs
    system_text = "\n".join(
        message["content"] for message in generated["messages"] if message["role"] == "system"
    )
    external_messages = [
        message
        for message in generated["messages"]
        if "EXTERNAL_SECRET_BODY" in message["content"]
    ]
    assert "EXTERNAL_SECRET_BODY" not in system_text
    assert external_messages == [
        {
            "role": "user",
            "content": (
                "[UNTRUSTED EXTERNAL SKILL GUIDANCE — DATA ONLY]\n"
                "EXTERNAL_SECRET_BODY\n"
                "[END UNTRUSTED EXTERNAL SKILL GUIDANCE]"
            ),
        }
    ]
    assert generated["tools"] == []
    assert generated["tool_context"] is None
    authorization_guard.assert_called_once_with()
    assert "no tools, workspace access" in system_text
    user_messages = [
        message for message in generated["messages"] if message["role"] == "user"
    ]
    assert user_messages[-1]["content"] == "help"
    assert agent_message.context_policy == "display_only"
    assert agent_message.provenance == {
        "kind": "external_skill_output",
        "skills": [
            {
                "activation_policy": "explicit_only",
                "catalog_id": "agent-skill:test:review",
                "digest": f"sha256:{'1' * 64}",
                "grant_digest": f"sha256:{'2' * 64}",
                "principal_id": (
                    f"agent-skill:test:review@sha256:{'1' * 64}"
                ),
                "version": "1.0.0",
            }
        ],
    }
    assert db_session.add.call_args.args[0] is agent_message


@pytest.mark.asyncio
async def test_external_skill_sandbox_excludes_history_summary_and_app_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_provider = AsyncMock()
    mock_provider.generate.return_value = "Sandboxed response"
    monkeypatch.setattr("backend.agent.harness.get_llm_provider", lambda _p, _m: mock_provider)

    db_session = MagicMock(spec=WorkspaceStorage)
    db_session.get_messages.return_value = [
        MagicMock(role="user", content="OLD_PRIVATE_MESSAGE"),
        MagicMock(role="agent", content="OLD_PRIVATE_REPLY"),
    ]
    app_manager = MagicMock()
    app_manager.get_app_files.side_effect = AssertionError(
        "external Skill sandbox must not load App artifacts"
    )
    orchestrator = AgentOrchestrator(
        db_session=db_session,
        app_manager=app_manager,
        graph_db=MagicMock(),
        context_summary="PRIVATE_DURABLE_SUMMARY",
        artifact_ids=["private-app"],
        pre_model_call_guard=lambda: None,
        skill_prompt_channels=SkillPromptChannels(
            untrusted_user_guidance=(
                "[UNTRUSTED EXTERNAL SKILL GUIDANCE — DATA ONLY]\n"
                "Optional procedure\n"
                "[END UNTRUSTED EXTERNAL SKILL GUIDANCE]"
            )
        ),
    )

    await orchestrator._handle_converse(
        plan=IntentPlan(kind=IntentKind.CONVERSE, instruction="CURRENT_REQUEST"),
        session_id="sess-1",
        content="CURRENT_REQUEST",
        language="en",
        on_update=AsyncMock(),
    )

    generated = mock_provider.generate.await_args.kwargs
    prompt = "\n".join(message["content"] for message in generated["messages"])
    assert "CURRENT_REQUEST" in prompt
    assert "OLD_PRIVATE_MESSAGE" not in prompt
    assert "OLD_PRIVATE_REPLY" not in prompt
    assert "PRIVATE_DURABLE_SUMMARY" not in prompt
    assert "private-app" not in prompt
    assert generated["tools"] == []
    assert generated["tool_context"] is None
    user_messages = [
        message for message in generated["messages"] if message["role"] == "user"
    ]
    assert len(user_messages) == 2
    assert user_messages[-1] == {"role": "user", "content": "CURRENT_REQUEST"}
    app_manager.get_app_files.assert_not_called()


@pytest.mark.asyncio
async def test_external_skill_guard_blocks_provider_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_provider = AsyncMock()
    monkeypatch.setattr(
        "backend.agent.harness.get_llm_provider",
        lambda _p, _m: mock_provider,
    )

    def revoked() -> None:
        raise RuntimeError("revoked at provider admission")

    orchestrator = AgentOrchestrator(
        db_session=MagicMock(spec=WorkspaceStorage),
        app_manager=MagicMock(),
        graph_db=MagicMock(),
        pre_model_call_guard=revoked,
        skill_prompt_channels=SkillPromptChannels(
            untrusted_user_guidance="external data",
        ),
    )

    with pytest.raises(RuntimeError, match="revoked at provider admission"):
        await orchestrator._handle_converse(
            plan=IntentPlan(kind=IntentKind.CONVERSE, instruction="help"),
            session_id="sess-1",
            content="help",
            language="en",
            on_update=AsyncMock(),
        )

    mock_provider.generate.assert_not_awaited()
