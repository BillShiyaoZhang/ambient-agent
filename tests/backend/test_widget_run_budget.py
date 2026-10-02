"""Development can outlive routing's turn quota without weakening verification."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.errors import BudgetExhaustedError
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.providers import CloudLLMProvider, ToolLoopBudget
from backend.capabilities.models import RuntimeContract
from backend.coding_agent_acp import CodingAgentStagedResult
from backend.graph_db import GraphDatabase
from backend.run_service import AgentRunState, Continue, Failed, RunBudget, RunCoordinator, RunStore, Wait
from backend.schema_verification import SchemaVerificationService


MODEL_SNAPSHOT = {
    "primary": {"provider_id": "fake", "model_id": "scripted"},
    "fast": {"provider_id": "fake", "model_id": "scripted"},
}


@pytest.fixture
def development(tmp_path: Path) -> tuple[DurableAgentWorkflow, RunStore]:
    store = RunStore(str(tmp_path))
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=store,
        app_manager=SimpleNamespace(apps_dir=tmp_path / "apps", list_apps=lambda: []),
        graph_db=GraphDatabase(str(tmp_path)),
        llm_config_store=SimpleNamespace(resolve=lambda _selection: None),
        coding_agent_runner=AsyncMock(),
    )
    return workflow, store


def _state(phase: str, *, kind: IntentKind = IntentKind.WIDGET_CREATE, **kwargs: Any) -> AgentRunState:
    return AgentRunState(
        phase=phase,
        workflow_type=kind.value,
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id="budget-session",
        model_snapshot=MODEL_SNAPSHOT,
        intent=IntentPlan(kind=kind, app_id="budget-app", instruction="Build an App").to_dict(),
        **kwargs,
    )


def _run(store: RunStore, state: AgentRunState) -> dict[str, Any]:
    return store.create_run(
        owner_id="session:budget-session",
        action_id="chat",
        action_title="Agent task",
        source_type="chat",
        source_id=state.session_id,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "Build an App"},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )


def _draft(workflow: DurableAgentWorkflow, source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    staging = workflow.app_manager.apps_dir / f".budget-app.staging-{'d' * 32}"
    staging.mkdir(parents=True)
    (staging / "controller.js").write_text(source, encoding="utf-8")
    contract = RuntimeContract.create(app_id="budget-app", schemas=[], capabilities=[]).to_dict()
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": "budget-app",
                "title": "Budget App",
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": [],
                "capabilities": [],
            }
        ),
        encoding="utf-8",
    )
    return contract, {
        "app_id": "budget-app",
        "output": "generated draft",
        "staging_dir": str(staging),
        "live_dir": str(workflow.app_manager.apps_dir / "budget-app"),
    }


def test_unlimited_model_turn_budget_round_trips_without_dropping_other_limits() -> None:
    state = _state("plan", budget=RunBudget(max_model_turns=None, model_turns=12))
    restored = AgentRunState.model_validate_json(state.model_dump_json())
    assert restored.budget.max_model_turns is None
    assert restored.budget.model_turns == 12
    assert restored.budget.max_tokens == 64_000
    assert restored.budget.max_cost_usd == 5.0
    assert restored.budget.max_wall_seconds == 600.0
    assert RunBudget().max_model_turns == 8


@pytest.mark.parametrize(
    "current_limit, window, explicit, expected_limit",
    [(8, None, False, None), (4, None, True, 4), (13, 8, False, None), (9, 4, True, 9)],
)
def test_legacy_limit_migration_uses_original_retry_allowance(
    current_limit: int, window: int | None, explicit: bool, expected_limit: int | None
) -> None:
    raw = _state("plan").model_dump(mode="json")
    raw["budget"]["max_model_turns"] = current_limit
    raw["budget"].pop("model_turn_limit_explicit", None)
    if window is not None:
        raw["data"]["retry_budget_window"] = {"model_turns": window}
    state = AgentRunState.model_validate(raw)
    state.apply_development_budget_policy()
    assert state.budget.model_turn_limit_explicit is explicit
    assert state.budget.max_model_turns == expected_limit


def test_future_explicit_eight_turn_limit_survives_recovery() -> None:
    state = _state("plan", budget=RunBudget(max_model_turns=8, model_turns=8))
    restored = AgentRunState.model_validate_json(state.model_dump_json())
    restored.apply_development_budget_policy()
    assert restored.budget.model_turn_limit_explicit is True
    assert restored.budget.max_model_turns == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [IntentKind.WIDGET_CREATE, IntentKind.WIDGET_MODIFY])
async def test_confirmed_widget_route_releases_default_turn_cap(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch, kind: IntentKind
) -> None:
    workflow, store = development
    intent = IntentPlan(kind=kind, app_id="budget-app", instruction="Build an App")
    monkeypatch.setattr("backend.agent.harness.AgentOrchestrator._classify_intent", AsyncMock(return_value=intent))
    state = _state("route", kind=IntentKind.CONVERSE)
    outcome = await workflow(_run(store, state), state)
    assert isinstance(outcome, Continue)
    assert outcome.next_phase == "plan"
    assert state.budget.max_model_turns is None


@pytest.mark.asyncio
async def test_historical_widget_checkpoint_can_generate_after_eight_calls(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store = development
    state = _state("plan", budget=RunBudget(model_turns=8))
    state = AgentRunState.model_validate_json(state.model_dump_json())
    budgets: list[ToolLoopBudget] = []

    async def generate(**kwargs: Any) -> str:
        budget = kwargs["budget"]
        budgets.append(budget)
        budget.on_model_call()
        budget.on_usage({"total_tokens": 10, "cost_usd": 0.01})
        return "Implement the App with its approved Runtime Contract."

    monkeypatch.setattr("backend.agent.durable_workflow.PlanGenerationService.generate_plan", generate)
    run = _run(store, state)
    for _ in range(3):
        state.phase = "plan"
        state.data.pop("plan_candidate", None)
        outcome = await workflow(run, state)
        assert isinstance(outcome, Wait)
    assert state.budget.max_model_turns is None
    assert state.budget.model_turns == 11
    assert state.budget.tokens_used == 30
    assert state.budget.cost_usd == pytest.approx(0.03)
    assert all(budget.max_iterations == 1 and budget.max_tool_calls == 0 for budget in budgets)


@pytest.mark.asyncio
async def test_nondefault_explicit_widget_cap_still_fails_closed(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store = development
    generation = AsyncMock()
    monkeypatch.setattr("backend.agent.durable_workflow.PlanGenerationService.generate_plan", generation)
    state = _state("plan", budget=RunBudget(max_model_turns=4, model_turns=4))
    outcome = await workflow(_run(store, state), state)
    assert isinstance(outcome, Failed)
    assert outcome.error_code == "budget_exhausted"
    assert state.budget.max_model_turns == 4
    generation.assert_not_awaited()


@pytest.mark.asyncio
async def test_converse_still_rejects_exhausted_default_turn_cap(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store = development
    response = AsyncMock()
    monkeypatch.setattr("backend.agent.harness.AgentOrchestrator._handle_converse", response)
    state = _state("converse", kind=IntentKind.CONVERSE, budget=RunBudget(model_turns=8))
    outcome = await workflow(_run(store, state), state)
    assert isinstance(outcome, Failed)
    assert outcome.error_code == "budget_exhausted"
    assert state.budget.max_model_turns == 8
    response.assert_not_awaited()


@pytest.mark.asyncio
async def test_deterministic_verify_never_requests_model_allowance_at_eight_of_eight(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store = development
    contract, staged = _draft(workflow, "export default function App() { return null; }")
    state = _state(
        "verify",
        budget=RunBudget(max_model_turns=8, model_turns=8, tokens_used=64_000, cost_usd=5.0),
        data={"runtime_contract": contract, "staged_app": staged},
    )
    allowance = Mock(side_effect=AssertionError("Deterministic verification must not admit a model call"))
    monkeypatch.setattr(workflow, "_model_budget", allowance)
    outcome = await workflow(_run(store, state), state)
    assert isinstance(outcome, Continue)
    assert outcome.next_phase == "promote"
    assert state.data["verification_passed"] is True
    assert state.budget.model_turns == 8
    allowance.assert_not_called()
    assert not Path(staged["live_dir"]).exists()


@pytest.mark.asyncio
async def test_actual_verification_fallback_with_exhausted_cap_cannot_publish(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store = development
    source = "export default function App() { const a = {action: 'create_node', type: 'Task', properties: 42}; return null; }"
    contract, staged = _draft(workflow, source)
    state = _state(
        "verify",
        budget=RunBudget(max_model_turns=4, model_turns=4),
        data={"runtime_contract": contract, "staged_app": staged},
    )
    provider = SimpleNamespace(generate=AsyncMock())
    monkeypatch.setattr("backend.schema_verification.get_llm_provider", lambda *_args: provider)
    outcome = await workflow(_run(store, state), state)
    assert isinstance(outcome, Failed)
    assert outcome.error_code == "budget_exhausted"
    assert not state.data.get("verification_passed")
    provider.generate.assert_not_awaited()
    assert Path(staged["staging_dir"]).is_dir()
    assert not Path(staged["live_dir"]).exists()


@pytest.mark.asyncio
async def test_acp_entry_does_not_charge_a_harness_model_turn(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store = development
    contract, staged = _draft(workflow, "export default function App() { return null; }")
    workflow.coding_agent_runner = AsyncMock(
        return_value=CodingAgentStagedResult(
            app_id="budget-app",
            output="generated",
            staging_dir=Path(staged["staging_dir"]),
            live_dir=Path(staged["live_dir"]),
        )
    )
    # An exhausted operator-specified harness cap must not stop the independent
    # coding agent, and launching it must not fabricate an unobserved model call.
    state = _state(
        "stage_code", budget=RunBudget(max_model_turns=4, model_turns=4), data={"runtime_contract": contract}
    )
    outcome = await workflow(_run(store, state), state)
    assert isinstance(outcome, Continue)
    assert outcome.next_phase == "verify"
    workflow.coding_agent_runner.assert_awaited_once()
    assert state.budget.model_turns == 4


@pytest.mark.asyncio
async def test_verifier_budget_factory_is_lazy_and_exhaustion_is_authoritative() -> None:
    requested: list[bool] = []

    def exhausted() -> ToolLoopBudget:
        requested.append(True)
        raise BudgetExhaustedError("No model allowance")

    clean = await SchemaVerificationService.diff(
        "budget-app", {"js": "export default function App() { return null; }"}, [], budget_factory=exhausted
    )
    assert clean.is_clean
    assert requested == []
    source = "const a = {action: 'create_node', type: 'Task', properties: 42};"
    with pytest.raises(BudgetExhaustedError, match="No model allowance"):
        await SchemaVerificationService.diff("budget-app", {"js": source}, [], budget_factory=exhausted)
    assert requested == [True]


def test_retry_unlimited_budget_preserves_usage_and_resumes_retained_verification(
    development: tuple[DurableAgentWorkflow, RunStore],
) -> None:
    workflow, store = development
    contract, staged = _draft(workflow, "export default function App() { return null; }")
    state = _state(
        "verify",
        budget=RunBudget(max_model_turns=None, model_turns=12, tokens_used=30, cost_usd=0.03),
        data={"runtime_contract": contract, "staged_app": staged, "active_seconds": 9},
    )
    original = _run(store, state)
    store.transition(original["id"], "failed", error={"code": "budget_exhausted", "effect_state": "none"})
    coordinator = RunCoordinator(
        store, SimpleNamespace(get_action=lambda *_: None), SimpleNamespace(), SimpleNamespace()
    )
    retried = coordinator.retry(original["id"])
    restored = AgentRunState.model_validate(retried["state"])
    assert restored.phase == "verify"
    assert restored.data["runtime_contract"] == contract
    assert restored.data["staged_app"] == staged
    assert restored.budget.max_model_turns is None
    assert restored.budget.model_turns == 12
    assert restored.budget.tokens_used == 30
    assert restored.budget.cost_usd == 0.03
    assert restored.budget.max_tokens == 64_030
    assert restored.budget.max_cost_usd == 5.03
    assert restored.data["active_seconds"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("usage_limit", ["tokens", "cost"])
async def test_unlimited_turns_still_stop_before_next_paid_call_when_usage_is_exhausted(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch, usage_limit: str
) -> None:
    workflow, _store = development
    state = _state("plan", budget=RunBudget(max_model_turns=None, max_tokens=10, max_cost_usd=0.10))
    calls: list[int] = []

    async def response(*_args: Any) -> dict[str, Any]:
        calls.append(1)
        return {
            "content": "",
            "tool_calls": [{"id": "read-1", "function": {"name": "query_graph", "arguments": '{"query_json": "{}"}'}}],
            "usage": {"total_tokens": 10} if usage_limit == "tokens" else {"cost_usd": 0.10},
        }

    monkeypatch.setattr("backend.llm_service.call_llm_api", response)
    execute = AsyncMock(return_value=[])
    monkeypatch.setattr("backend.agent.tools.registry.execute", execute)
    tools = [{"type": "function", "function": {"name": "query_graph", "parameters": {"type": "object"}}}]
    provider = CloudLLMProvider("scripted", "fake")
    budget = workflow._model_budget(state, max_iterations=3, max_tool_calls=3)
    with pytest.raises(
        BudgetExhaustedError, match=f"{usage_limit[:-1] if usage_limit == 'tokens' else usage_limit} budget"
    ):
        await provider.generate([{"role": "user", "content": "Read the graph"}], tools=tools, budget=budget)
    assert len(calls) == 1
    assert state.budget.model_turns == 1


@pytest.mark.asyncio
async def test_converse_on_unlimited_saga_retains_eight_iteration_local_limit(
    development: tuple[DurableAgentWorkflow, RunStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, store = development
    state = _state("converse", kind=IntentKind.CONVERSE, budget=RunBudget(max_model_turns=None, model_turns=20))
    calls: list[int] = []

    async def response(*_args: Any) -> dict[str, Any]:
        calls.append(1)
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": f"read-{len(calls)}",
                    "function": {
                        "name": "query_graph",
                        "arguments": json.dumps({"query_json": json.dumps({"offset": len(calls)})}),
                    },
                }
            ],
        }

    monkeypatch.setattr("backend.llm_service.call_llm_api", response)
    execute = AsyncMock(return_value=[])
    monkeypatch.setattr("backend.agent.tools.registry.execute", execute)
    outcome = await workflow(_run(store, state), state)
    assert isinstance(outcome, Failed)
    assert "maximum iterations (8)" in outcome.message
    assert len(calls) == 8
    assert execute.await_count == 8
    assert state.budget.model_turns == 28


def test_cancelled_widget_retry_returns_to_bounded_admission_and_keeps_usage_windows(
    development: tuple[DurableAgentWorkflow, RunStore],
) -> None:
    _workflow, store = development
    state = _state("stage_code", budget=RunBudget(model_turns=12, tokens_used=30, cost_usd=0.03))
    state.apply_development_budget_policy()
    original = _run(store, state)
    store.transition(original["id"], "cancelled")
    coordinator = RunCoordinator(
        store, SimpleNamespace(get_action=lambda *_: None), SimpleNamespace(), SimpleNamespace()
    )
    retried = coordinator.retry(original["id"])
    restored = AgentRunState.model_validate(retried["state"])
    assert restored.phase == "route"
    assert restored.intent is None
    assert restored.budget.max_model_turns == 20
    assert restored.budget.model_turns == 12
    assert restored.budget.max_tokens == 64_030
    assert restored.budget.max_cost_usd == 5.03
    assert restored.data["retry_budget_window"] == {"model_turns": 8, "tokens": 64_000, "cost_usd": 5.0}
    # A second admission failure renews exactly the same finite windows.
    store.transition(retried["id"], "failed", error={"code": "budget_exhausted", "effect_state": "none"})
    repeated = AgentRunState.model_validate(coordinator.retry(retried["id"])["state"])
    assert repeated.budget.max_model_turns == 20
    assert repeated.budget.max_tokens == 64_030
    assert repeated.budget.max_cost_usd == 5.03


@pytest.mark.parametrize("window", [{}, {"model_turns": None}])
def test_partial_or_unlimited_old_retry_window_cannot_remove_explicit_finite_limit(window: dict[str, Any]) -> None:
    state = _state(
        "converse",
        kind=IntentKind.CONVERSE,
        budget=RunBudget(max_model_turns=8, model_turns=2),
        data={"retry_budget_window": window},
    )
    RunCoordinator._renew_agent_retry_budget(state)
    assert state.budget.max_model_turns == 10
    assert state.budget.max_tokens == 64_000
    assert state.budget.max_cost_usd == 5.0


def test_old_finite_retry_window_cannot_revive_unlimited_widget_cap() -> None:
    state = _state(
        "verify",
        budget=RunBudget(max_model_turns=None, model_turns=12),
        data={"retry_budget_window": {"model_turns": 8}},
    )
    RunCoordinator._renew_agent_retry_budget(state)
    assert state.budget.max_model_turns is None
    assert state.budget.model_turns == 12
    assert state.data["retry_budget_window"]["model_turns"] is None


@pytest.mark.asyncio
async def test_deterministic_findings_do_not_request_a_model_budget() -> None:
    factory = Mock(side_effect=AssertionError("A deterministic finding must not need a model"))
    source = "const a = {action: 'create_node', type: 'UnregisteredTask', properties: {title: 'x'}};"
    diff = await SchemaVerificationService.diff("budget-app", {"js": source}, [], budget_factory=factory)
    assert diff.is_clean is False
    assert len(diff.unknown_types) == 1
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_factory", [False, True])
async def test_actual_fallback_accepts_existing_budget_or_one_lazy_factory(
    monkeypatch: pytest.MonkeyPatch, use_factory: bool
) -> None:
    budget = ToolLoopBudget(max_iterations=1)
    factory = Mock(return_value=budget)
    response = AsyncMock(return_value=json.dumps({"unknown_props": [], "type_mismatches": [], "unknown_types": []}))
    monkeypatch.setattr("backend.schema_verification.get_llm_provider", lambda *_: SimpleNamespace(generate=response))
    source = "const a = {action: 'create_node', type: 'Task', properties: 42};"
    kwargs = {"budget_factory": factory} if use_factory else {"budget": budget}
    diff = await SchemaVerificationService.diff("budget-app", {"js": source}, [], **kwargs)
    assert diff.is_clean
    response.assert_awaited_once()
    assert response.call_args.kwargs["budget"] is budget
    assert factory.call_count == (1 if use_factory else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("elapsed, expected_remaining", [(20.0, 540.0), (561.0, None)])
async def test_fallback_budget_excludes_current_deterministic_verification_time(
    development: tuple[DurableAgentWorkflow, RunStore],
    monkeypatch: pytest.MonkeyPatch,
    elapsed: float,
    expected_remaining: float | None,
) -> None:
    workflow, store = development
    contract, staged = _draft(workflow, "export default function App() { return null; }")
    state = _state("verify", data={"runtime_contract": contract, "staged_app": staged, "active_seconds": 40.0})
    instant = [100.0]
    clock = SimpleNamespace(monotonic=lambda: instant[0])
    monkeypatch.setattr("backend.agent.durable_workflow.time", clock)
    monkeypatch.setattr("backend.agent.decisions.time", clock)

    def delayed_failure(*_args: Any) -> Any:
        instant[0] += elapsed
        raise ValueError("Parser could not inspect this artifact")

    monkeypatch.setattr("backend.schema_verification.diff_controller_js", delayed_failure)
    response = AsyncMock(return_value=json.dumps({"unknown_props": [], "type_mismatches": [], "unknown_types": []}))
    monkeypatch.setattr("backend.schema_verification.get_llm_provider", lambda *_: SimpleNamespace(generate=response))
    outcome = await workflow(_run(store, state), state)
    if expected_remaining is None:
        assert isinstance(outcome, Failed)
        assert outcome.error_code == "budget_exhausted"
        response.assert_not_awaited()
        assert not state.data.get("verification_passed")
    else:
        assert isinstance(outcome, Continue)
        response.assert_awaited_once()
        assert response.call_args.kwargs["budget"].wall_clock_s == expected_remaining
