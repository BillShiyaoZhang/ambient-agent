import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import backend.agent.router as router_module
from backend.agent.errors import BudgetExhaustedError
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.jev_router import JevDecisionClient, JevRouterConfig, JevRouterError
from backend.agent.router import IntentRouter
from backend.agent.providers import ToolLoopBudget
from backend.router_context import RouterContext


class AuditStore:
    def __init__(self):
        self.logs = []

    def add(self, record):
        self.logs.append(record)

    def commit(self):
        pass


def install_jev(monkeypatch, kind="converse", probability=0.99, target="none", status=200):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-never-persist")
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        options = payload["questions"]["intent"]["criteria"]
        probabilities = {key: probability if key == kind else (1 - probability) / 7 for key in options}
        answers = {
            "intent": {
                "type": "choice",
                "choice": kind,
                "confidence": (probability - 1 / 8) / (1 - 1 / 8),
                "probabilities": probabilities,
            }
        }
        if "target_app" in payload["questions"]:
            options = payload["questions"]["target_app"]["criteria"]
            answers["target_app"] = {
                "type": "choice",
                "choice": target,
                "confidence": 0.98,
                "probabilities": {key: 1.0 if key == target else 0.0 for key in options},
            }
        return httpx.Response(
            status,
            json={"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 10}},
        )

    client = JevDecisionClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("backend.agent.router.JevDecisionClient", lambda: client)
    return requests


def install_legacy(monkeypatch, kind="converse", **fields):
    result = {
        "tool_calls": [{"function": {"name": "classify_intent", "arguments": json.dumps({"kind": kind, **fields})}}],
        "usage": {"prompt_tokens": 31, "completion_tokens": 12},
    }
    mocked = AsyncMock(return_value=result)
    monkeypatch.setattr("backend.agent.router.call_llm_api", mocked)
    return mocked


async def route(mode, *, content="Explain knowledge graphs", context=None, db=None, **kwargs):
    return await IntentRouter.route(
        content,
        context,
        provider_name="mock",
        model_name="generation-model",
        db_session=db,
        jev_config=JevRouterConfig(mode=mode).snapshot(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_shadow_retains_generated_actions_and_compares_kind(monkeypatch):
    requests = install_jev(monkeypatch, "graph_query")
    actions = [{"action": "delete_node", "id": "task-1"}]
    legacy = install_legacy(monkeypatch, "graph_mutation", actions=actions)
    db = AuditStore()
    plan = await route("shadow", content="Delete task-1", db=db)
    assert plan.kind == IntentKind.GRAPH_MUTATION
    assert plan.actions == actions
    assert len(requests) == 1
    legacy.assert_awaited_once()
    log = next(log for log in db.logs if log.stage == "route_decision")
    recorded = json.loads(log.response)
    assert recorded["routing"]["reason"] == "shadow_mode"
    assert recorded["routing"]["kind_agreement"] is False
    assert recorded["routing"]["selected_plan_kind"] == "graph_mutation"
    assert log.usage["input_tokens"] == 100
    assert next(log for log in db.logs if log.stage == "route").usage["prompt_tokens"] == 31
    assert "test-key-never-persist" not in json.dumps([log.model_dump(mode="json") for log in db.logs])


@pytest.mark.asyncio
async def test_cascade_high_probability_converse_is_complete_and_skips_generation(monkeypatch):
    install_jev(monkeypatch)
    legacy = install_legacy(monkeypatch)
    original = "解释一下 Graph 数据模型，先不要修改任何数据。"
    plan = await route("cascade", content=original)
    assert plan.kind == IntentKind.CONVERSE
    assert plan.instruction == original
    assert plan.confidence == 0.99
    assert plan.rationale
    legacy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,fields",
    [
        ("graph_query", {"query": {"type": "Task"}}),
        ("graph_mutation", {"actions": [{"action": "delete_node", "id": "task-1"}]}),
        ("widget_modify", {"app_id": "todo-main", "instruction": "Add a button"}),
        ("widget_create", {"app_id": "new-widget", "instruction": "Build a clock"}),
        ("multi_intent", {"sub_intents": [{"kind": "graph_query", "query": {"type": "Task"}}]}),
        (
            "plan_and_act",
            {"sub_intents": [{"kind": "graph_mutation", "actions": [{"action": "delete_node", "id": "t"}]}]},
        ),
        ("clarify", {"clarification_message": "Which app?"}),
    ],
)
async def test_cascade_never_dispatches_label_only_effect_or_query(monkeypatch, kind, fields):
    install_jev(monkeypatch, kind)
    legacy = install_legacy(monkeypatch, kind, **fields)
    plan = await route("cascade")
    legacy.assert_awaited_once()
    assert plan.kind.value == kind
    for key, value in fields.items():
        assert plan.to_dict()[key] == value


@pytest.mark.asyncio
async def test_uncertain_intent_falls_back_and_records_gate(monkeypatch):
    install_jev(monkeypatch, probability=0.65)
    legacy = install_legacy(monkeypatch, "clarify", clarification_message="Which one?")
    db = AuditStore()
    plan = await route("cascade", db=db)
    assert plan.kind == IntentKind.CLARIFY
    legacy.assert_awaited_once()
    assert json.loads(db.logs[-1].response)["routing"]["reason"] == "intent_uncertain"


@pytest.mark.asyncio
async def test_converse_with_conflicting_app_target_falls_back(monkeypatch):
    install_jev(monkeypatch, target="app_0")
    legacy = install_legacy(monkeypatch, "widget_modify", app_id="todo-main")
    db = AuditStore()
    context = RouterContext(app_manifests=[{"id": "todo-main", "title": "Todos"}])
    plan = await route("cascade", context=context, db=db)
    assert plan.app_id == "todo-main"
    legacy.assert_awaited_once()
    assert json.loads(db.logs[-1].response)["routing"]["reason"] == "target_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 529])
async def test_provider_failures_fall_back_without_credential_leaks(monkeypatch, status):
    install_jev(monkeypatch, status=status)
    legacy = install_legacy(monkeypatch, "graph_query", query={"type": "Task"})
    db = AuditStore()
    plan = await route("cascade", db=db)
    assert plan.query == {"type": "Task"}
    legacy.assert_awaited_once()
    decision_log = db.logs[-1]
    assert decision_log.error.startswith("jev_")
    assert "test-key-never-persist" not in decision_log.model_dump_json()


@pytest.mark.asyncio
async def test_missing_key_preserves_legacy_route(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    legacy = install_legacy(monkeypatch)
    db = AuditStore()
    await route("shadow", db=db)
    legacy.assert_awaited_once()
    assert db.logs[-1].error == "jev_key_missing"


@pytest.mark.asyncio
async def test_invalid_decision_audits_known_usage_and_falls_back(monkeypatch):
    failure = JevRouterError(
        "jev_response_invalid",
        model="jev-1.13.0",
        usage={"input_tokens": 100, "output_tokens": 10, "untrusted": "secret"},
    )
    mocked = AsyncMock(side_effect=failure)
    monkeypatch.setattr("backend.agent.router.JevDecisionClient", lambda: type("Client", (), {"decide": mocked})())
    legacy = install_legacy(monkeypatch)
    db = AuditStore()
    await route("shadow", db=db)
    legacy.assert_awaited_once()
    log = db.logs[-1]
    assert log.error == "jev_response_invalid"
    assert log.model == "jev-1.13.0"
    assert log.usage["input_tokens"] == 100
    assert log.usage["total_tokens"] == 110
    assert "secret" not in log.model_dump_json()


@pytest.mark.asyncio
async def test_off_and_explicit_commands_never_call_decision_model(monkeypatch):
    mocked = AsyncMock(side_effect=AssertionError("Jev must not be called"))
    monkeypatch.setattr("backend.agent.router.JevDecisionClient", lambda: type("Client", (), {"decide": mocked})())
    legacy = install_legacy(monkeypatch)
    await route("off")
    assert (await route("cascade", content="/ask Explain graphs")).kind == IntentKind.CONVERSE
    assert (await route("cascade", content="/app todo-main add a button")).app_id == "todo-main"
    mocked.assert_not_awaited()
    legacy.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [asyncio.CancelledError(), BudgetExhaustedError("budget exceeded")])
async def test_cancellation_and_budget_exhaustion_are_not_degraded(monkeypatch, failure):
    mocked = AsyncMock(side_effect=failure)
    monkeypatch.setattr("backend.agent.router.JevDecisionClient", lambda: type("Client", (), {"decide": mocked})())
    legacy = install_legacy(monkeypatch)
    with pytest.raises(type(failure)):
        await route("cascade")
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_large_state_is_not_silently_truncated(monkeypatch):
    install_jev(monkeypatch)
    legacy = install_legacy(monkeypatch)
    db = AuditStore()
    await route("cascade", content="data " * 12000, db=db)
    legacy.assert_awaited_once()
    assert db.logs[-1].error == "jev_state_too_large"


@pytest.mark.asyncio
async def test_two_stage_router_passes_remaining_wall_budget_and_preserves_callbacks(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("backend.agent.router.time", SimpleNamespace(monotonic=lambda: clock[0]))
    install_jev(monkeypatch)
    model_calls, usage = MagicMock(), MagicMock()
    budget = ToolLoopBudget(wall_clock_s=10.0, llm_call_timeout_s=9.0, on_model_call=model_calls, on_usage=usage)
    client = router_module.JevDecisionClient()
    original_decide = client.decide

    async def elapsed_decide(*args, **kwargs):
        result = await original_decide(*args, **kwargs)
        clock[0] += 6.0
        return result

    monkeypatch.setattr(client, "decide", elapsed_decide)
    legacy = AsyncMock(return_value=IntentPlan(kind=IntentKind.CONVERSE, instruction="hello"))
    monkeypatch.setattr(IntentRouter, "_route_legacy", legacy)

    await route("shadow", budget=budget)

    remaining = legacy.await_args.args[-2]
    assert remaining.wall_clock_s == 4.0
    assert remaining.llm_call_timeout_s == 4.0
    assert remaining.on_model_call is model_calls
    assert remaining.on_usage is usage
    assert budget.wall_clock_s == 10.0
    assert budget.llm_call_timeout_s == 9.0
    model_calls.assert_called_once()
    usage.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,override", [("off", None), ("shadow", "custom routing rubric")])
async def test_existing_routing_paths_keep_the_original_budget(monkeypatch, mode, override):
    legacy = AsyncMock(return_value=IntentPlan(kind=IntentKind.CONVERSE, instruction="hello"))
    monkeypatch.setattr(IntentRouter, "_route_legacy", legacy)
    budget = ToolLoopBudget(wall_clock_s=10.0)

    await route(mode, budget=budget, override_system_prompt=override)

    assert legacy.await_args.args[-2] is budget


@pytest.mark.asyncio
async def test_spent_jev_wall_budget_prevents_fallback_and_keeps_decision_audit(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("backend.agent.router.time", SimpleNamespace(monotonic=lambda: clock[0]))
    install_jev(monkeypatch)
    client = router_module.JevDecisionClient()
    original_decide = client.decide

    async def elapsed_decide(*args, **kwargs):
        result = await original_decide(*args, **kwargs)
        clock[0] += 10.0
        return result

    monkeypatch.setattr(client, "decide", elapsed_decide)
    legacy = AsyncMock()
    monkeypatch.setattr(IntentRouter, "_route_legacy", legacy)
    db = AuditStore()

    with pytest.raises(BudgetExhaustedError, match="wall-clock budget"):
        await route("shadow", budget=ToolLoopBudget(wall_clock_s=10.0), db=db)

    legacy.assert_not_awaited()
    assert len(db.logs) == 1
    recorded = json.loads(db.logs[0].response)
    assert recorded["routing"]["selected_plan_kind"] is None
    assert recorded["answers"]["intent"]["choice"] == "converse"
    assert db.logs[0].usage["input_tokens"] == 100


@pytest.mark.asyncio
async def test_fallback_plan_after_shared_deadline_is_rejected(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("backend.agent.router.time", SimpleNamespace(monotonic=lambda: clock[0]))
    install_jev(monkeypatch)

    async def expired_fallback(*args):
        clock[0] += 10.0
        return IntentPlan(kind=IntentKind.CONVERSE, rationale="fallback heuristic")

    monkeypatch.setattr(IntentRouter, "_route_legacy", expired_fallback)
    db = AuditStore()

    with pytest.raises(BudgetExhaustedError, match="wall-clock budget"):
        await route("shadow", budget=ToolLoopBudget(wall_clock_s=10.0), db=db)

    assert len(db.logs) == 1
    assert json.loads(db.logs[0].response)["routing"]["selected_plan_kind"] is None


@pytest.mark.asyncio
async def test_fallback_wall_timeout_propagates_budget_exhaustion_and_audits_decision(monkeypatch):
    install_jev(monkeypatch)
    legacy_started = asyncio.Event()
    legacy_cancelled = asyncio.Event()

    async def never_finishes(*args):
        legacy_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            legacy_cancelled.set()

    monkeypatch.setattr(IntentRouter, "_route_legacy", never_finishes)
    db = AuditStore()

    with pytest.raises(BudgetExhaustedError, match="wall-clock budget"):
        await route("shadow", budget=ToolLoopBudget(wall_clock_s=0.05), db=db)

    assert legacy_started.is_set()
    assert legacy_cancelled.is_set()
    assert len(db.logs) == 1
    assert json.loads(db.logs[0].response)["routing"]["selected_plan_kind"] is None
