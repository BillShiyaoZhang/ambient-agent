import copy

import pytest
from jsonschema import Draft202012Validator

from backend.agent.decision_context import content_hash
from backend.agent.generation import GenerationContractError, IntentGenerationTask, validate_complete_plan
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntent, SubIntentKind

DECISION_HASH = "a" * 64


def _task(kind, *, target=None, instruction="根据这条指令完成请求"):
    return IntentGenerationTask(kind, instruction, target, DECISION_HASH, 0.98)


@pytest.mark.parametrize(
    "kind,fields,required",
    [
        (IntentKind.GRAPH_QUERY, {"query"}, {"query"}),
        (IntentKind.GRAPH_MUTATION, {"actions"}, {"actions"}),
        (IntentKind.WIDGET_CREATE, {"app_id", "instruction"}, {"app_id", "instruction"}),
        (IntentKind.WIDGET_MODIFY, {"instruction"}, {"instruction"}),
        (IntentKind.MULTI_INTENT, {"sub_intents"}, {"sub_intents"}),
        (IntentKind.PLAN_AND_ACT, {"sub_intents"}, {"sub_intents"}),
        (IntentKind.CLARIFY, {"clarification_message", "clarification_options"}, {"clarification_message"}),
        (IntentKind.CONVERSE, {"instruction"}, {"instruction"}),
    ],
)
def test_generation_schema_exposes_only_missing_parameters(kind, fields, required):
    schema = _task(kind, target="todo-main" if kind == IntentKind.WIDGET_MODIFY else None).tool_schema()
    assert schema["function"]["name"] == "generate_intent_parameters"
    parameters = schema["function"]["parameters"]
    assert set(parameters["properties"]) == fields
    assert set(parameters["required"]) == required
    assert parameters["additionalProperties"] is False
    assert not {"kind", "confidence", "rationale", "decision_hash"} & set(parameters["properties"])
    Draft202012Validator.check_schema(parameters)


def test_fixed_new_app_target_has_a_satisfiable_schema_and_remains_fixed():
    task = _task(IntentKind.WIDGET_CREATE, target="weather-new")
    schema = task.tool_schema()["function"]["parameters"]
    assert "app_id" not in schema["properties"]
    assert set(schema["required"]).issubset(schema["properties"])
    Draft202012Validator(schema).validate({"instruction": "创建天气组件"})
    plan = task.compile({"instruction": "创建天气组件"}, current_decision_hash=DECISION_HASH)
    assert plan.app_id == "weather-new"


@pytest.mark.parametrize(
    "parameters",
    [
        {"query": {"type": "Task"}, "kind": "graph_mutation"},
        {"query": {"type": "Task"}, "confidence": 1.0},
        {"query": {"type": "Task"}, "rationale": "choose another route"},
        {"query": {"type": "Task"}, "app_id": "different-app"},
        {"query": {"type": "Task"}, "actions": [{"action": "delete_node", "id": "t"}]},
    ],
)
def test_generation_cannot_change_fixed_decision_fields(parameters):
    with pytest.raises(GenerationContractError):
        _task(IntentKind.GRAPH_QUERY).compile(parameters, current_decision_hash=DECISION_HASH)


def test_existing_app_cannot_be_retargeted_by_generation():
    task = _task(IntentKind.WIDGET_MODIFY, target="todo-main")
    with pytest.raises(GenerationContractError):
        task.compile({"instruction": "增加按钮", "app_id": "calendar-main"}, current_decision_hash=DECISION_HASH)
    plan = task.compile({"instruction": "增加按钮"}, current_decision_hash=DECISION_HASH)
    assert plan.kind == IntentKind.WIDGET_MODIFY
    assert plan.app_id == "todo-main"
    assert plan.confidence == 0.98


def test_stale_generation_is_rejected_before_compilation():
    task = _task(IntentKind.GRAPH_QUERY)
    with pytest.raises(GenerationContractError, match="stale_generation_decision"):
        task.compile({"query": {"type": "Task"}}, current_decision_hash="b" * 64)
    assert task.binding()["decision_hash"] == DECISION_HASH
    assert task.binding()["input_hash"] == content_hash(task.instruction)
    assert task.binding()["purpose"] == "intent_parameters"


@pytest.mark.parametrize("kind", list(IntentKind))
def test_missing_required_generation_parameters_are_rejected(kind):
    target = "todo-main" if kind == IntentKind.WIDGET_MODIFY else None
    with pytest.raises(ValueError):
        _task(kind, target=target).compile({}, current_decision_hash=DECISION_HASH)


@pytest.mark.parametrize(
    "kind,parameters",
    [
        (IntentKind.GRAPH_QUERY, {"query": None}),
        (IntentKind.GRAPH_QUERY, {"query": {}}),
        (IntentKind.GRAPH_MUTATION, {"actions": []}),
        (IntentKind.GRAPH_MUTATION, {"actions": ["delete everything"]}),
        (IntentKind.WIDGET_CREATE, {"app_id": "invalid/path", "instruction": "创建"}),
        (IntentKind.WIDGET_MODIFY, {"instruction": " "}),
        (IntentKind.CONVERSE, {"instruction": " "}),
        (IntentKind.CLARIFY, {"clarification_message": " "}),
    ],
)
def test_generated_parameters_must_form_a_complete_plan(kind, parameters):
    target = "todo-main" if kind == IntentKind.WIDGET_MODIFY else None
    with pytest.raises(ValueError):
        _task(kind, target=target).compile(parameters, current_decision_hash=DECISION_HASH)


@pytest.mark.parametrize("kind", [IntentKind.MULTI_INTENT, IntentKind.PLAN_AND_ACT])
@pytest.mark.parametrize("steps", [[], [{"kind": "graph_query", "query": {"type": "Task"}}] * 33])
def test_compound_empty_and_excessive_steps_are_rejected(kind, steps):
    with pytest.raises(ValueError):
        _task(kind).compile({"sub_intents": steps}, current_decision_hash=DECISION_HASH)


@pytest.mark.parametrize(
    "steps",
    [
        [{"kind": "unknown_action", "instruction": "something"}],
        [{"kind": "graph_query", "query": {"type": "Task"}}, "second requested action"],
        [{"actions": [{"action": "delete_node", "id": "task-1"}]}],
        [{"kind": "graph_query", "query": {"type": "Task"}, "unrecognized_permission": True}],
    ],
)
def test_compound_unknown_or_malformed_steps_are_rejected_without_dropping_actions(steps):
    with pytest.raises(ValueError):
        _task(IntentKind.MULTI_INTENT).compile({"sub_intents": steps}, current_decision_hash=DECISION_HASH)


def test_compound_success_preserves_order_actions_and_original_targets():
    steps = [
        {"kind": "graph_query", "query": {"type": "Task", "properties": {"status": "pending"}}},
        {
            "kind": "graph_mutation",
            "actions": [{"action": "update_node_property", "id": "task-1", "properties": {"status": "done"}}],
        },
        {"kind": "widget_modify", "app_id": "todo-main", "instruction": "显示完成状态"},
    ]
    original = copy.deepcopy(steps)
    plan = _task(IntentKind.MULTI_INTENT).compile({"sub_intents": steps}, current_decision_hash=DECISION_HASH)
    assert [step.kind for step in plan.sub_intents] == [
        SubIntentKind.GRAPH_QUERY,
        SubIntentKind.GRAPH_MUTATION,
        SubIntentKind.WIDGET_MODIFY,
    ]
    assert plan.sub_intents[1].actions == original[1]["actions"]
    assert plan.sub_intents[2].app_id == "todo-main"
    assert steps == original


def test_schema_extension_must_include_properties_and_a_valid_app_instruction():
    plan = IntentPlan(
        kind=IntentKind.MULTI_INTENT,
        sub_intents=[SubIntent(kind=SubIntentKind.WIDGET_EXTEND_SCHEMA, app_id="todo-main", instruction="显示优先级")],
    )
    with pytest.raises(GenerationContractError, match="schema_extension_missing"):
        validate_complete_plan(plan)
    plan.sub_intents[0].extend_schema_props = {"Task": {"priority": "string"}}
    validate_complete_plan(plan)


def test_clarification_options_must_match_the_declared_object_schema():
    with pytest.raises(ValueError):
        _task(IntentKind.CLARIFY).compile(
            {"clarification_message": "请选择一个应用", "clarification_options": ["free-form option"]},
            current_decision_hash=DECISION_HASH,
        )


def test_generated_schema_extension_properties_cannot_be_free_text():
    with pytest.raises(ValueError):
        _task(IntentKind.MULTI_INTENT).compile(
            {
                "sub_intents": [
                    {
                        "kind": "widget_extend_schema",
                        "app_id": "todo-main",
                        "instruction": "增加优先级",
                        "extend_schema_props": "Task priority string",
                    }
                ]
            },
            current_decision_hash=DECISION_HASH,
        )
