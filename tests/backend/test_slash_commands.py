import json
from unittest.mock import AsyncMock

import pytest

from backend.agent.intent_plan import IntentKind, SubIntentKind
from backend.agent.router import IntentRouter
from backend.agent.slash_commands import (
    build_slash_command_catalog,
    explicit_skill_ids,
    parse_slash_commands,
)
from backend.router_context import RouterContext


def test_parser_preserves_order_prefix_and_escaped_literal_command() -> None:
    parsed = parse_slash_commands(
        r"先读取上下文 /app planner 修复按钮并保留文字 \/query /ask 解释修改"
    )

    assert [(item.name, item.arguments) for item in parsed] == [
        ("ask", {"instruction": "先读取上下文"}),
        ("app", {"app_id": "planner", "instruction": "修复按钮并保留文字 /query"}),
        ("ask", {"instruction": "解释修改"}),
    ]


def test_multiple_explicit_skill_ids_are_extracted() -> None:
    assert explicit_skill_ids(
        "/skill daily-planning 安排今天 /skill review-notes 检查风险"
    ) == ["daily-planning", "review-notes"]


def test_catalog_returns_every_dynamic_id_choice() -> None:
    catalog = build_slash_command_catalog(
        apps=[
            {"id": "planner", "title": "Planner"},
            {"id": "calendar", "title": "Calendar"},
        ],
        skills=[
            {
                "catalog_id": "agent-skill:daily",
                "title": "Daily",
                "available": True,
                "status": "ready",
            },
            {
                "catalog_id": "agent-skill:review",
                "title": "Review",
                "available": False,
                "status": "unavailable",
            },
        ],
    )

    assert [item["name"] for item in catalog["commands"]] == [
        "ask",
        "app",
        "create",
        "query",
        "mutate",
        "skill",
    ]
    app_argument = next(
        command for command in catalog["commands"] if command["name"] == "app"
    )["arguments"][0]
    assert {option["value"] for option in app_argument["options"]} == {
        "planner",
        "calendar",
    }
    skill_argument = next(
        command for command in catalog["commands"] if command["name"] == "skill"
    )["arguments"][0]
    assert [option["value"] for option in skill_argument["options"]] == [
        "agent-skill:daily",
        "agent-skill:review",
    ]
    assert skill_argument["options"][1]["disabled"] is True


@pytest.mark.asyncio
async def test_direct_commands_compile_to_ordered_multi_intent_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = AsyncMock(side_effect=AssertionError("direct slash commands must not call the router model"))
    monkeypatch.setattr("backend.agent.router.call_llm_api", llm)

    plan = await IntentRouter.route(
        "/app planner 改成周视图 /ask 解释你的设计取舍",
        RouterContext(app_manifests=[{"id": "planner", "title": "Planner"}]),
    )

    assert plan.kind == IntentKind.MULTI_INTENT
    assert plan.rationale == "explicit slash command sequence"
    assert [item.kind for item in plan.sub_intents] == [
        SubIntentKind.WIDGET_MODIFY,
        SubIntentKind.CONVERSE,
    ]
    assert plan.sub_intents[0].app_id == "planner"
    assert plan.sub_intents[1].instruction == "解释你的设计取舍"
    llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_and_mutation_commands_are_constrained_then_compiled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = AsyncMock(
        side_effect=[
            {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "classify_intent",
                        "arguments": json.dumps({
                            "kind": "graph_query",
                            "query": {"type": "Task", "properties": {"status": "pending"}},
                        }),
                    },
                }],
            },
            {
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "classify_intent",
                        "arguments": json.dumps({
                            "kind": "graph_mutation",
                            "actions": [{
                                "action": "create_node",
                                "id": "task-1",
                                "type": "Task",
                                "properties": {"title": "ship"},
                            }],
                        }),
                    },
                }],
            },
        ]
    )
    monkeypatch.setattr("backend.agent.router.call_llm_api", llm)

    plan = await IntentRouter.route(
        "/query 列出未完成任务 /mutate 新建 ship 任务",
        RouterContext(),
        provider_name="fake",
        model_name="router",
    )

    assert plan.kind == IntentKind.MULTI_INTENT
    assert [item.kind for item in plan.sub_intents] == [
        SubIntentKind.GRAPH_QUERY,
        SubIntentKind.GRAPH_MUTATION,
    ]
    assert plan.sub_intents[0].query == {
        "type": "Task",
        "properties": {"status": "pending"},
    }
    assert plan.sub_intents[1].actions[0]["id"] == "task-1"
    assert llm.await_count == 2


@pytest.mark.asyncio
async def test_unknown_app_and_oversized_sequence_fail_closed_to_clarify() -> None:
    unknown = await IntentRouter.route(
        "/app missing 修复",
        RouterContext(app_manifests=[{"id": "planner", "title": "Planner"}]),
    )
    oversized = await IntentRouter.route(
        " ".join(f"/ask question-{index}" for index in range(9)),
        RouterContext(),
    )

    assert unknown.kind == IntentKind.CLARIFY
    assert unknown.clarification_options == [{"value": "planner", "label": "Planner"}]
    assert oversized.kind == IntentKind.CLARIFY
    assert "8" in oversized.clarification_message
