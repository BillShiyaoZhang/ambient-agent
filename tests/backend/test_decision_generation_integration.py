"""Real router/projection/compiler integration with only external providers mocked."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import backend.agent.decisions as decisions_module
import backend.agent.router as router_module
from backend.agent.decisions import DecisionConfig
from backend.agent.errors import BudgetExhaustedError
from backend.agent.harness import AgentOrchestrator
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntent, SubIntentKind
from backend.agent.jev_router import JevDecisionClient, JevJSONClient, JevRouterConfig
from backend.agent.providers import ToolLoopBudget
from backend.agent.router import IntentRouter
from backend.router_context import GraphSnapshot, RouterContext


class AuditStore:
    def __init__(self):
        self.logs = []

    def add(self, record):
        self.logs.append(record)

    def commit(self):
        pass


def install_jev(monkeypatch, kind, *, target="none", probability=0.99, template=False):
    monkeypatch.setenv("TYPESAFE_API_KEY", "integration-test-key")
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        answers = {}
        for name, question in payload["questions"].items():
            if question["type"] == "choice":
                choice = kind if name == "intent" else target if name == "target_app" else "q_0"
                levels = question["criteria"]
                answers[name] = {
                    "type": "choice",
                    "choice": choice,
                    "confidence": 0.98,
                    "probabilities": {
                        key: probability if key == choice else (1 - probability) / (len(levels) - 1) for key in levels
                    },
                }
            else:
                answers[name] = {"type": "noul", "noul": 0.99 if template else 0.01}
        return httpx.Response(
            200, json={"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 10}}
        )

    transport = httpx.MockTransport(handler)
    routing_client, generic_client = JevDecisionClient(transport=transport), JevJSONClient(transport=transport)
    monkeypatch.setattr(router_module, "JevDecisionClient", lambda: routing_client)
    monkeypatch.setattr(decisions_module, "JevJSONClient", lambda: generic_client)
    return requests


def tool_response(name, parameters):
    return {
        "tool_calls": [{"function": {"name": name, "arguments": json.dumps(parameters)}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }


def install_generator(monkeypatch, *parameters):
    responses = [tool_response(name, value) for name, value in parameters]
    mock = AsyncMock(side_effect=responses)
    monkeypatch.setattr(router_module, "call_llm_api", mock)
    return mock


async def route(content, context=None, **kwargs):
    return await IntentRouter.route(
        content,
        context,
        provider_name="mock",
        model_name="generator",
        jev_config=JevRouterConfig(mode="cascade", context_version="routing-context-v2").snapshot(),
        decision_config=kwargs.pop("decision_config", DecisionConfig(mode="cascade").snapshot()),
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,parameters",
    [
        ("graph_query", {"query": {"type": "Task", "filters": {"done": False}}}),
        ("graph_mutation", {"actions": [{"action": "delete_node", "id": "task-1"}]}),
        ("widget_create", {"app_id": "new-clock", "instruction": "Build a clock"}),
        ("widget_modify", {"instruction": "Add a button"}),
        ("clarify", {"clarification_message": "Which task?", "clarification_options": [{"id": "one"}]}),
        (
            "multi_intent",
            {
                "sub_intents": [
                    {"kind": "graph_query", "query": {"type": "Task"}},
                    {"kind": "widget_modify", "app_id": "todo-main", "instruction": "Add a list"},
                ]
            },
        ),
        ("plan_and_act", {"sub_intents": [{"kind": "widget_create", "app_id": "new-clock", "instruction": "Build"}]}),
    ],
)
async def test_all_generated_routes_lock_kind_and_target(monkeypatch, kind, parameters):
    target = "app_0" if kind == "widget_modify" else "none"
    requests = install_jev(monkeypatch, kind, target=target)
    generator = install_generator(monkeypatch, ("generate_intent_parameters", parameters))
    context = RouterContext(app_manifests=[{"id": "todo-main", "title": "Todo", "code": "PRIVATE"}])
    db = AuditStore()
    plan = await route("请完成这个请求", context, db_session=db)
    assert plan.kind.value == kind
    if kind == "widget_modify":
        assert plan.app_id == "todo-main"
    assert len(requests) == 1
    generator.assert_awaited_once()
    schema = generator.call_args.args[3][0]["function"]["parameters"]
    assert not {"kind", "confidence", "rationale"} & set(schema["properties"])
    assert "PRIVATE" not in json.dumps(requests)
    audit = json.loads(next(log.response for log in db.logs if log.stage == "route_decision"))
    assert audit["routing"]["reason"] == "decision_plus_generation"
    assert len(audit["routing"]["generation_task"]["decision_hash"]) == 64
    assert {log.stage for log in db.logs} == {"route_decision", "intent_generate"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "graph_mutation", "query": {"type": "Task"}},
        {"query": {}},
        {"query": None},
        {"query": "DROP ALL"},
        {"query": {"type": "Task"}, "app_id": "changed-target"},
        {},
    ],
)
async def test_generation_drift_or_missing_parameters_reverts_to_full_router(monkeypatch, bad):
    install_jev(monkeypatch, "graph_query")
    generator = install_generator(
        monkeypatch,
        ("generate_intent_parameters", bad),
        ("classify_intent", {"kind": "clarify", "clarification_message": "Clarify safely"}),
    )
    db = AuditStore()
    plan = await route("复杂查询", db_session=db)
    assert plan.kind == IntentKind.CLARIFY
    assert generator.await_count == 2
    record = json.loads(next(log.response for log in db.logs if log.stage == "route_decision"))
    assert record["routing"]["generation_fallback"] == "decision_generation_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [None, True, 5, "invented_operation", ""])
async def test_invalid_generated_action_type_reverts_before_dispatch(monkeypatch, action):
    install_jev(monkeypatch, "graph_mutation")
    install_generator(
        monkeypatch,
        ("generate_intent_parameters", {"actions": [{"action": action}]}),
        ("classify_intent", {"kind": "clarify", "clarification_message": "Specify the change"}),
    )
    assert (await route("Change a task")).kind == IntentKind.CLARIFY


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [["todo-main"], ["new-app", "new-app"]])
async def test_composite_creation_cannot_overwrite_or_duplicate_app_ids(monkeypatch, ids):
    install_jev(monkeypatch, "multi_intent")
    install_generator(
        monkeypatch,
        (
            "generate_intent_parameters",
            {
                "sub_intents": [
                    {"kind": "widget_create", "app_id": app_id, "instruction": "Build new app"} for app_id in ids
                ]
            },
        ),
        ("classify_intent", {"kind": "clarify", "clarification_message": "Choose unique new App IDs"}),
    )
    context = RouterContext(app_manifests=[{"id": "todo-main"}])
    assert (await route("Build new Apps", context)).kind == IntentKind.CLARIFY


@pytest.mark.asyncio
async def test_exact_read_template_skips_generator_but_remains_query(monkeypatch):
    requests = install_jev(monkeypatch, "graph_query", template=True)
    generator = install_generator(monkeypatch)
    context = RouterContext(
        graph_snapshot=GraphSnapshot(
            schema_manifest=[{"id": "Task", "description": "Todo items"}],
            recent_nodes_by_type={"Task": [{"private": "DO_NOT_SEND"}]},
        )
    )
    plan = await route("列出所有任务", context)
    assert plan.query == {"type": "Task", "limit": 500}
    assert len(requests) == 2
    assert "DO_NOT_SEND" not in json.dumps(requests)
    generator.assert_not_awaited()


@pytest.mark.asyncio
async def test_complex_filtered_query_generates_after_template_abstains(monkeypatch):
    install_jev(monkeypatch, "graph_query", template=False)
    generator = install_generator(
        monkeypatch, ("generate_intent_parameters", {"query": {"type": "Task", "filters": {"done": False}}})
    )
    context = RouterContext(graph_snapshot=GraphSnapshot(schema_manifest=[{"id": "Task"}]))
    plan = await route("列出未完成任务", context)
    assert plan.query["filters"] == {"done": False}
    generator.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_mode", ["off", "shadow"])
async def test_expanded_cascade_requires_frozen_workflow_opt_in(monkeypatch, decision_mode):
    install_jev(monkeypatch, "graph_query")
    generator = install_generator(monkeypatch, ("classify_intent", {"kind": "graph_query", "query": {"type": "Task"}}))
    plan = await route("List tasks", decision_config=DecisionConfig(mode=decision_mode).snapshot())
    assert plan.kind == IntentKind.GRAPH_QUERY
    assert generator.call_args.args[3][0]["function"]["name"] == "classify_intent"


@pytest.mark.asyncio
async def test_stricter_workflow_threshold_is_observed(monkeypatch):
    install_jev(monkeypatch, "graph_query", probability=0.96)
    generator = install_generator(
        monkeypatch, ("classify_intent", {"kind": "clarify", "clarification_message": "Which?"})
    )
    plan = await route("List", decision_config=DecisionConfig(mode="cascade", min_probability=0.99).snapshot())
    assert plan.kind == IntentKind.CLARIFY
    generator.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["decision", "context"])
async def test_mutating_source_during_generation_invalidates_binding(monkeypatch, mutation):
    context = RouterContext()
    decision = {"kind": "graph_query"}
    from backend.agent.generation import GenerationContractError, IntentGenerationTask

    task = IntentGenerationTask(
        IntentKind.GRAPH_QUERY,
        "List tasks",
        None,
        IntentRouter._generation_source_hash(decision, context, "List tasks"),
        0.99,
    )

    async def generate(*args):
        if mutation == "decision":
            decision["kind"] = "graph_mutation"
        else:
            context.session_summary = "User changed the target"
        return tool_response("generate_intent_parameters", {"query": {"type": "Task"}})

    monkeypatch.setattr(router_module, "call_llm_api", generate)
    with pytest.raises(GenerationContractError, match="stale_generation_decision"):
        await IntentRouter._generate_intent_parameters(
            task, context, None, "mock", "model", "zh", None, None, None, decision_source=decision
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [asyncio.CancelledError, BudgetExhaustedError])
async def test_generation_cancellation_and_budget_propagate_without_fallback(monkeypatch, exception):
    install_jev(monkeypatch, "graph_query")
    generator = AsyncMock(side_effect=exception())
    monkeypatch.setattr(router_module, "call_llm_api", generator)
    with pytest.raises(exception):
        await route("List tasks")
    generator.assert_awaited_once()


@pytest.mark.asyncio
async def test_all_sequential_stages_charge_shared_model_and_token_budget(monkeypatch):
    install_jev(monkeypatch, "graph_query", template=False)
    install_generator(
        monkeypatch, ("generate_intent_parameters", {"query": {"type": "Task", "filters": {"done": False}}})
    )
    calls, usages = [], []
    budget = ToolLoopBudget(
        wall_clock_s=30, llm_call_timeout_s=10, on_model_call=lambda: calls.append(1), on_usage=usages.append
    )
    context = RouterContext(graph_snapshot=GraphSnapshot(schema_manifest=[{"id": "Task"}]))
    await route("List unfinished tasks", context, budget=budget)
    assert len(calls) == len(usages) == 3
    assert sum(u.get("input_tokens", u.get("prompt_tokens", 0)) for u in usages) == 220


@pytest.mark.asyncio
async def test_billable_router_response_is_audited_when_usage_exhausts_budget(monkeypatch):
    install_jev(monkeypatch, "graph_query")
    generator = install_generator(monkeypatch)
    db = AuditStore()

    def consume(usage):
        assert usage["input_tokens"] == 100
        raise BudgetExhaustedError("Token budget exhausted")

    budget = ToolLoopBudget(wall_clock_s=30, llm_call_timeout_s=10, on_usage=consume)
    with pytest.raises(BudgetExhaustedError):
        await route("List tasks", db_session=db, budget=budget)
    generator.assert_not_awaited()
    log = next(log for log in db.logs if log.stage == "route_decision")
    assert log.error == "budget_exhausted"
    assert log.usage["input_tokens"] == 100
    assert json.loads(log.response)["routing"]["selected_plan_kind"] is None


@pytest.mark.asyncio
async def test_billable_parameter_generation_is_audited_when_usage_exhausts_budget(monkeypatch):
    install_jev(monkeypatch, "graph_query")
    generator = install_generator(monkeypatch, ("generate_intent_parameters", {"query": {"type": "Task"}}))
    db = AuditStore()

    def consume(usage):
        if "prompt_tokens" in usage:
            raise BudgetExhaustedError("Generation token budget exhausted")

    budget = ToolLoopBudget(wall_clock_s=30, llm_call_timeout_s=10, on_usage=consume)
    with pytest.raises(BudgetExhaustedError):
        await route("List tasks", db_session=db, budget=budget)
    generator.assert_awaited_once()
    log = next(log for log in db.logs if log.stage == "intent_generate")
    assert log.usage["prompt_tokens"] == 20
    assert log.error == "intent_generation_failed"


@pytest.mark.asyncio
async def test_parameter_cascade_override_does_not_enable_other_stage_calls(monkeypatch):
    requests = install_jev(monkeypatch, "graph_query", template=True)
    generator = install_generator(monkeypatch, ("generate_intent_parameters", {"query": {"type": "Task"}}))
    config = DecisionConfig(mode="off", stage_modes={"intent_parameters": "cascade"})
    context = RouterContext(graph_snapshot=GraphSnapshot(schema_manifest=[{"id": "Task"}]))
    plan = await route("List tasks", context, decision_config=config.snapshot())
    assert plan.kind == IntentKind.GRAPH_QUERY
    assert len(requests) == 1
    generator.assert_awaited_once()


@pytest.mark.asyncio
async def test_parameter_off_override_retains_complete_legacy_routing(monkeypatch):
    install_jev(monkeypatch, "graph_query")
    generator = install_generator(
        monkeypatch, ("classify_intent", {"kind": "clarify", "clarification_message": "Which?"})
    )
    config = DecisionConfig(mode="cascade", stage_modes={"intent_parameters": "off"})
    assert (await route("List tasks", decision_config=config.snapshot())).kind == IntentKind.CLARIFY
    assert generator.call_args.args[3][0]["function"]["name"] == "classify_intent"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["order", "target", "kind", "drop", "invalid"])
async def test_cascade_refiner_cannot_reorder_retarget_or_drop_steps(monkeypatch, change):
    original = IntentPlan(
        kind=IntentKind.MULTI_INTENT,
        sub_intents=[
            SubIntent(kind=SubIntentKind.GRAPH_QUERY, query={"type": "Task"}),
            SubIntent(kind=SubIntentKind.WIDGET_MODIFY, app_id="todo-main", instruction="Add table"),
        ],
    )
    saved = copy.deepcopy(original.to_dict())
    refined = copy.deepcopy(saved)
    if change == "order":
        refined["sub_intents"].reverse()
    if change == "target":
        refined["sub_intents"][1]["app_id"] = "other-app"
    if change == "kind":
        refined["sub_intents"][1]["kind"] = "widget_create"
    if change == "drop":
        refined["sub_intents"].pop()
    if change == "invalid":
        refined["sub_intents"][0]["query"] = {}
    install_generator(monkeypatch, ("classify_intent", refined))
    # Empty content intentionally prevents semantic shortcut; real refiner/compile still run.
    result = await IntentRouter.refine_sub_intents(
        original,
        RouterContext(app_manifests=[{"id": "todo-main"}]),
        provider_name="mock",
        model_name="model",
        decision_config=DecisionConfig(mode="cascade").snapshot(),
    )
    assert result.to_dict() == saved


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed", [4, 11])
async def test_harness_refiner_uses_classification_remaining_deadline(monkeypatch, elapsed):
    clock = [100.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr("backend.agent.harness.time", fake_time)
    monkeypatch.setattr(decisions_module, "time", fake_time)
    plan = IntentPlan(
        kind=IntentKind.MULTI_INTENT, sub_intents=[SubIntent(kind=SubIntentKind.GRAPH_QUERY, query={"type": "Task"})]
    )

    async def slow_route(*args, **kwargs):
        clock[0] += elapsed
        return plan

    monkeypatch.setattr(IntentRouter, "route", slow_route)
    refiner = AsyncMock(return_value=plan)
    monkeypatch.setattr(IntentRouter, "refine_sub_intents", refiner)
    db = MagicMock()
    db.get_messages.return_value = []
    apps = MagicMock()
    apps.list_apps.return_value = []
    graph = MagicMock()
    graph.routing_snapshot.return_value = {}
    budget = ToolLoopBudget(wall_clock_s=10, llm_call_timeout_s=8)
    harness = AgentOrchestrator(db, apps, graph, tool_loop_budget=budget)
    if elapsed > 10:
        with pytest.raises(BudgetExhaustedError):
            await harness._classify_intent("List then edit", "session")
        refiner.assert_not_awaited()
    else:
        assert await harness._classify_intent("List then edit", "session") is plan
        passed = refiner.call_args.kwargs["budget"]
        assert passed.wall_clock_s == passed.llm_call_timeout_s == 6
