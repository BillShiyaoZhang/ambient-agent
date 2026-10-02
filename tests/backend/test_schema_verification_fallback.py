"""Exercise the real model fallback and its durable publication gate."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.errors import VerificationError, WorkflowError
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.capabilities.models import RuntimeContract
from backend.graph_db import GraphDatabase
from backend.run_service import AgentRunState, Continue, RunStore, Wait
from backend.schema_diff import VerificationDiff, diff_controller_js
from backend.schema_verification import SchemaVerificationService


# This syntactically valid template has a non-object properties value that the
# deterministic extractor cannot inspect. No production method is mocked to
# trigger fallback: the real extractor raises while deduplicating the template.
CONTROLLER_REQUIRING_FALLBACK = """export default function App() {
  const deferredAction = {action: 'create_node', type: 'Task', properties: 42};
  return null;
}
"""
SCHEMAS = [{"id": "Task", "properties": {"title": "string"}}]
UNKNOWN_PROP = {
    "node_type": "Task",
    "property_name": "priority",
    "sample_value_repr": "2",
    "occurrences": 1,
}
TYPE_MISMATCH = {
    "node_type": "Task",
    "property_name": "title",
    "schema_type": "string",
    "observed_value_repr": "42",
}
UNKNOWN_TYPE = {"type_name": "UnregisteredTask", "occurrences": 1}
MODEL_SNAPSHOT = {
    "primary": {"provider_id": "fake", "model_id": "scripted"},
    "fast": {"provider_id": "fake", "model_id": "scripted"},
}


def _payload(*, props: bool = False, mismatch: bool = False, types: bool = False) -> dict[str, Any]:
    return {
        "unknown_props": [dict(UNKNOWN_PROP)] if props else [],
        "type_mismatches": [dict(TYPE_MISMATCH)] if mismatch else [],
        "unknown_types": [dict(UNKNOWN_TYPE)] if types else [],
    }


def _scripted_provider(monkeypatch: pytest.MonkeyPatch, response: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class ScriptedProvider:
        async def generate(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
            calls.append({"messages": messages, **kwargs})
            return response

    monkeypatch.setattr("backend.schema_verification.get_llm_provider", lambda *_args: ScriptedProvider())
    return calls


def test_fixture_reaches_the_real_deterministic_failure() -> None:
    with pytest.raises(AttributeError, match="keys"):
        diff_controller_js(CONTROLLER_REQUIRING_FALLBACK, SCHEMAS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, expected_counts, report_text",
    [
        pytest.param(_payload(props=True), (1, 0, 0), "Task.priority", id="unknown-property"),
        pytest.param(_payload(mismatch=True), (0, 1, 0), "Type mismatches", id="type-mismatch"),
        pytest.param(_payload(types=True), (0, 0, 1), "UnregisteredTask", id="unknown-type"),
        pytest.param(_payload(props=True, mismatch=True, types=True), (1, 1, 1), "Recommendations", id="mixed"),
    ],
)
async def test_fallback_findings_are_dirty_at_construction(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    expected_counts: tuple[int, int, int],
    report_text: str,
) -> None:
    calls = _scripted_provider(monkeypatch, json.dumps(payload))

    diff = await SchemaVerificationService.diff("fallback-app", {"js": CONTROLLER_REQUIRING_FALLBACK}, SCHEMAS)

    assert len(calls) == 1
    assert calls[0]["audit_context"]["stage"] == "schema_verification"
    assert (len(diff.unknown_props), len(diff.type_mismatches), len(diff.unknown_types)) == expected_counts
    assert diff.is_clean is False
    assert diff.has_issues is True
    assert "WARNING" in diff.to_markdown()
    assert report_text in diff.to_markdown()
    assert "PASSED" not in diff.to_markdown()
    assert "is_clean" in {field.name for field in fields(VerificationDiff)}


@pytest.mark.asyncio
async def test_explicit_empty_finding_lists_are_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _scripted_provider(monkeypatch, json.dumps(_payload()))

    diff = await SchemaVerificationService.diff("fallback-app", {"js": CONTROLLER_REQUIRING_FALLBACK}, SCHEMAS)

    assert len(calls) == 1
    assert diff.is_clean is True
    assert diff.has_issues is False
    assert diff.to_markdown() == "✅ Schema Verification PASSED"
    assert diff.to_per_field_payload() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        pytest.param("not JSON", id="invalid-json"),
        pytest.param("[]", id="non-object"),
        pytest.param("{}", id="missing-all-lists"),
        pytest.param('{"unknown_props": [], "unknown_types": []}', id="missing-mismatch-list"),
        pytest.param(json.dumps({**_payload(), "unknown_props": None}), id="null-list"),
        pytest.param(json.dumps({**_payload(), "unknown_props": {}}), id="object-instead-of-list"),
        pytest.param(json.dumps({**_payload(), "unknown_props": [{}]}), id="missing-finding-fields"),
        pytest.param(json.dumps({**_payload(), "unknown_props": [{**UNKNOWN_PROP, "node_type": ""}]}), id="empty-type"),
        pytest.param(
            json.dumps({**_payload(), "unknown_props": [{**UNKNOWN_PROP, "occurrences": -1}]}), id="negative-count"
        ),
        pytest.param(
            json.dumps({**_payload(), "unknown_props": [{**UNKNOWN_PROP, "occurrences": True}]}), id="boolean-count"
        ),
        pytest.param(
            json.dumps({**_payload(), "type_mismatches": [{**TYPE_MISMATCH, "schema_type": 42}]}), id="non-text-type"
        ),
        pytest.param(json.dumps({**_payload(), "unknown_types": ["UnregisteredTask"]}), id="non-object-finding"),
        pytest.param(json.dumps({**_payload(), "is_clean": True}), id="unsolicited-clean-assertion"),
    ],
)
async def test_malformed_fallback_cannot_claim_verification_success(
    monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    calls = _scripted_provider(monkeypatch, response)

    with pytest.raises(VerificationError, match="Both deterministic and model schema verification failed") as caught:
        await SchemaVerificationService.diff("fallback-app", {"js": CONTROLLER_REQUIRING_FALLBACK}, SCHEMAS)

    assert len(calls) == 1
    assert caught.value.code == "verification_failed"
    assert caught.value.retryable is True


async def _execute_step(
    store: RunStore, workflow: DurableAgentWorkflow, run_id: str, worker: str
) -> tuple[Any, Any, Any]:
    claimed = store.claim_next(worker, global_limit=4, owner_limit=1)
    assert claimed is not None and claimed["id"] == run_id
    state = AgentRunState.model_validate(claimed["state"])
    phase = state.phase
    attempt = store.begin_step_attempt(run_id, phase, lease_owner=worker, lease_epoch=claimed["lease_epoch"])
    assert attempt is not None
    outcome = await workflow(claimed, state)
    committed = store.commit_step(
        run_id,
        phase,
        attempt=attempt,
        lease_owner=worker,
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=outcome,
    )
    return outcome, committed, state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_payload(props=True), id="unknown-property"),
        pytest.param(_payload(mismatch=True), id="type-mismatch"),
        pytest.param(_payload(types=True), id="unknown-type"),
        pytest.param(_payload(props=True, mismatch=True, types=True), id="mixed"),
    ],
)
async def test_real_fallback_findings_wait_for_repair_and_never_reach_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    calls = _scripted_provider(monkeypatch, json.dumps(payload))
    app_id = "fallback-app"
    apps_dir = tmp_path / "apps"
    staging_dir = apps_dir / f".{app_id}.staging-{'c' * 32}"
    live_dir = apps_dir / app_id
    staging_dir.mkdir(parents=True)
    (staging_dir / "controller.js").write_text(CONTROLLER_REQUIRING_FALLBACK, encoding="utf-8")
    contract = RuntimeContract.create(app_id=app_id, schemas=SCHEMAS, capabilities=[]).to_dict()
    (staging_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": app_id,
                "title": "Fallback App",
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": ["Task"],
                "capabilities": [],
            }
        ),
        encoding="utf-8",
    )
    store = RunStore(str(tmp_path))
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=store,
        app_manager=SimpleNamespace(apps_dir=apps_dir),
        graph_db=GraphDatabase(str(tmp_path)),
        llm_config_store=SimpleNamespace(resolve=lambda _selection: None),
        coding_agent_runner=None,
    )
    state = AgentRunState(
        phase="verify",
        workflow_type="widget_create",
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id="fallback-session",
        model_snapshot=MODEL_SNAPSHOT,
        intent=IntentPlan(kind=IntentKind.WIDGET_CREATE, app_id=app_id, instruction="Build an App").to_dict(),
        data={
            "runtime_contract": contract,
            "staged_app": {
                "app_id": app_id,
                "output": "generated draft",
                "staging_dir": str(staging_dir),
                "live_dir": str(live_dir),
            },
        },
    )
    run = store.create_run(
        owner_id="session:fallback-session",
        action_id="chat",
        action_title="Chat",
        source_type="chat",
        source_id="fallback-session",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "Build an App"},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )

    outcome, waiting, verified_state = await _execute_step(store, workflow, run["id"], "verify-worker")

    assert len(calls) == 1
    assert isinstance(outcome, Wait)
    assert not isinstance(outcome, Continue)
    assert waiting["status"] == "waiting_user"
    assert verified_state.phase == "wait_override"
    assert not verified_state.data.get("verification_passed")
    assert "WARNING" in verified_state.data["verification_report"]
    interaction = store.get_interaction(outcome.interaction_id)
    assert interaction is not None
    assert interaction["type"] == "verification_approval"
    assert "WARNING" in interaction["payload"]["report"]
    assert not live_dir.exists()
    assert staging_dir.is_dir()

    # Even an approval cannot bypass findings returned by the real fallback.
    store.resolve_interaction(outcome.interaction_id, {"approved": "approve"}, expected_run_version=waiting["version"])
    rejected, waiting_again, rejected_state = await _execute_step(store, workflow, run["id"], "repair-worker")
    assert isinstance(rejected, Wait)
    assert waiting_again["status"] == "waiting_user"
    assert rejected_state.phase == "wait_override"
    assert not rejected_state.data.get("verification_passed")
    assert len(calls) == 1
    assert not live_dir.exists()

    with pytest.raises(WorkflowError, match="unverified artifact"):
        await workflow._phase_promote(run, rejected_state)
