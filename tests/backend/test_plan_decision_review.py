from __future__ import annotations

import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import backend.agent.decisions as decision_module
from backend.agent.decisions import DecisionConfig
from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntent, SubIntentKind
from backend.agent.jev_router import JevJSONClient
from backend.graph_db import GraphDatabase
from backend.models import ChatSession
from backend.run_service import AgentRunState, Continue, RunStore, Wait
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture
def workflow_tape(tmp_path):
    store = RunStore(str(tmp_path))
    storage = WorkspaceStorage(str(tmp_path))
    storage.add(ChatSession(id="plan-session", title="Plan review"))
    storage.commit()
    config_store = MagicMock()
    coding = AsyncMock(side_effect=AssertionError("Plan decisions must not execute coding"))
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=store,
        app_manager=SimpleNamespace(apps_dir=tmp_path / "apps", list_apps=lambda: []),
        graph_db=GraphDatabase(str(tmp_path)),
        llm_config_store=config_store,
        coding_agent_runner=coding,
    )
    return SimpleNamespace(store=store, storage=storage, workflow=workflow, coding=coding)


def create_run(tape, *, mode="cascade", candidate=None, data=None, config=None):
    model = {"provider_id": "fake", "model_id": "plan-generation"}
    intent = IntentPlan(kind=IntentKind.WIDGET_CREATE, app_id="task-app", instruction="Create a task editor")
    state = AgentRunState(
        workflow_type="widget_create",
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id="plan-session",
        phase="plan",
        intent=intent.to_dict(),
        model_snapshot={
            "primary": model,
            "fast": model,
            "workflow_decisions": (config or DecisionConfig(mode=mode)).snapshot(),
        },
        data={"language": "en", **(data or {}), **({"plan_candidate": candidate} if candidate else {})},
    )
    run = tape.store.create_run(
        owner_id="session:plan-session",
        action_id="chat",
        action_title="Chat",
        source_type="chat",
        source_id="plan-session",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": intent.instruction},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    return run, state, intent


async def execute_step(tape, run_id):
    claimed = tape.store.claim_next("plan-worker", global_limit=4, owner_limit=1)
    assert claimed is not None and claimed["id"] == run_id
    state = AgentRunState.model_validate(claimed["state"])
    phase = state.phase
    attempt = tape.store.begin_step_attempt(
        run_id, phase, lease_owner="plan-worker", lease_epoch=claimed["lease_epoch"]
    )
    assert attempt is not None
    outcome = await tape.workflow(claimed, state)
    committed = tape.store.commit_step(
        run_id,
        phase,
        attempt=attempt,
        lease_owner="plan-worker",
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=outcome,
    )
    return outcome, committed, state


def install_review(monkeypatch, *, adverse=False, status=200):
    requests = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "plan-review-test-key-never-persist")

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        levels = payload["questions"]["coverage"]["criteria"]
        score = 0 if adverse else 2
        return httpx.Response(
            status,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "relevant": {"type": "noul", "noul": 0.01 if adverse else 0.99},
                    "scope_creep": {"type": "noul", "noul": 0.99 if adverse else 0.01},
                    "coverage": {
                        "type": "score",
                        "score": score,
                        "confidence": 1.0,
                        "probabilities": {str(index): 1.0 if index == score else 0.0 for index in range(len(levels))},
                        "legend": {str(index): level for index, level in enumerate(levels)},
                    },
                },
                "usage": {"input_tokens": 11, "output_tokens": 3},
            },
        )

    client = JevJSONClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(decision_module, "JevJSONClient", lambda: client)
    return requests


def install_plan_generation(monkeypatch, candidate="Add a task editor with Graph persistence"):
    async def generate(*args, **kwargs):
        budget = kwargs["budget"]
        budget.on_model_call()
        budget.on_usage({"total_tokens": 5})
        return candidate

    function = AsyncMock(side_effect=generate)
    monkeypatch.setattr("backend.agent.durable_workflow.PlanGenerationService.generate_plan", function)
    return function


@pytest.mark.asyncio
async def test_plan_receives_original_constraints_omitted_by_routing(workflow_tape, monkeypatch):
    original = "Create a task editor. Persist tasks after reload, reject blank titles and keep existing tasks."
    run, state, _intent = create_run(workflow_tape, mode="off")
    run["input"]["content"] = original
    generation = install_plan_generation(monkeypatch)
    outcome = await workflow_tape.workflow(run, state)
    assert isinstance(outcome, Wait)
    assert original in generation.call_args.kwargs["instruction"]
    assert original in state.data["plan_review_instruction"]


@pytest.mark.parametrize("scoped", ["multi", "slash"])
def test_original_context_cannot_broaden_a_scoped_app_step(workflow_tape, scoped):
    run, state, intent = create_run(workflow_tape, mode="off")
    run["input"]["content"] = "Create a task editor and delete all notes in a separate App."
    if scoped == "multi":
        state.data["return_to_multi"] = True
    else:
        intent.rationale = "explicit slash command"
    assert workflow_tape.workflow._widget_instruction(run, state, intent) == "Create a task editor"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["candidate", "instruction", "app_id", "feedback"])
async def test_review_checkpoint_reuses_same_inputs_and_invalidates_changed_inputs(
    workflow_tape, monkeypatch, changed_field
):
    requests = install_review(monkeypatch)
    run, state, intent = create_run(workflow_tape, candidate="Initial task editor plan")
    await workflow_tape.workflow._review_plan_candidate(
        run, state, intent, "Initial task editor plan", time.monotonic()
    )
    first = copy.deepcopy(state.data["plan_review"])
    first_hash = state.data["plan_review_input_hash"]
    restored = AgentRunState.model_validate_json(state.model_dump_json())
    # The deployment may change while this exact checkpoint is resumed.
    monkeypatch.setenv("JEV_DECISION_MODE", "off")
    await workflow_tape.workflow._review_plan_candidate(
        run, restored, intent, "Initial task editor plan", time.monotonic()
    )

    assert len(requests) == 1
    assert restored.budget.model_turns == 1
    assert restored.budget.tokens_used == 14
    assert restored.data["plan_review"] == first
    assert restored.data["plan_review_input_hash"] == first_hash
    changed = IntentPlan.from_dict(intent.to_dict())
    candidate = "Initial task editor plan"
    if changed_field == "candidate":
        candidate = "Task editor with a due date"
    elif changed_field == "instruction":
        changed.instruction = "Create a task editor with a due date"
    elif changed_field == "app_id":
        changed.app_id = "other-task-app"
    else:
        restored.data["plan_review_instruction"] = intent.instruction + "\n\nPlease include due dates"
    await workflow_tape.workflow._review_plan_candidate(run, restored, changed, candidate, time.monotonic())

    assert len(requests) == 2
    assert restored.budget.model_turns == 2
    assert restored.data["plan_review_input_hash"] != first_hash
    assert restored.data["plan_review"]["request_hash"] != first["request_hash"]
    assert len(workflow_tape.storage.get_audit_logs()) == 2


@pytest.mark.asyncio
async def test_off_review_does_not_request_model_budget_or_transport(workflow_tape, monkeypatch):
    requests = install_review(monkeypatch)
    run, state, intent = create_run(workflow_tape, mode="off", candidate="Already generated plan")
    state.budget.model_turns = state.budget.max_model_turns
    budget = MagicMock(side_effect=AssertionError("Off review must not ask for a model turn"))
    monkeypatch.setattr(workflow_tape.workflow, "_model_budget", budget)

    await workflow_tape.workflow._review_plan_candidate(run, state, intent, "Already generated plan", time.monotonic())

    budget.assert_not_called()
    assert requests == []
    assert "plan_review" not in state.data
    assert "plan_review_input_hash" not in state.data


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "cascade"])
@pytest.mark.parametrize("adverse", [False, True])
async def test_plan_phase_requires_user_approval_regardless_of_score_or_noul(workflow_tape, monkeypatch, mode, adverse):
    requests = install_review(monkeypatch, adverse=adverse)
    generation = install_plan_generation(monkeypatch)
    run, _, _ = create_run(workflow_tape, mode=mode)

    outcome, committed, state = await execute_step(workflow_tape, run["id"])

    assert isinstance(outcome, Wait)
    assert committed["status"] == "waiting_user"
    assert state.phase == "wait_plan"
    assert "approved_plan" not in state.data
    interaction = workflow_tape.store.get_interaction(outcome.interaction_id)
    assert interaction["status"] == "pending"
    assert interaction["type"] == "plan_approval"
    assert interaction["payload"]["plan"] == "Add a task editor with Graph persistence"
    review = state.data["plan_review"]
    assert review["error"] is None
    assert review["answers"]["coverage"]["score"] == (0 if adverse else 2)
    assert review["answers"]["relevant"]["noul"] == (0.01 if adverse else 0.99)
    assert state.budget.model_turns == 2
    assert state.budget.tokens_used == 19
    assert len(requests) == 1
    generation.assert_awaited_once()
    workflow_tape.coding.assert_not_called()
    assert not any(event.type == "widget" for event in outcome.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("edited", [False, True])
async def test_approval_preserves_only_review_of_the_exact_approved_candidate(workflow_tape, monkeypatch, edited):
    requests = install_review(monkeypatch, adverse=True)
    install_plan_generation(monkeypatch)
    run, _, _ = create_run(workflow_tape)
    first, waiting, candidate_state = await execute_step(workflow_tape, run["id"])
    original_review = copy.deepcopy(candidate_state.data["plan_review"])
    approved = "Use a task editor with due dates" if edited else candidate_state.data["plan_candidate"]
    workflow_tape.store.resolve_interaction(
        first.interaction_id,
        {"approved": "approve", "plan": approved},
        expected_run_version=waiting["version"],
    )

    outcome, committed, state = await execute_step(workflow_tape, run["id"])

    assert isinstance(outcome, Continue)
    assert outcome.next_phase == "align_schema"
    assert state.data["approved_plan"] == approved
    assert committed["state"]["phase"] == "align_schema"
    if edited:
        assert "plan_review" not in state.data
        assert "plan_review_input_hash" not in state.data
    else:
        assert state.data["plan_review"] == original_review
        assert "plan_review_input_hash" in state.data
    assert len(requests) == 1
    workflow_tape.coding.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_state_chars", [12_000, 30_000])
async def test_refined_candidate_keeps_full_feedback_for_review_and_requires_new_approval(
    workflow_tape, monkeypatch, max_state_chars
):
    requests = install_review(monkeypatch)
    install_plan_generation(monkeypatch)
    run, _, intent = create_run(workflow_tape, config=DecisionConfig(mode="cascade", max_state_chars=max_state_chars))
    first, waiting, initial_state = await execute_step(workflow_tape, run["id"])
    original_hash = initial_state.data["plan_review_input_hash"]
    edited_draft = "Edited task editor plan before refinement"
    refined_candidate = "Add due dates and sorting to the task editor"
    feedback = "Please add due dates and sorting. " + "Detailed requirements. " * 550 + "REQUIRED_FEEDBACK_TAIL"
    assert len(feedback) > 12_000

    async def refine(*args, **kwargs):
        assert kwargs["current_plan"] == edited_draft
        assert kwargs["feedback"] == feedback
        kwargs["budget"].on_model_call()
        kwargs["budget"].on_usage({"total_tokens": 7})
        return refined_candidate

    refined = AsyncMock(side_effect=refine)
    monkeypatch.setattr("backend.agent.durable_workflow.PlanGenerationService.refine_plan", refined)
    workflow_tape.store.resolve_interaction(
        first.interaction_id,
        {"approved": "refine", "plan": edited_draft, "feedback": feedback},
        expected_run_version=waiting["version"],
    )

    outcome, committed, state = await execute_step(workflow_tape, run["id"])

    assert isinstance(outcome, Wait)
    assert outcome.interaction_id != first.interaction_id
    assert committed["status"] == "waiting_user"
    assert state.phase == "wait_plan"
    assert state.data["plan_candidate"] == refined_candidate
    assert "approved_plan" not in state.data
    assert state.data["plan_review_input_hash"] != original_hash
    assert state.data["plan_review"]["request_hash"] != initial_state.data["plan_review"]["request_hash"]
    assert state.data["plan_review_instruction"] == intent.instruction + "\n\n[PLAN REFINEMENT FEEDBACK]\n" + feedback
    if max_state_chars > 12_000:
        assert [request["state"]["candidate"] for request in requests] == [
            "Add a task editor with Graph persistence",
            refined_candidate,
        ]
        assert requests[1]["state"]["instruction"] == state.data["plan_review_instruction"]
        assert requests[1]["state"]["instruction"].endswith("REQUIRED_FEEDBACK_TAIL")
        assert state.data["plan_review"]["error"] is None
        assert state.budget.model_turns == 4
        assert state.budget.tokens_used == 40
    else:
        # The shared service declines oversized evidence; it must never turn
        # a truncated feedback prefix into apparently complete model evidence.
        assert len(requests) == 1
        assert state.data["plan_review"]["error"] == "jev_decision_state_too_large"
        assert state.budget.model_turns == 3
        assert state.budget.tokens_used == 26
    assert workflow_tape.store.get_interaction(first.interaction_id)["status"] == "resolved"
    assert workflow_tape.store.get_interaction(outcome.interaction_id)["status"] == "pending"
    refined.assert_awaited_once()
    workflow_tape.coding.assert_not_called()


@pytest.mark.asyncio
async def test_plan_rework_reviews_the_same_feedback_that_generated_the_candidate(workflow_tape, monkeypatch):
    requests = install_review(monkeypatch)
    generation = install_plan_generation(monkeypatch, candidate="Task editor with local UI state")
    feedback = "Keep UI state App-local and remove the unrequested Graph writes."
    run, _, intent = create_run(workflow_tape, data={"plan_rework_feedback": feedback})

    outcome, committed, state = await execute_step(workflow_tape, run["id"])

    expected_instruction = intent.instruction + "\n\n[PLAN REWORK FEEDBACK]\n" + feedback
    assert isinstance(outcome, Wait)
    assert committed["status"] == "waiting_user"
    assert generation.await_args.kwargs["instruction"] == expected_instruction
    assert state.data["plan_review_instruction"] == expected_instruction
    assert requests[0]["state"]["instruction"] == expected_instruction
    assert requests[0]["state"]["candidate"] == "Task editor with local UI state"
    assert "plan_rework_feedback" not in state.data
    assert "approved_plan" not in state.data


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["finish", "dispatch"])
async def test_saga_boundaries_remove_previous_widget_review_evidence(workflow_tape, boundary):
    run, state, first = create_run(workflow_tape, candidate="Old task editor plan")
    second = IntentPlan(kind=IntentKind.WIDGET_CREATE, app_id="notes-app", instruction="Create a notes list")
    state.intent = IntentPlan(
        kind=IntentKind.MULTI_INTENT,
        sub_intents=[
            SubIntent(kind=SubIntentKind.WIDGET_CREATE, app_id=first.app_id, instruction=first.instruction),
            SubIntent(kind=SubIntentKind.WIDGET_CREATE, app_id=second.app_id, instruction=second.instruction),
        ],
    ).to_dict()
    state.data.update(
        {
            "multi_index": 0 if boundary == "finish" else 1,
            "multi_preflight_complete": True,
            "multi_preflight_intents": [first.to_dict(), second.to_dict()],
            "current_intent": first.to_dict(),
            "return_to_multi": True,
            "plan_review": {"answers": {"relevant": {"type": "noul", "noul": 0.99}}},
            "plan_review_input_hash": "old-task-review-hash",
            "plan_review_instruction": "Task-only review instruction and prior feedback",
        }
    )

    if boundary == "finish":
        outcome = await workflow_tape.workflow._finish_subflow(run, state, content="Task App finished", result={})
        assert outcome.next_phase == "multi_dispatch"
        assert state.data["multi_index"] == 1
        assert "current_intent" not in state.data
    else:
        outcome = await workflow_tape.workflow._phase_multi_dispatch(run, state)
        assert outcome.next_phase == "plan"
        assert state.data["current_intent"] == second.to_dict()
    assert isinstance(outcome, Continue)
    assert "plan_candidate" not in state.data
    assert "plan_review" not in state.data
    assert "plan_review_input_hash" not in state.data
    assert "plan_review_instruction" not in state.data


@pytest.mark.asyncio
async def test_review_transport_failure_still_waits_for_explicit_plan_approval(workflow_tape, monkeypatch):
    install_review(monkeypatch, status=503)
    install_plan_generation(monkeypatch)
    run, _, _ = create_run(workflow_tape)

    outcome, committed, state = await execute_step(workflow_tape, run["id"])

    assert isinstance(outcome, Wait)
    assert committed["status"] == "waiting_user"
    assert state.data["plan_review"]["error"] == "jev_http_503"
    assert "approved_plan" not in state.data
