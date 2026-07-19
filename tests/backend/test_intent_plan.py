from backend.agent.intent_plan import IntentKind, IntentPlan


def test_intent_kind_enum_members():
    expected = {
        "widget_create",
        "widget_modify",
        "graph_mutation",
        "graph_query",
        "plan_and_act",
        "clarify",
        "converse",
    }
    actual = {k.value for k in IntentKind}
    assert expected.issubset(actual)


def test_intent_plan_defaults():
    p = IntentPlan(kind=IntentKind.CONVERSE)
    assert p.confidence == 0.0
    assert p.app_id is None
    assert p.instruction is None
    assert p.actions == []
    assert p.query is None
    assert p.clarification_options == []
    assert p.clarification_message == ""
    assert p.deprecated is False
    assert p.rationale == ""


def test_intent_plan_to_dict_minimal():
    p = IntentPlan(kind=IntentKind.GRAPH_MUTATION, rationale="user added a task")
    d = p.to_dict()
    assert d["kind"] == "graph_mutation"
    assert d["rationale"] == "user added a task"
    assert d["confidence"] == 0.0


def test_intent_plan_to_dict_full():
    p = IntentPlan(
        kind=IntentKind.WIDGET_MODIFY,
        confidence=0.92,
        rationale="modify todo",
        app_id="todo-app-1234",
        instruction="add delete button",
    )
    d = p.to_dict()
    assert d["kind"] == "widget_modify"
    assert d["app_id"] == "todo-app-1234"
    assert d["instruction"] == "add delete button"
    assert d["confidence"] == 0.92


def test_intent_plan_from_dict_roundtrip():
    src = {
        "kind": "graph_mutation",
        "confidence": 0.7,
        "rationale": "user wants to add a task",
        "actions": [{"action": "create_node", "id": "t1", "type": "Task", "properties": {"title": "x"}}],
    }
    p = IntentPlan.from_dict(src)
    assert p.kind == IntentKind.GRAPH_MUTATION
    assert p.confidence == 0.7
    assert len(p.actions) == 1


def test_intent_plan_from_dict_invalid_kind_fails_closed_to_clarify():
    src = {"kind": "not_a_real_kind", "rationale": "x"}
    p = IntentPlan.from_dict(src)
    assert p.kind == IntentKind.CLARIFY


def test_intent_plan_from_tool_call_args():
    # Simulate OpenAI-style tool call args dict
    args = {
        "kind": "graph_query",
        "confidence": 0.81,
        "rationale": "user asked what's on calendar",
        "query": {"type": "CalendarEvent"},
    }
    p = IntentPlan.from_tool_call_args(args)
    assert p.kind == IntentKind.GRAPH_QUERY
    assert p.query == {"type": "CalendarEvent"}


def test_intent_plan_clarify_payload():
    p = IntentPlan(
        kind=IntentKind.CLARIFY,
        rationale="multiple todo apps",
        clarification_message="Which todo?",
        clarification_options=[
            {"value": "todo-app-1", "label": "Work todo"},
            {"value": "todo-app-2", "label": "Personal todo"},
        ],
    )
    d = p.to_dict()
    assert d["clarification_options"][0]["value"] == "todo-app-1"


def test_get_tool_schema_present():
    schema = IntentPlan.tool_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "classify_intent"
    params = fn["parameters"]
    assert "kind" in params["properties"]
    assert set(params["properties"]["kind"]["enum"]) == {k.value for k in IntentKind}
    assert "kind" in params["required"]


def test_intent_plan_deprecated_flag_for_plan_and_act():
    p = IntentPlan(kind=IntentKind.PLAN_AND_ACT, deprecated=True)
    assert p.deprecated is True
