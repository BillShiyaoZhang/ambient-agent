from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.evaluation import (
    EvaluationGateError,
    EvaluationHarness,
    EvaluationScenario,
    EvaluationTrace,
    RunStoreTraceAdapter,
    ScriptedTape,
    exact_event_types,
    exact_outcome,
    safe_trajectory,
)
from backend.agent.harness import AgentOrchestrator
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.graph_db import GraphDatabase
from backend.models import ChatMessage, LLMAuditLog
from backend.run_service import AgentRunState, Failed, RunCoordinator, RunStore


MODEL_SNAPSHOT = {
    "primary": {"provider_id": "fake", "model_id": "scripted"},
    "fast": {"provider_id": "fake", "model_id": "scripted"},
}


class _FakeLLMConfigStore:
    def get_settings(self) -> dict[str, Any]:
        return {"default_model": MODEL_SNAPSHOT["primary"], "fast_model": MODEL_SNAPSHOT["fast"]}

    def resolve(self, selection: Any) -> SimpleNamespace:
        return SimpleNamespace(selection=selection)


class _FakeAppManager:
    def list_apps(self) -> list[dict[str, Any]]:
        return []

    def get_app_files(self, _app_id: str) -> None:
        return None


def _consume_scripted_model_call(
    orchestrator: AgentOrchestrator,
    *,
    stage: str,
    prompt: str,
    response: str,
    usage: dict[str, Any],
) -> None:
    assert orchestrator.run_context is not None
    assert orchestrator.tool_loop_budget is not None
    assert orchestrator.tool_loop_budget.on_model_call is not None
    assert orchestrator.tool_loop_budget.on_usage is not None
    orchestrator.tool_loop_budget.on_model_call()
    orchestrator.tool_loop_budget.on_usage(usage)
    orchestrator.db.add(
        LLMAuditLog(
            provider="fake",
            model="scripted",
            prompt=prompt,
            response=response,
            latency_ms=2.0,
            usage=usage,
            finish_reason="stop",
            **orchestrator.run_context.audit_context(stage=stage),
        )
    )
    orchestrator.db.commit()


def _contains_subsequence(observed: list[str], expected: list[str]) -> bool:
    cursor = iter(observed)
    return all(any(item == target for item in cursor) for target in expected)


@pytest.mark.asyncio
async def test_deterministic_tape_scores_outcome_trajectory_and_usage_metrics():
    expected = {"status": "succeeded", "artifact": "weather-card"}
    event_types = ["model_call", "tool_call", "final"]
    tape = ScriptedTape(
        [
            EvaluationTrace(
                outcome=expected,
                events=[
                    {"type": "model_call", "tokens": 100, "cost_usd": 0.01},
                    {"type": "tool_call", "tool_name": "query_graph", "effect": "read"},
                    {"type": "final"},
                ],
                latency_ms=100,
                recovery_attempts=1,
                recoveries_succeeded=1,
            ),
            EvaluationTrace(
                outcome=expected,
                events=[
                    {"type": "model_call", "tokens": 50, "cost_usd": 0.02},
                    {"type": "tool_call", "tool_name": "query_graph", "effect": "read"},
                    {"type": "final"},
                ],
                latency_ms=300,
            ),
        ]
    )
    scenario = EvaluationScenario.from_tape(
        "deterministic-weather",
        tape,
        outcome_scorer=exact_outcome(expected),
        trajectory_scorer=exact_event_types(event_types),
    )

    report = await EvaluationHarness().evaluate([scenario], enforce_ci_gate=True)

    assert report.ci_gate_passed is True
    assert report.runs == 2
    assert report.outcome_score == 1
    assert report.trajectory_score == 1
    assert report.success_rate == 1
    assert report.unsafe_action_rate == 0
    assert report.tool_calls == 2
    assert report.tokens == 150
    assert report.cost_usd == pytest.approx(0.03)
    assert report.latency_ms == 200
    assert report.recovery_rate == 1
    assert report.scenarios[0].samples == report.samples
    assert '"success_rate":1.0' in report.model_dump_json()


@pytest.mark.asyncio
async def test_scripted_converse_runs_through_coordinator_and_evaluates_persisted_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RunStore(str(tmp_path))
    app_manager = _FakeAppManager()
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=store,
        app_manager=app_manager,
        graph_db=GraphDatabase(str(tmp_path)),
        llm_config_store=_FakeLLMConfigStore(),
        opencode_runner=lambda **_kwargs: pytest.fail("Converse must not invoke OpenCode"),
    )
    coordinator = RunCoordinator(store, SimpleNamespace(), app_manager, SimpleNamespace())
    coordinator.register_internal_agent_executor(workflow)

    async def scripted_route(
        self: AgentOrchestrator,
        content: str,
        session_id: str,
        language: str = "zh",
    ) -> IntentPlan:
        assert (content, session_id, language) == ("say hello", "eval-session", "zh")
        _consume_scripted_model_call(
            self,
            stage="route",
            prompt=content,
            response='{"kind":"converse"}',
            usage={"input_tokens": 4, "output_tokens": 3, "cost_usd": 0.001},
        )
        return IntentPlan(
            kind=IntentKind.CONVERSE,
            confidence=1.0,
            rationale="scripted evaluation route",
            instruction=content,
        )

    async def scripted_converse(
        self: AgentOrchestrator,
        plan: IntentPlan,
        session_id: str,
        content: str,
        language: str,
        on_update: Any,
    ) -> tuple[ChatMessage, None]:
        assert plan.kind == IntentKind.CONVERSE
        assert (session_id, content, language) == ("eval-session", "say hello", "zh")
        _consume_scripted_model_call(
            self,
            stage="converse",
            prompt=content,
            response="scripted hello",
            usage={"total_tokens": 21, "cost_usd": 0.002},
        )
        await on_update({"type": "agent_progress", "message": "scripted model completed"})
        message = ChatMessage(
            session_id=session_id,
            role="agent",
            sender="agent",
            content="scripted hello",
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message, None

    monkeypatch.setattr(AgentOrchestrator, "_classify_intent", scripted_route)
    monkeypatch.setattr(AgentOrchestrator, "_handle_converse", scripted_converse)

    state = AgentRunState(
        workflow_type="chat",
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id="eval-session",
        phase="route",
        model_snapshot=MODEL_SNAPSHOT,
        data={"trace_id": "trace-scripted-converse"},
    )
    try:
        submitted = coordinator.submit_internal_agent(
            owner_id="session:eval-session",
            action_id="chat",
            title="Scripted Converse",
            session_id="eval-session",
            input_data={"content": "say hello"},
            workflow_type=state.workflow_type,
            workflow_version=state.workflow_version,
            state=state,
        )
        terminal = await coordinator.wait_terminal(submitted["id"], timeout=5)
    finally:
        await coordinator.shutdown()

    assert terminal["status"] == "succeeded"
    persisted = store.get_run(submitted["id"], include_events=True)
    assert persisted is not None
    production_event_types = [event["type"] for event in persisted["events"]]
    assert _contains_subsequence(
        production_event_types,
        [
            "run_created",
            "step_started",
            "agent_routed",
            "step_committed",
            "step_started",
            "agent_progress",
            "reply",
            "step_committed",
            "status_changed",
        ],
    )

    adapter = RunStoreTraceAdapter(store)
    trace = adapter.trace(submitted["id"])
    expected_outcome = {
        "run_id": submitted["id"],
        "status": "succeeded",
        "result": {"message": "scripted hello", "app_id": None},
        "artifacts": [],
    }
    assert trace.outcome == expected_outcome
    assert [(event.step_id, event.attempt, event.status) for event in trace.events if event.source == "attempt"] == [
        ("route", 1, "succeeded"),
        ("converse", 1, "succeeded"),
    ]
    assert [(event.step_id, getattr(event, "stage", None)) for event in trace.events if event.source == "audit"] == [
        ("route", "route"),
        ("converse", "converse"),
    ]
    assert sum(event.tokens for event in trace.events) == 28
    assert sum(event.cost_usd for event in trace.events) == pytest.approx(0.003)
    assert not any(event.unsafe for event in trace.events)

    def production_converse_trajectory(candidate: EvaluationTrace) -> float:
        attempts = [event.step_id for event in candidate.events if event.source == "attempt"]
        stages = [getattr(event, "stage", None) for event in candidate.events if event.source == "audit"]
        run_events = [event.type for event in candidate.events if event.source == "event"]
        return float(
            attempts == ["route", "converse"]
            and stages == ["route", "converse"]
            and _contains_subsequence(
                run_events,
                ["agent_routed", "step_committed", "agent_progress", "reply", "step_committed"],
            )
        )

    scenario = EvaluationScenario(
        name="persisted-scripted-converse",
        mode="deterministic",
        repetitions=1,
        runner=lambda _repetition: adapter.trace(submitted["id"]),
        outcome_scorer=exact_outcome(expected_outcome),
        trajectory_scorer=production_converse_trajectory,
    )
    report = await EvaluationHarness().evaluate([scenario], enforce_ci_gate=True)

    assert report.ci_gate_passed is True
    assert report.success_rate == 1
    assert report.tokens == 28
    assert report.cost_usd == pytest.approx(0.003)
    assert report.tool_calls == 0
    assert report.unsafe_action_rate == 0


def test_run_store_trace_derives_unknown_effect_as_unsafe_without_manual_label(tmp_path: Path) -> None:
    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="chat",
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id="unsafe-session",
        phase="converse",
        model_snapshot=MODEL_SNAPSHOT,
    )
    run = store.create_run(
        owner_id="session:unsafe-session",
        action_id="chat",
        action_title="Unknown effect",
        source_type="chat",
        source_id="unsafe-session",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "exercise unknown effect"},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    claimed = store.claim_next("unsafe-worker", global_limit=1, owner_limit=1)
    assert claimed is not None
    attempt = store.begin_step_attempt(
        run["id"],
        "converse",
        lease_owner="unsafe-worker",
        lease_epoch=claimed["lease_epoch"],
    )
    assert attempt == 1
    store.commit_step(
        run["id"],
        "converse",
        attempt=attempt,
        lease_owner="unsafe-worker",
        lease_epoch=claimed["lease_epoch"],
        state=AgentRunState.model_validate(claimed["state"]),
        outcome=Failed(
            error_code="worker_lost",
            message="External effect completion is unknown",
            effect_state="unknown",
        ),
    )

    trace = RunStoreTraceAdapter(store).trace(run["id"])

    unsafe_events = [event for event in trace.events if event.unsafe]
    assert [(event.source, event.type, getattr(event, "effect_state", None)) for event in unsafe_events] == [
        ("attempt", "step_attempt", "unknown")
    ]
    assert trace.outcome["status"] == "needs_attention"
    assert trace.error == "run: needs_attention: External effect completion is unknown"
    assert safe_trajectory(trace) == 0


@pytest.mark.asyncio
async def test_deterministic_ci_gate_fails_on_unsafe_trajectory_even_if_scorers_pass():
    tape = ScriptedTape(
        [
            {
                "outcome": {"status": "succeeded"},
                "events": [
                    {
                        "type": "tool_call",
                        "tool_name": "shell",
                        "effect": "execute",
                        "unsafe": True,
                    }
                ],
                "latency_ms": 10,
            }
        ]
    )
    scenario = EvaluationScenario.from_tape(
        "unsafe-shell",
        tape,
        outcome_scorer=exact_outcome({"status": "succeeded"}),
        # An unsafe marker is an independent hard failure; a permissive custom
        # trajectory scorer cannot accidentally waive it.
        trajectory_scorer=lambda _trace: 1.0,
    )

    with pytest.raises(EvaluationGateError, match="unsafe-shell#1") as exc_info:
        await EvaluationHarness().evaluate([scenario], enforce_ci_gate=True)

    report = exc_info.value.report
    assert report.ci_gate_passed is False
    assert report.success_rate == 0
    assert report.unsafe_action_rate == 1
    assert report.samples[0].outcome_score == 1
    assert report.samples[0].trajectory_score == 1


def test_real_model_scenarios_require_at_least_three_repetitions():
    with pytest.raises(ValueError, match="repetitions >= 3"):
        EvaluationScenario(
            name="live-model",
            mode="real_model",
            repetitions=2,
            runner=lambda _repetition: EvaluationTrace(outcome="ok"),
            outcome_scorer=exact_outcome("ok"),
            trajectory_scorer=safe_trajectory,
        )


@pytest.mark.asyncio
async def test_real_model_mode_accepts_injected_offline_runner_and_runs_three_times():
    calls: list[int] = []

    async def fake_real_model_runner(repetition: int):
        calls.append(repetition)
        return {
            "outcome": "ok",
            "events": [{"type": "model_call", "tokens": 10, "cost_usd": 0.001}],
            "latency_ms": 30,
        }

    scenario = EvaluationScenario(
        name="offline-real-model-contract",
        mode="real_model",
        repetitions=3,
        runner=fake_real_model_runner,
        outcome_scorer=exact_outcome("ok"),
        trajectory_scorer=safe_trajectory,
    )

    report = await EvaluationHarness().evaluate([scenario], enforce_ci_gate=True)

    assert calls == [0, 1, 2]
    assert report.runs == 3
    assert report.success_rate == 1
    assert report.tokens == 30
    assert report.cost_usd == pytest.approx(0.003)
    # CI gating intentionally evaluates deterministic tapes only.
    assert report.ci_gate_passed is True


@pytest.mark.asyncio
async def test_runner_failure_becomes_a_scored_failure_instead_of_aborting_report():
    async def broken_runner(_repetition: int):
        raise TimeoutError("scripted model timeout")

    scenario = EvaluationScenario(
        name="timeout-tape",
        mode="deterministic",
        repetitions=1,
        runner=broken_runner,
        outcome_scorer=exact_outcome("ok"),
        trajectory_scorer=safe_trajectory,
    )

    report = await EvaluationHarness().evaluate([scenario])

    assert report.success_rate == 0
    assert report.ci_gate_passed is False
    assert report.samples[0].outcome_score == 0
    assert report.samples[0].trajectory_score == 0
    assert report.samples[0].error == "runner: TimeoutError: scripted model timeout"
