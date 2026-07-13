"""Test scenarios for routing experiments.

Each scenario is a tuple of (user_message, expected_kind, expected_app_id, RouterContext,
notes). The RouterContext is fixed per scenario so that A/B variants see identical state.

OFAT phase 4 adds multi-intent scenarios (S21-S24) to test the new ``multi_intent``
routing path.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.agent.intent_plan import IntentKind
from backend.router_context import GraphSnapshot, RouterContext


@dataclass
class Scenario:
    """A single routing test case."""

    id: str
    user_message: str
    expected_kind: IntentKind
    expected_app_id: str | None  # None = any (or generate new)
    context: RouterContext
    notes: str = ""
    ambiguous: bool = False  # True for ambiguous cases (S04/S05/S08/S17)
    # For multi_intent scenarios: list of expected sub_intent kinds (in order).
    expected_sub_kinds: list[str] | None = None


# ── Standard fixtures reused across many scenarios ────────────────────────

_EMPTY_CTX = RouterContext()

_RICH_CTX = RouterContext(
    app_manifests=[
        {"id": "clock-app-abcd", "title": "My Clock"},
        {"id": "todo-app-efgh", "title": "My Todo List"},
        {"id": "calendar-app-ijkl", "title": "Calendar View"},
    ],
    graph_snapshot=GraphSnapshot(
        type_counts={"Task": 5, "CalendarEvent": 3, "Note": 2},
        recent_nodes_by_type={
            "Task": [
                {"id": "task-1", "type": "Task", "properties": {"title": "buy milk", "status": "pending"}},
                {"id": "task-2", "type": "Task", "properties": {"title": "call dentist", "status": "done"}},
            ],
            "CalendarEvent": [
                {"id": "evt-1", "type": "CalendarEvent", "properties": {"title": "standup", "when": "2026-07-12T09:00"}},
            ],
        },
        node_count=10,
        edge_count=4,
        schema_manifest=[
            {"id": "Task", "name": "Task", "description": "A todo item",
             "properties": {"title": "string", "status": "string"}},
            {"id": "CalendarEvent", "name": "CalendarEvent", "description": "An event",
             "properties": {"title": "string", "when": "string"}},
        ],
    ),
    session_recent=[
        {"role": "user", "content": "show me my widgets"},
        {"role": "agent", "content": "You have clock, todo, calendar."},
    ],
)

_NO_GRAPH_CTX = RouterContext(
    app_manifests=[
        {"id": "clock-app-abcd", "title": "My Clock"},
        {"id": "todo-app-efgh", "title": "My Todo List"},
    ],
    graph_snapshot=GraphSnapshot(node_count=0, edge_count=0),
)

_CLEAN_CTX = RouterContext(
    graph_snapshot=GraphSnapshot(node_count=0, edge_count=0),
)


# ── Scenario bank ─────────────────────────────────────────────────────────

SCENARIOS: list[Scenario] = [
    # Chinese creation (no existing widget)
    Scenario("S01", "建一个时钟", IntentKind.WIDGET_CREATE, "clock-app-", _CLEAN_CTX,
             "no clock widget yet, no graph data"),
    Scenario("S02", "做一个待办", IntentKind.WIDGET_CREATE, "todo-app-", _CLEAN_CTX,
             "no todo widget yet"),
    Scenario("S03", "帮我创建一个天气小程序", IntentKind.WIDGET_CREATE, "weather-app-", _CLEAN_CTX,
             "weather widget does not exist"),

    # Chinese creation (existing widget — ambiguous under C4 strict)
    Scenario("S04", "建一个时钟", IntentKind.WIDGET_MODIFY, "clock-app-abcd", _RICH_CTX,
             "AMBIGUOUS: clock already exists; strict rule says modify", ambiguous=True),
    Scenario("S05", "重新做一个待办", IntentKind.WIDGET_MODIFY, "todo-app-efgh", _RICH_CTX,
             "AMBUGUOUS: todo already exists; user says '重新做'", ambiguous=True),

    # Chinese modification (existing widget)
    Scenario("S06", "改下待办加删除按钮", IntentKind.WIDGET_MODIFY, "todo-app-efgh", _RICH_CTX,
             "modification with keyword '改下'"),
    Scenario("S07", "把 clock-app-1234 改成玻璃拟态", IntentKind.WIDGET_MODIFY, "clock-app-1234", _RICH_CTX,
             "explicit id reference (note: 1234 not in inventory, should be treated as modify)"),

    # Chinese graph
    Scenario("S08", "在待办里加买牛奶", IntentKind.GRAPH_MUTATION, None, _RICH_CTX,
             "AMBIGUOUS: pure data op — create Task node; widget subscribes via ambient.graph.subscribe",
             ambiguous=True),
    Scenario("S09", "待办里有几条", IntentKind.GRAPH_QUERY, None, _RICH_CTX,
             "querying count"),
    Scenario("S10", "今天有什么日程", IntentKind.GRAPH_QUERY, None, _RICH_CTX,
             "querying events"),

    # Chinese converse
    Scenario("S11", "你好", IntentKind.CONVERSE, None, _EMPTY_CTX),
    Scenario("S12", "你是谁", IntentKind.CONVERSE, None, _EMPTY_CTX),

    # English creation
    Scenario("S13", "build me a clock app", IntentKind.WIDGET_CREATE, "clock-app-", _CLEAN_CTX),
    Scenario("S14", "create a calculator", IntentKind.WIDGET_CREATE, "calculator-app-", _CLEAN_CTX),

    # English modification
    Scenario("S15", "Make clock-app-abcd look glassmorphic", IntentKind.WIDGET_MODIFY, "clock-app-abcd", _RICH_CTX),
    Scenario("S16", "add delete button to todo-app-efgh", IntentKind.WIDGET_MODIFY, "todo-app-efgh", _RICH_CTX),

    # English graph
    Scenario("S17", "add buy milk to todos", IntentKind.GRAPH_MUTATION, None, _RICH_CTX,
             "AMBIGUOUS: graph_mutation vs widget_modify (todo widget exists)", ambiguous=True),
    Scenario("S18", "what's on my calendar today", IntentKind.GRAPH_QUERY, None, _RICH_CTX),

    # English converse
    Scenario("S19", "hi", IntentKind.CONVERSE, None, _EMPTY_CTX),
    Scenario("S20", "tell me a joke", IntentKind.CONVERSE, None, _EMPTY_CTX),

    # ── OFAT phase 4: multi-intent scenarios (NEW) ───────────────────────
    # S21: a clear multi-intent: add an event AND make the calendar widget show it.
    Scenario(
        "S21",
        "在日历里加一个明天下午 3 点的会议并让日历 widget 显示",
        IntentKind.MULTI_INTENT,
        None,
        _RICH_CTX,
        "AMBIGUOUS: graph_mutation + widget_extend_schema (calendar widget exists)",
        ambiguous=True,
        expected_sub_kinds=["graph_mutation", "widget_extend_schema"],
    ),
    # S22: graph_mutation with multiple actions is still graph_mutation.
    Scenario(
        "S22",
        "加一个买鸡蛋的任务并把任务标为 pending",
        IntentKind.GRAPH_MUTATION,
        None,
        _RICH_CTX,
        "single graph_mutation with multiple actions (create_node sets status)",
    ),
    # S23: English multi-intent: extend schema + add data.
    Scenario(
        "S23",
        "add a buy eggs Task node and also extend Task schema with priority field",
        IntentKind.MULTI_INTENT,
        None,
        _RICH_CTX,
        "multi_intent with graph_mutation + widget_extend_schema",
        ambiguous=True,
        expected_sub_kinds=["graph_mutation", "widget_extend_schema"],
    ),
    # S24: ambiguous — could be plain widget_modify OR multi_intent.
    Scenario(
        "S24",
        "把日历改成显示分类颜色",
        IntentKind.MULTI_INTENT,
        "calendar-app-ijkl",
        _RICH_CTX,
        "AMBIGUOUS: user wants widget to show new field; multi_intent preferred if schema extension is needed",
        ambiguous=True,
        expected_sub_kinds=["widget_extend_schema"],
    ),
]


def get_scenario(scenario_id: str) -> Scenario:
    """Look up a scenario by id."""
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    raise KeyError(f"Unknown scenario: {scenario_id}")


def get_ambiguous_scenarios() -> list[Scenario]:
    return [s for s in SCENARIOS if s.ambiguous]
