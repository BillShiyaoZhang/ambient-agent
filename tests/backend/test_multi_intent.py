"""Tests for multi-intent routing (Direction D)."""

import json
from unittest.mock import AsyncMock

import pytest

from backend.agent.intent_plan import IntentKind, SubIntentKind
from backend.agent.router import IntentRouter
from backend.router_context import RouterContext


def _mock_call_api_factory(side_effect_data):
    calls = list(side_effect_data)
    mock = AsyncMock(side_effect=lambda *args, **kwargs: calls.pop(0))
    return mock


@pytest.mark.asyncio
async def test_route_multi_intent_returns_sub_intents(monkeypatch):
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
                                    "kind": "multi_intent",
                                    "rationale": "add data + extend schema",
                                    "sub_intents": [
                                        {
                                            "kind": "graph_mutation",
                                            "actions": [
                                                {
                                                    "action": "create_node",
                                                    "id": "t1",
                                                    "type": "Task",
                                                    "properties": {"title": "buy eggs"},
                                                }
                                            ],
                                        },
                                        {
                                            "kind": "widget_extend_schema",
                                            "app_id": "todo-app-efgh",
                                            "extend_schema_props": {"Task": {"priority": "string"}},
                                        },
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
    plan = await IntentRouter.route(
        "add buy eggs Task and extend Task schema with priority",
        ctx,
        provider_name="openai",
        model_name="gpt-4",
    )
    assert plan.kind == IntentKind.MULTI_INTENT
    assert len(plan.sub_intents) == 2
    assert plan.sub_intents[0].kind == SubIntentKind.GRAPH_MUTATION
    assert plan.sub_intents[1].kind == SubIntentKind.WIDGET_EXTEND_SCHEMA
    assert plan.sub_intents[1].extend_schema_props == {"Task": {"priority": "string"}}


@pytest.mark.asyncio
async def test_refine_sub_intents_prompts_with_router_context(monkeypatch):
    """Layer 2: refine_sub_intents should call the LLM with a sub_intent prompt
    and merge the refined sub_intents back into the plan."""

    initial = {
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "classify_intent",
                    "arguments": json.dumps(
                        {
                            "kind": "multi_intent",
                            "rationale": "add + extend",
                            "sub_intents": [
                                {"kind": "graph_mutation", "actions": []},
                                {"kind": "widget_extend_schema", "app_id": "todo-app-efgh"},
                            ],
                        }
                    ),
                },
            }
        ],
    }

    refined_response = {
        "content": "",
        "tool_calls": [
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "classify_intent",
                    "arguments": json.dumps(
                        {
                            "kind": "multi_intent",
                            "rationale": "refined",
                            "sub_intents": [
                                {
                                    "kind": "graph_mutation",
                                    "actions": [
                                        {
                                            "action": "create_node",
                                            "id": "t1",
                                            "type": "Task",
                                            "properties": {"title": "buy eggs"},
                                        }
                                    ],
                                },
                                {
                                    "kind": "widget_extend_schema",
                                    "app_id": "todo-app-efgh",
                                    "extend_schema_props": {"Task": {"priority": "string"}},
                                },
                            ],
                        }
                    ),
                },
            }
        ],
    }
    mock_call = _mock_call_api_factory([initial, refined_response])
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    ctx = RouterContext()
    plan = await IntentRouter.route(
        "add buy eggs Task and extend Task schema with priority",
        ctx,
        provider_name="openai",
        model_name="gpt-4",
    )
    assert plan.kind == IntentKind.MULTI_INTENT
    assert len(plan.sub_intents) == 2

    # Now refine.
    refined_plan = await IntentRouter.refine_sub_intents(
        plan,
        ctx,
        provider_name="openai",
        model_name="gpt-4",
    )
    assert refined_plan is plan  # in-place mutation
    assert refined_plan.sub_intents[0].actions == [
        {
            "action": "create_node",
            "id": "t1",
            "type": "Task",
            "properties": {"title": "buy eggs"},
        }
    ]
    assert refined_plan.sub_intents[1].extend_schema_props == {"Task": {"priority": "string"}}


@pytest.mark.asyncio
async def test_refine_sub_intents_returns_unchanged_when_no_sub_intents(monkeypatch):
    """A plan with no sub_intents should pass through unchanged."""
    mock_call = _mock_call_api_factory([{"content": "x", "tool_calls": None}])
    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_call)

    from backend.agent.intent_plan import IntentPlan

    plan = IntentPlan(kind=IntentKind.GRAPH_MUTATION, rationale="t", actions=[])
    out = await IntentRouter.refine_sub_intents(plan, RouterContext())
    assert out is plan
    assert out.actions == []


@pytest.mark.asyncio
async def test_route_multi_intent_tool_schema_includes_sub_intent(monkeypatch):
    """The tool schema surfaced to LLM #1 should include sub_intents[] entries."""

    seen_payload = {}

    async def mock_capture(provider, model, messages, tools=None):
        seen_payload["tools"] = tools
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "classify_intent",
                        "arguments": json.dumps({"kind": "converse", "rationale": "ok"}),
                    },
                }
            ],
        }

    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_capture)
    await IntentRouter.route("hi", RouterContext(), provider_name="openai", model_name="gpt-4")
    schema = seen_payload["tools"][0]["function"]["parameters"]
    assert "sub_intents" in schema["properties"]
    # multi_intent should be in the kind enum.
    assert "multi_intent" in schema["properties"]["kind"]["enum"]
    # Sub-intent kinds should include widget_extend_schema.
    sub_enum = schema["properties"]["sub_intents"]["items"]["properties"]["kind"]["enum"]
    assert "widget_extend_schema" in sub_enum
    assert "widget_fix_code" in sub_enum
    assert "widget_rewrite" in sub_enum
