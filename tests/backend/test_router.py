import json
from unittest.mock import AsyncMock

import pytest

from backend.agent.intent_plan import IntentKind
from backend.agent.router import IntentRouter
from backend.router_context import GraphSnapshot, RouterContext


def _mock_call_api_factory(side_effect_data):
    """Build a mocked call_llm_api that returns the next item from ``side_effect_data``.

    Each item can be either a string (content only) or a dict with content/tool_calls.
    """
    calls = list(side_effect_data)
    mock = AsyncMock(side_effect=lambda *args, **kwargs: calls.pop(0))
    return mock


@pytest.mark.asyncio
async def test_route_explicit_slash_command_widget_modify():
    ctx = RouterContext()
    plan = await IntentRouter.route("/app todo-app-abcd add delete button", ctx)
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "todo-app-abcd"
    assert plan.instruction == "add delete button"


@pytest.mark.asyncio
async def test_route_slash_app_without_instruction_defaults_to_inspect():
    ctx = RouterContext()
    plan = await IntentRouter.route("/app todo-app-abcd", ctx)
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "todo-app-abcd"
    assert plan.instruction


@pytest.mark.asyncio
async def test_route_function_call_cloud_picks_graph_mutation(monkeypatch):
    mock_call = _mock_call_api_factory(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": json.dumps(
                                {
                                    "kind": "graph_mutation",
                                    "confidence": 0.92,
                                    "rationale": "user wants to add a task",
                                    "actions": [
                                        {
                                            "action": "create_node",
                                            "id": "t1",
                                            "type": "Task",
                                            "properties": {"title": "buy milk"},
                                        }
                                    ],
                                }
                            ),
                        },
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext()
    plan = await IntentRouter.route("add buy milk to todos", ctx, provider_name="openai", model_name="gpt-4")
    assert plan.kind == IntentKind.GRAPH_MUTATION
    assert plan.actions[0]["action"] == "create_node"


@pytest.mark.asyncio
async def test_route_function_call_clarify_picks_options(monkeypatch):
    mock_call = _mock_call_api_factory(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": json.dumps(
                                {
                                    "kind": "clarify",
                                    "rationale": "two todo widgets exist",
                                    "clarification_message": "Which one?",
                                    "clarification_options": [
                                        {"value": "a", "label": "Work"},
                                        {"value": "b", "label": "Personal"},
                                    ],
                                }
                            ),
                        },
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext(app_manifests=[{"id": "todo-a", "title": "Work"}, {"id": "todo-b", "title": "Personal"}])
    plan = await IntentRouter.route("add to todos", ctx, provider_name="openai", model_name="gpt-4")
    assert plan.kind == IntentKind.CLARIFY
    assert len(plan.clarification_options) == 2


@pytest.mark.asyncio
async def test_route_ollama_function_call_parse(monkeypatch):
    # Ollama's response shape can sometimes wrap args as already-parsed dict
    mock_call = _mock_call_api_factory(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": json.dumps(
                                {
                                    "kind": "graph_query",
                                    "rationale": "user asks what's today",
                                    "query": {"type": "Event"},
                                }
                            ),
                        },
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext()
    plan = await IntentRouter.route("what's on calendar today", ctx, provider_name="ollama", model_name="llama3")
    assert plan.kind == IntentKind.GRAPH_QUERY


@pytest.mark.asyncio
async def test_route_falls_back_to_converse_on_llm_failure(monkeypatch):
    mock_call = AsyncMock(side_effect=Exception("network down"))
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext()
    plan = await IntentRouter.route("hello there", ctx, provider_name="openai", model_name="gpt-4")
    assert plan.kind == IntentKind.CONVERSE
    assert "hello there" in (plan.instruction or "")


@pytest.mark.asyncio
async def test_route_falls_back_to_converse_when_no_tool_call(monkeypatch):
    mock_call = _mock_call_api_factory([{"content": "I am a polite refusal.", "tool_calls": None}])
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext()
    plan = await IntentRouter.route("hello there", ctx, provider_name="openai", model_name="gpt-4")
    # Falls back to converse
    assert plan.kind == IntentKind.CONVERSE


@pytest.mark.asyncio
async def test_route_graph_create_phrase_routes_widget_create_via_llm(monkeypatch):
    mock_call = _mock_call_api_factory(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": json.dumps(
                                {
                                    "kind": "widget_create",
                                    "confidence": 0.8,
                                    "rationale": "create a clock widget",
                                    "app_id": "clock-app-12ab",
                                    "instruction": "create a clock widget",
                                }
                            ),
                        },
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext()
    plan = await IntentRouter.route("做一个时钟", ctx, provider_name="openai", model_name="gpt-4")
    assert plan.kind == IntentKind.WIDGET_CREATE
    assert "clock-app-" in plan.app_id


@pytest.mark.asyncio
async def test_route_existing_app_match_routes_widget_modify(monkeypatch):
    # LLM picks widget_modify because the existing app matches
    mock_call = _mock_call_api_factory(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": json.dumps(
                                {
                                    "kind": "widget_modify",
                                    "confidence": 0.9,
                                    "rationale": "user asked to modify todo",
                                    "app_id": "todo-app-abcd",
                                    "instruction": "add delete button",
                                }
                            ),
                        },
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext(app_manifests=[{"id": "todo-app-abcd", "title": "My Todo"}])
    plan = await IntentRouter.route("改下待办", ctx, provider_name="openai", model_name="gpt-4")
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "todo-app-abcd"


@pytest.mark.asyncio
async def test_route_passes_router_context_to_prompt(monkeypatch):
    seen_payload = {}

    async def mock_capture(provider, model, messages, tools=None):
        seen_payload["messages"] = messages
        seen_payload["tools"] = tools
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "classify_intent",
                        "arguments": json.dumps({"kind": "converse", "rationale": "ok"}),
                    },
                }
            ],
        }

    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_capture)

    ctx = RouterContext(
        app_manifests=[{"id": "x-app", "title": "X"}],
        graph_snapshot=GraphSnapshot(type_counts={"Task": 2}),
        session_recent=[{"role": "user", "content": "hi"}],
    )
    plan = await IntentRouter.route("hello", ctx, provider_name="openai", model_name="gpt-4")
    sys_msg = seen_payload["messages"][0]["content"]
    assert "x-app" in sys_msg
    assert "Task=2" in sys_msg
    # tool schema passed in
    assert any(t.get("function", {}).get("name") == "classify_intent" for t in (seen_payload["tools"] or []))


@pytest.mark.asyncio
async def test_route_keeps_legacy_signature_for_harness_compat(monkeypatch):
    # The harness still calls the old signature passing existing_apps;
    # IntentRouter.route should accept the legacy shape and convert internally.
    mock_call = _mock_call_api_factory(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "classify_intent",
                            "arguments": json.dumps({"kind": "converse", "rationale": "ok"}),
                        },
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    # Legacy list-of-apps still accepted
    plan = await IntentRouter.route_legacy("hello", existing_apps=[{"id": "x", "title": "X"}])
    assert plan.kind == IntentKind.CONVERSE
