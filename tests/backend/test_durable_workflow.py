from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import backend.agent.durable_workflow as durable_workflow_module
from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.harness import AgentOrchestrator
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntent, SubIntentKind
from backend.app_manifest import AppManifest
from backend.capabilities.models import RuntimeContract
from backend.graph_db import GraphDatabase
from backend.models import ChatMessage, ChatSession
from backend.opencode_service import OpenCodeStagedResult
from backend.run_service import AgentRunState, Continue, Failed, RunCoordinator, RunStore, Succeeded, Wait
from backend.schema_diff import VerificationDiff
from backend.workspace_storage import WorkspaceStorage


MODEL_SNAPSHOT = {
    "primary": {"provider_id": "fake", "model_id": "scripted"},
    "fast": {"provider_id": "fake", "model_id": "scripted"},
}


class FakeLLMConfigStore:
    def get_settings(self) -> dict[str, Any]:
        return {"default_model": MODEL_SNAPSHOT["primary"], "fast_model": MODEL_SNAPSHOT["fast"]}

    def resolve(self, selection: Any) -> SimpleNamespace:
        return SimpleNamespace(selection=selection)


class FakeAppManager:
    def __init__(self, apps_dir: Path):
        self.apps_dir = apps_dir

    def list_apps(self) -> list[dict[str, Any]]:
        return []

    def get_app_files(self, app_id: str) -> dict[str, Any] | None:
        app_dir = self.apps_dir / app_id
        controller = app_dir / "controller.js"
        if not controller.is_file():
            return None
        manifest_path = app_dir / "manifest.json"
        manifest = AppManifest.read(manifest_path, expected_app_id=app_id) if manifest_path.is_file() else None
        return {
            "id": app_id,
            "title": "Durable Test App",
            "js": controller.read_text(encoding="utf-8"),
            "manifest_revision": manifest.revision if manifest else None,
            "grants_digest": manifest.grants_digest if manifest else None,
            "capabilities": [grant.to_dict() for grant in manifest.capabilities] if manifest else [],
        }


def _runtime_contract(app_id: str, capabilities: list[dict] | None = None) -> dict[str, Any]:
    return RuntimeContract.create(app_id=app_id, schemas=[], capabilities=capabilities or []).to_dict()


def _write_manifest(directory: Path, app_id: str, contract: dict[str, Any]) -> None:
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": app_id,
                "title": "Durable Test App",
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": [schema["id"] for schema in contract["schemas"]],
                "capabilities": contract["capabilities"],
            }
        ),
        encoding="utf-8",
    )


def test_staged_runtime_contract_rejects_backend_adapter_declarations(tmp_path: Path) -> None:
    app_id = "unsafe-backend-app"
    contract = _runtime_contract(app_id)
    manifest = {
        "manifest_version": 2,
        "id": app_id,
        "title": "Unsafe Backend App",
        "description": "",
        "app_version": "0.1.0",
        "intents": [],
        "schema_refs": [],
        "capabilities": [],
        "backend_type": "mcp",
        "mcp_server": {"command": ["node"], "args": ["server.js"], "env": {}},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(durable_workflow_module.WorkflowError, match="backend adapters"):
        DurableAgentWorkflow._assert_staged_runtime_contract(tmp_path, contract)


def _state(
    *,
    session_id: str = "session-1",
    phase: str = "route",
    workflow_type: str = "chat",
    intent: IntentPlan | None = None,
    data: dict[str, Any] | None = None,
) -> AgentRunState:
    return AgentRunState(
        workflow_type=workflow_type,
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id=session_id,
        phase=phase,
        intent=intent.to_dict() if intent else None,
        model_snapshot=MODEL_SNAPSHOT,
        data=data or {},
    )


def _create_run(store: RunStore, state: AgentRunState, *, content: str = "hello") -> dict[str, Any]:
    return store.create_run(
        owner_id=f"session:{state.session_id}",
        action_id="chat",
        action_title="Chat",
        source_type="chat",
        source_id=state.session_id,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": content},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )


def _workflow(
    tmp_path: Path,
    store: RunStore,
    graph_db: GraphDatabase,
    *,
    app_manager: FakeAppManager | None = None,
    coding_agent_runner: Any = None,
    emitted: list[dict[str, Any]] | None = None,
    app_diagnostic_loader: Any = None,
) -> DurableAgentWorkflow:
    async def fail_if_called(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("OpenCode must not be called in this workflow tape")

    async def event_sink(_session_id: str, payload: dict[str, Any]) -> None:
        if emitted is not None:
            emitted.append(payload)

    return DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=store,
        app_manager=app_manager or FakeAppManager(tmp_path / "apps"),
        graph_db=graph_db,
        llm_config_store=FakeLLMConfigStore(),
        coding_agent_runner=coding_agent_runner or fail_if_called,
        event_sink=event_sink,
        app_diagnostic_loader=app_diagnostic_loader,
    )


async def _execute_fenced_step(
    store: RunStore,
    workflow: DurableAgentWorkflow,
    run_id: str,
    *,
    worker_id: str,
) -> tuple[Any, dict[str, Any], AgentRunState]:
    claimed = store.claim_next(worker_id, global_limit=4, owner_limit=1)
    assert claimed is not None and claimed["id"] == run_id
    state = AgentRunState.model_validate(claimed["state"])
    step_key = state.phase
    attempt = store.begin_step_attempt(
        run_id,
        step_key,
        lease_owner=worker_id,
        lease_epoch=claimed["lease_epoch"],
    )
    assert attempt is not None
    outcome = await workflow(claimed, state)
    committed = store.commit_step(
        run_id,
        step_key,
        attempt=attempt,
        lease_owner=worker_id,
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=outcome,
    )
    return outcome, committed, state


@pytest.mark.asyncio
async def test_route_converse_tape_recovers_without_duplicate_model_call_or_final_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    state = _state()
    run = _create_run(store, state, content="say hello")
    converse_calls = 0

    async def scripted_route(_self: AgentOrchestrator, content: str, session_id: str, language: str) -> IntentPlan:
        assert (content, session_id, language) == ("say hello", "session-1", "zh")
        return IntentPlan(kind=IntentKind.CONVERSE, confidence=1.0, rationale="scripted tape")

    async def scripted_converse(
        self: AgentOrchestrator,
        plan: IntentPlan,
        session_id: str,
        content: str,
        language: str,
        on_update: Any,
    ) -> tuple[ChatMessage, None]:
        nonlocal converse_calls
        converse_calls += 1
        assert plan.kind == IntentKind.CONVERSE
        assert (session_id, content, language) == ("session-1", "say hello", "zh")
        message = ChatMessage(session_id=session_id, role="agent", sender="agent", content="hello")
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message, None

    monkeypatch.setattr(AgentOrchestrator, "_classify_intent", scripted_route)
    monkeypatch.setattr(AgentOrchestrator, "_handle_converse", scripted_converse)

    routed = await workflow(run, state)
    assert isinstance(routed, Continue)
    assert routed.next_phase == "converse"
    state.phase = routed.next_phase

    completed = await workflow(run, state)
    assert isinstance(completed, Succeeded)
    assert completed.result == {"message": "hello", "app_id": None}

    # Simulate a crash after the chat projection was persisted but before the
    # scheduler observed the terminal commit. Replaying the phase is idempotent.
    replayed = await workflow(run, AgentRunState.model_validate(state.model_dump(mode="json")))
    assert isinstance(replayed, Succeeded)
    assert replayed.result == completed.result
    assert converse_calls == 1
    messages = WorkspaceStorage(str(tmp_path)).get_messages("session-1")
    assert [(message.role, message.content, message.run_id) for message in messages] == [("agent", "hello", run["id"])]
    reply_event = next(event for event in completed.events if event.type == "reply")
    assert reply_event.payload["type"] == "reply"
    assert reply_event.payload["message"]["content"] == "hello"
    assert "schema_version" not in reply_event.payload
    assert "event_id" not in reply_event.payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_tokens", "max_cost_usd", "usage", "expected_tokens", "expected_cost"),
    [
        (10, None, {"total_tokens": 11}, 11, 0.0),
        (100, 0.10, {"input_tokens": 4, "output_tokens": 3, "cost_usd": 0.20}, 7, 0.20),
    ],
)
async def test_route_usage_is_checkpointed_and_budget_exhaustion_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    max_tokens: int,
    max_cost_usd: float | None,
    usage: dict[str, Any],
    expected_tokens: int,
    expected_cost: float,
) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    state = _state()
    state.budget.max_tokens = max_tokens
    state.budget.max_cost_usd = max_cost_usd
    run = _create_run(store, state, content="route this request")

    async def over_budget_response(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"content": "", "tool_calls": [], "usage": usage}

    monkeypatch.setattr("backend.agent.router.call_llm_api", over_budget_response)

    outcome, committed, executed_state = await _execute_fenced_step(
        store,
        workflow,
        run["id"],
        worker_id="worker-budget",
    )

    assert isinstance(outcome, Failed)
    assert outcome.error_code == "budget_exhausted"
    assert outcome.retryable is False
    assert executed_state.budget.model_turns == 1
    assert executed_state.budget.tokens_used == expected_tokens
    assert executed_state.budget.cost_usd == pytest.approx(expected_cost)
    assert committed["status"] == "failed"
    assert committed["state"]["last_error"]["code"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_route_checkpoints_content_addressed_context_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_CONTEXT_MAX_MESSAGES", "2")
    storage = WorkspaceStorage(str(tmp_path))
    storage.add(ChatSession(id="session-1", title="Summary Session"))
    for index in range(5):
        storage.add(
            ChatMessage(
                session_id="session-1",
                role="user" if index % 2 == 0 else "agent",
                content=f"durable history {index}",
            )
        )
    storage.commit()

    store = RunStore(str(tmp_path))
    workflow = _workflow(tmp_path, store, GraphDatabase(str(tmp_path)))
    run = _create_run(store, _state(), content="continue")

    async def scripted_route(_self: AgentOrchestrator, content: str, session_id: str, language: str) -> IntentPlan:
        assert _self.context_summary is not None
        assert "durable history 0" in _self.context_summary
        return IntentPlan(kind=IntentKind.CONVERSE, instruction=content)

    monkeypatch.setattr(AgentOrchestrator, "_classify_intent", scripted_route)
    outcome, committed, _ = await _execute_fenced_step(
        store,
        workflow,
        run["id"],
        worker_id="worker-summary",
    )

    assert isinstance(outcome, Continue)
    summary = committed["state"]["data"]["context_summary"]
    assert committed["state"]["context_summary_ref"] == (
        f"sha256:{hashlib.sha256(summary.encode('utf-8')).hexdigest()}"
    )


@pytest.mark.asyncio
async def test_graph_mutation_preflight_wait_resolve_and_atomic_commit(tmp_path: Path) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    intent = IntentPlan(
        kind=IntentKind.GRAPH_MUTATION,
        confidence=1.0,
        actions=[
            {
                "action": "create_node",
                "id": "task-1",
                "type": "Task",
                "properties": {"title": "Durable mutation", "status": "open"},
            }
        ],
    )
    run = _create_run(
        store,
        _state(phase="graph_preflight", workflow_type="graph_mutation", intent=intent),
    )

    waiting_outcome, waiting, _ = await _execute_fenced_step(store, workflow, run["id"], worker_id="worker-preflight")
    assert isinstance(waiting_outcome, Wait)
    assert waiting["status"] == "waiting_user"
    assert graph_db.get_node("task-1") is None
    interaction = store.get_interaction(waiting_outcome.interaction_id)
    assert interaction is not None
    assert interaction["status"] == "pending"
    assert interaction["payload"]["permission_type"] == "graph_mutation"

    resolved_interaction = store.resolve_interaction(
        interaction["id"],
        {"approved": True},
        expected_run_version=waiting["version"],
    )
    assert resolved_interaction["status"] == "resolved"
    assert store.get_run(run["id"])["status"] == "queued"

    approval_outcome, approved, _ = await _execute_fenced_step(store, workflow, run["id"], worker_id="worker-approval")
    assert isinstance(approval_outcome, Continue)
    assert approval_outcome.next_phase == "graph_commit"
    assert approved["state"]["pending_interaction_id"] is None
    assert graph_db.get_node("task-1") is None

    commit_outcome, committed, _ = await _execute_fenced_step(store, workflow, run["id"], worker_id="worker-commit")
    assert isinstance(commit_outcome, Succeeded)
    assert committed["status"] == "succeeded"
    assert committed["state"]["phase"] == "done"
    assert committed["result"]["ticket_id"].startswith("tkt-")
    assert graph_db.get_node("task-1")["properties"] == {
        "title": "Durable mutation",
        "status": "open",
    }
    assert len(graph_db.list_mutation_history("session-1")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", [False, "unknown-action"])
async def test_graph_mutation_denied_or_unknown_approval_fails_closed(tmp_path: Path, approval: bool | str) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    intent = IntentPlan(
        kind=IntentKind.GRAPH_MUTATION,
        actions=[
            {
                "action": "create_node",
                "id": "must-not-exist",
                "type": "Task",
                "properties": {"title": "Denied"},
            }
        ],
    )
    run = _create_run(
        store,
        _state(phase="graph_preflight", workflow_type="graph_mutation", intent=intent),
    )
    wait_outcome, waiting, _ = await _execute_fenced_step(store, workflow, run["id"], worker_id="worker-preflight")
    assert isinstance(wait_outcome, Wait)
    store.resolve_interaction(
        wait_outcome.interaction_id,
        {"approved": approval},
        expected_run_version=waiting["version"],
    )

    denied_outcome, failed, _ = await _execute_fenced_step(store, workflow, run["id"], worker_id="worker-denied")
    assert isinstance(denied_outcome, Failed)
    assert denied_outcome.error_code == "approval_denied"
    assert failed["status"] == "failed"
    assert graph_db.get_node("must-not-exist") is None


@pytest.mark.asyncio
async def test_widget_staging_does_not_touch_live_app_until_clean_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apps_dir = tmp_path / "apps"
    live_dir = apps_dir / "durable-app"
    live_dir.mkdir(parents=True)
    (live_dir / "controller.js").write_text("// old live controller", encoding="utf-8")
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    app_manager = FakeAppManager(apps_dir)
    promotion_calls: list[str] = []

    async def staged_runner(
        app_id: str,
        instruction: str,
        *,
        language: str,
        on_update: Any,
        promote: bool,
    ) -> OpenCodeStagedResult:
        assert app_id == "durable-app"
        assert "APPROVED DEVELOPMENT PLAN" in instruction
        assert "RECENT APP RUNTIME DIAGNOSTICS" in instruction
        assert "data_source_path_not_allowed" in instruction
        assert "Add the exact API path" in instruction
        assert language == "en"
        assert promote is False
        staging_dir = apps_dir / f".{app_id}.staging-{uuid.uuid4().hex}"
        staging_dir.mkdir()
        (staging_dir / "controller.js").write_text("// verified new controller", encoding="utf-8")
        _write_manifest(staging_dir, app_id, _runtime_contract(app_id))
        return OpenCodeStagedResult(
            output="generated safely",
            app_id=app_id,
            staging_dir=staging_dir,
            live_dir=live_dir,
        )

    def validate_staging(result: OpenCodeStagedResult) -> Path:
        controller = result.staging_dir / "controller.js"
        assert controller.is_file()
        return controller

    def promote_staging(result: OpenCodeStagedResult) -> Path:
        promotion_calls.append(result.app_id)
        if result.live_dir.exists():
            shutil.rmtree(result.live_dir)
        result.staging_dir.replace(result.live_dir)
        return result.live_dir

    async def clean_diff(**_kwargs: Any) -> VerificationDiff:
        return VerificationDiff()

    monkeypatch.setattr(durable_workflow_module, "validate_opencode_staging", validate_staging)
    monkeypatch.setattr(durable_workflow_module, "promote_opencode_staging", promote_staging)
    monkeypatch.setattr(durable_workflow_module.SchemaVerificationService, "diff", clean_diff)

    workflow = _workflow(
        tmp_path,
        store,
        graph_db,
        app_manager=app_manager,
        coding_agent_runner=staged_runner,
        app_diagnostic_loader=lambda app_id: [
            {
                "app_id": app_id,
                "code": "data_source_path_not_allowed",
                "message": "Path is not allowed",
                "hint": "Add the exact API path",
            }
        ],
    )
    intent = IntentPlan(
        kind=IntentKind.WIDGET_MODIFY,
        app_id="durable-app",
        instruction="Replace the controller",
    )
    state = _state(
        phase="stage_code",
        workflow_type="widget_modify",
        intent=intent,
        data={
            "language": "en",
            "approved_plan": "Implement and verify it",
            "approved_schema": {"reused_schemas": [], "new_schemas": [], "capabilities": []},
            "runtime_contract": _runtime_contract("durable-app"),
        },
    )
    run = _create_run(store, state, content="replace the app")

    staged = await workflow(run, state)
    assert isinstance(staged, Continue)
    assert staged.next_phase == "verify"
    assert promotion_calls == []
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == "// old live controller"
    assert Path(state.data["staged_app"]["staging_dir"]).is_dir()

    state.phase = "verify"
    verified = await workflow(run, state)
    assert isinstance(verified, Continue)
    assert verified.next_phase == "promote"
    assert promotion_calls == []
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == "// old live controller"

    state.phase = "promote"
    published = await workflow(run, state)
    assert isinstance(published, Succeeded)
    assert published.result["app_id"] == "durable-app"
    assert promotion_calls == ["durable-app"]
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == "// verified new controller"
    assert state.data["verification_report"] == "✅ Schema Verification PASSED"
    canvas = WorkspaceStorage(str(tmp_path)).get_canvas_config()
    assert canvas["open_app_ids"] == ["durable-app"]
    assert canvas["active_app_id"] == "durable-app"


@pytest.mark.asyncio
async def test_widget_v2_coordinator_e2e_resolves_durable_approvals_before_verified_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps_dir = tmp_path / "apps"
    live_dir = apps_dir / "durable-app"
    live_dir.mkdir(parents=True)
    old_controller = "// old live controller"
    new_controller = "// verified v2 controller"
    (live_dir / "index.html").write_text("<main>old</main>", encoding="utf-8")
    (live_dir / "style.css").write_text("main { color: gray; }", encoding="utf-8")
    (live_dir / "controller.js").write_text(old_controller, encoding="utf-8")

    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    app_manager = FakeAppManager(apps_dir)
    verification_entered = asyncio.Event()
    allow_verification = asyncio.Event()
    promotion_calls: list[str] = []
    generation_calls: list[str] = []

    async def scripted_plan(**kwargs: Any) -> str:
        generation_calls.append("plan")
        assert kwargs["app_id"] == "durable-app"
        assert kwargs["instruction"] == "Replace the live controller safely"
        return "Implement the v2 controller in isolated staging and verify it"

    async def scripted_schema(**kwargs: Any) -> dict[str, Any]:
        generation_calls.append("align_schema")
        assert kwargs["app_id"] == "durable-app"
        assert kwargs["approved_plan"] == "Implement the v2 controller in isolated staging and verify it"
        return {"reused_schemas": [], "new_schemas": []}

    async def staged_runner(
        app_id: str,
        instruction: str,
        *,
        language: str,
        on_update: Any,
        promote: bool,
    ) -> OpenCodeStagedResult:
        generation_calls.append("stage_code")
        assert app_id == "durable-app"
        assert "[APPROVED DEVELOPMENT PLAN]" in instruction
        assert "Implement the v2 controller in isolated staging and verify it" in instruction
        assert language == "en"
        assert promote is False
        assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_controller
        await on_update({"type": "opencode_progress", "message": "scripted staging complete"})
        staging_dir = apps_dir / f".{app_id}.staging-{uuid.uuid4().hex}"
        staging_dir.mkdir()
        (staging_dir / "controller.js").write_text(new_controller, encoding="utf-8")
        _write_manifest(staging_dir, app_id, _runtime_contract(app_id))
        assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_controller
        return OpenCodeStagedResult(
            output="scripted v2 staged output",
            app_id=app_id,
            staging_dir=staging_dir,
            live_dir=live_dir,
        )

    def validate_staging(result: OpenCodeStagedResult) -> Path:
        controller = result.staging_dir / "controller.js"
        assert controller.read_text(encoding="utf-8") == new_controller
        return controller

    async def clean_diff(**kwargs: Any) -> VerificationDiff:
        generation_calls.append("verify")
        assert kwargs["app_id"] == "durable-app"
        assert kwargs["widget_code"]["js"] == new_controller
        assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_controller
        assert promotion_calls == []
        verification_entered.set()
        await allow_verification.wait()
        return VerificationDiff()

    def promote_staging(result: OpenCodeStagedResult) -> Path:
        generation_calls.append("promote")
        assert allow_verification.is_set()
        assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_controller
        promotion_calls.append(result.app_id)
        shutil.rmtree(result.live_dir)
        result.staging_dir.replace(result.live_dir)
        return result.live_dir

    monkeypatch.setattr(
        durable_workflow_module.PlanGenerationService,
        "generate_plan",
        scripted_plan,
    )
    monkeypatch.setattr(
        durable_workflow_module.SchemaAlignmentService,
        "align_schemas",
        scripted_schema,
    )
    monkeypatch.setattr(durable_workflow_module, "validate_opencode_staging", validate_staging)
    monkeypatch.setattr(durable_workflow_module, "promote_opencode_staging", promote_staging)
    monkeypatch.setattr(durable_workflow_module.SchemaVerificationService, "diff", clean_diff)

    workflow = _workflow(
        tmp_path,
        store,
        graph_db,
        app_manager=app_manager,
        coding_agent_runner=staged_runner,
    )
    coordinator = RunCoordinator(store, SimpleNamespace(), app_manager, SimpleNamespace())
    coordinator.register_internal_agent_executor(workflow)
    intent = IntentPlan(
        kind=IntentKind.WIDGET_MODIFY,
        app_id="durable-app",
        instruction="Replace the live controller safely",
    )
    state = _state(
        session_id="widget-e2e-session",
        phase="plan",
        workflow_type="widget_modify",
        intent=intent,
        data={"language": "en", "trace_id": "trace-widget-v2-e2e"},
    )
    resolved_interactions: list[tuple[str, str]] = []

    async def resolve_approvals(run_id: str) -> None:
        expected_types = ["plan_approval", "schema_approval"]
        for expected_type in expected_types:
            for _ in range(500):
                current = store.get_run(run_id)
                assert current is not None
                pending = [item for item in current["interactions"] if item["status"] == "pending"]
                if current["status"] == "waiting_user" and pending:
                    interaction = pending[0]
                    assert interaction["type"] == expected_type
                    payload = interaction["payload"]
                    assert payload["app_id"] == "durable-app"
                    assert payload["type"] == f"{expected_type}_request"
                    assert payload["request_id"] == interaction["id"]
                    assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_controller
                    response: dict[str, Any] = {"approved": True}
                    if expected_type == "plan_approval":
                        response["plan"] = payload["plan"]
                    else:
                        response["proposal"] = payload["proposal"]
                    coordinator.resolve_interaction(
                        interaction["id"],
                        response,
                        expected_run_version=current["version"],
                    )
                    resolved_interactions.append((expected_type, interaction["id"]))
                    break
                if current["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
                    raise AssertionError(f"Widget Run terminated as {current['status']} before {expected_type}")
                await asyncio.sleep(0.005)
            else:
                raise AssertionError(f"Widget Run never requested {expected_type}")

    resolver_task: asyncio.Task[None] | None = None
    try:
        submitted = coordinator.submit_internal_agent(
            owner_id="session:widget-e2e-session",
            action_id="chat",
            title="Widget v2 E2E",
            session_id="widget-e2e-session",
            input_data={"content": "Replace the live controller safely"},
            workflow_type=state.workflow_type,
            workflow_version=state.workflow_version,
            state=state,
        )
        resolver_task = asyncio.create_task(resolve_approvals(submitted["id"]))
        await asyncio.wait_for(verification_entered.wait(), timeout=5)

        verifying = store.get_run(submitted["id"], include_events=True)
        assert verifying is not None
        assert verifying["status"] == "running"
        assert verifying["state"]["phase"] == "verify"
        assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_controller
        assert promotion_calls == []

        allow_verification.set()
        terminal = await coordinator.wait_terminal(submitted["id"], timeout=5)
        await asyncio.wait_for(resolver_task, timeout=1)
    finally:
        allow_verification.set()
        if resolver_task is not None and not resolver_task.done():
            resolver_task.cancel()
            await asyncio.gather(resolver_task, return_exceptions=True)
        await coordinator.shutdown()

    assert terminal["status"] == "succeeded"
    assert terminal["state"]["phase"] == "done"
    assert terminal["result"]["app_id"] == "durable-app"
    assert terminal["artifacts"] == [{"type": "app", "id": "durable-app"}]
    assert terminal["state"]["artifact_refs"][0]["id"] == "durable-app"
    assert promotion_calls == ["durable-app"]
    assert generation_calls == ["plan", "align_schema", "stage_code", "verify", "promote"]
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == new_controller
    assert not list(apps_dir.glob(".durable-app.staging-*"))

    completed = store.get_run(submitted["id"], include_events=True)
    assert completed is not None
    expected_phases = [
        "plan",
        "wait_plan",
        "align_schema",
        "wait_schema",
        "stage_code",
        "verify",
        "promote",
    ]
    assert [step["step_key"] for step in completed["steps"]] == expected_phases
    assert [(step["attempt"], step["status"]) for step in completed["steps"]] == [(1, "succeeded")] * len(
        expected_phases
    )
    assert [item[0] for item in resolved_interactions] == ["plan_approval", "schema_approval"]
    assert [item["type"] for item in completed["interactions"]] == [
        "plan_approval",
        "schema_approval",
    ]
    assert [item["status"] for item in completed["interactions"]] == ["resolved", "resolved"]

    events = completed["events"]
    step_started = [event for event in events if event["type"] == "step_started"]
    step_committed = [event for event in events if event["type"] == "step_committed"]
    assert [event["payload"]["step_key"] for event in step_started] == expected_phases
    assert [event["payload"]["step_key"] for event in step_committed] == expected_phases
    assert [event["payload"]["outcome"]["kind"] for event in step_committed] == [
        "wait",
        "continue",
        "wait",
        "continue",
        "continue",
        "continue",
        "succeeded",
    ]
    assert all(event["attempt"] == 1 for event in [*step_started, *step_committed])
    assert all(event["trace_id"] == "trace-widget-v2-e2e" for event in [*step_started, *step_committed])
    assert [event["payload"]["type"] for event in events if event["type"] == "interaction_requested"] == [
        "plan_approval",
        "schema_approval",
    ]
    assert [event["type"] for event in events if event["type"].endswith("_approval_request")] == [
        "plan_approval_request",
        "schema_approval_request",
    ]
    assert len([event for event in events if event["type"] == "interaction_resolved"]) == 2
    terminal_status = [event for event in events if event["type"] == "status_changed"][-1]
    assert terminal_status["payload"]["to"] == "succeeded"


@pytest.mark.asyncio
async def test_multi_intent_preflight_rejects_later_invalid_step_before_any_effect(tmp_path: Path) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    intent = IntentPlan(
        kind=IntentKind.MULTI_INTENT,
        sub_intents=[
            SubIntent(
                kind=SubIntentKind.GRAPH_MUTATION,
                actions=[
                    {
                        "action": "create_node",
                        "id": "must-never-commit",
                        "type": "Task",
                        "properties": {"title": "preflight"},
                    }
                ],
            ),
            SubIntent(
                kind=SubIntentKind.GRAPH_MUTATION,
                actions=[{"action": "delete_node", "id": "missing-node"}],
            ),
        ],
    )
    state = _state(phase="multi_preflight", workflow_type="multi_intent", intent=intent)
    run = _create_run(store, state)

    outcome = await workflow(run, state)

    assert isinstance(outcome, Failed)
    assert graph_db.get_node("must-never-commit") is None
    assert state.data.get("effects_committed") is not True


@pytest.mark.asyncio
async def test_multi_intent_denial_compensates_prior_graph_effect(tmp_path: Path) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    intent = IntentPlan(
        kind=IntentKind.MULTI_INTENT,
        sub_intents=[
            SubIntent(
                kind=SubIntentKind.GRAPH_MUTATION,
                actions=[
                    {
                        "action": "create_node",
                        "id": "saga-node-1",
                        "type": "Task",
                        "properties": {"title": "compensate me"},
                    }
                ],
            ),
            SubIntent(
                kind=SubIntentKind.GRAPH_MUTATION,
                actions=[
                    {
                        "action": "create_node",
                        "id": "saga-node-2",
                        "type": "Task",
                        "properties": {"title": "deny me"},
                    }
                ],
            ),
        ],
    )
    run = _create_run(
        store,
        _state(phase="multi_preflight", workflow_type="multi_intent", intent=intent),
    )

    first_wait: Wait | None = None
    second_wait: Wait | None = None
    committed_first = False
    for turn in range(20):
        outcome, current, _ = await _execute_fenced_step(
            store,
            workflow,
            run["id"],
            worker_id=f"worker-{turn}",
        )
        if isinstance(outcome, Wait):
            if first_wait is None:
                first_wait = outcome
                store.resolve_interaction(
                    outcome.interaction_id,
                    {"approved": True},
                    expected_run_version=current["version"],
                )
            else:
                second_wait = outcome
                store.resolve_interaction(
                    outcome.interaction_id,
                    {"approved": False},
                    expected_run_version=current["version"],
                )
        if graph_db.get_node("saga-node-1") is not None:
            committed_first = True
        if current["status"] == "failed":
            break
    else:
        raise AssertionError("multi-intent saga did not terminate")

    assert first_wait is not None and second_wait is not None
    assert committed_first is True
    assert current["status"] == "failed"
    assert graph_db.get_node("saga-node-1") is None
    assert graph_db.get_node("saga-node-2") is None
    assert current["state"]["data"]["effects_committed"] is False
    assert current["state"]["phase"] == "route"
    assert current["state"]["intent"] is None
    assert "multi_index" not in current["state"]["data"]
    assert "multi_results" not in current["state"]["data"]


@pytest.mark.asyncio
async def test_retryable_saga_failure_keeps_prior_effect_and_compensation(tmp_path: Path) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    mutation = graph_db.apply_actions_atomic(
        [
            {
                "action": "create_node",
                "id": "retry-saga-node",
                "type": "Task",
                "properties": {"title": "keep until terminal failure"},
            }
        ]
    )
    state = _state(
        phase="verify",
        workflow_type="multi_intent",
        intent=IntentPlan(kind=IntentKind.MULTI_INTENT),
        data={
            "effects_committed": True,
            "graph_compensations": [{"ticket_id": mutation["ticket_id"], "actions": mutation["reverse_actions"]}],
            "multi_index": 1,
            "multi_results": [{"message": "committed"}],
        },
    )

    retry = await workflow._failure(
        state,
        code="temporary",
        message="try again",
        retryable=True,
        effect_state="none",
    )

    assert retry.retryable is True
    assert graph_db.get_node("retry-saga-node") is not None
    assert state.data["multi_index"] == 1
    assert state.data["multi_results"] == [{"message": "committed"}]
    assert state.data["graph_compensations"]


@pytest.mark.asyncio
async def test_shutdown_requeues_checkpoint_without_compensating_live_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_GRACE_SECONDS", "0.1")
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    mutation = graph_db.apply_actions_atomic(
        [
            {
                "action": "create_node",
                "id": "shutdown-saga-node",
                "type": "Task",
                "properties": {"title": "must survive worker shutdown"},
            }
        ]
    )
    started = asyncio.Event()

    async def block_phase(_run: dict[str, Any], _state: AgentRunState) -> Continue:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(workflow, "_phase_graph_commit", block_phase)
    state = _state(
        phase="graph_commit",
        workflow_type=IntentKind.MULTI_INTENT.value,
        intent=IntentPlan(kind=IntentKind.MULTI_INTENT),
        data={
            "effects_committed": True,
            "graph_compensations": [{"ticket_id": mutation["ticket_id"], "actions": mutation["reverse_actions"]}],
            "multi_index": 1,
            "multi_results": [{"message": "committed"}],
        },
    )
    coordinator = RunCoordinator(store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    coordinator.register_internal_agent_executor(workflow)
    await coordinator.start()
    run = coordinator.submit_internal_agent(
        owner_id="ambient-agent:session-1",
        action_id="chat",
        title="Agent task",
        session_id="session-1",
        input_data={"content": "continue saga"},
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
        state=state,
    )
    await asyncio.wait_for(started.wait(), timeout=2)

    await coordinator.shutdown()

    released = store.get_run(run["id"])
    assert released is not None
    assert released["status"] == "queued"
    assert released["steps"][0]["status"] == "interrupted"
    assert graph_db.get_node("shutdown-saga-node") is not None
    assert released["state"]["data"]["effects_committed"] is True
    assert released["state"]["data"]["multi_index"] == 1
    assert released["state"]["data"]["multi_results"] == [{"message": "committed"}]
    assert released["state"]["data"]["graph_compensations"] == [
        {"ticket_id": mutation["ticket_id"], "actions": mutation["reverse_actions"]}
    ]


@pytest.mark.asyncio
async def test_explicit_cancel_still_compensates_inflight_durable_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RunStore(str(tmp_path))
    graph_db = GraphDatabase(str(tmp_path))
    workflow = _workflow(tmp_path, store, graph_db)
    mutation = graph_db.apply_actions_atomic(
        [
            {
                "action": "create_node",
                "id": "cancel-saga-node",
                "type": "Task",
                "properties": {"title": "explicit cancel compensates"},
            }
        ]
    )
    started = asyncio.Event()

    async def block_phase(_run: dict[str, Any], _state: AgentRunState) -> Continue:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(workflow, "_phase_graph_commit", block_phase)
    state = _state(
        phase="graph_commit",
        workflow_type=IntentKind.MULTI_INTENT.value,
        intent=IntentPlan(kind=IntentKind.MULTI_INTENT),
        data={
            "effects_committed": True,
            "graph_compensations": [{"ticket_id": mutation["ticket_id"], "actions": mutation["reverse_actions"]}],
        },
    )
    coordinator = RunCoordinator(store, SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    coordinator.register_internal_agent_executor(workflow)
    await coordinator.start()
    try:
        run = coordinator.submit_internal_agent(
            owner_id="ambient-agent:session-1",
            action_id="chat",
            title="Agent task",
            session_id="session-1",
            input_data={"content": "cancel saga"},
            workflow_type=state.workflow_type,
            workflow_version=state.workflow_version,
            state=state,
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        assert coordinator.cancel(run["id"])["status"] == "cancel_requested"
        completed = await coordinator.wait_terminal(run["id"], timeout=2)
    finally:
        await coordinator.shutdown()

    assert completed["status"] == "cancelled"
    assert graph_db.get_node("cancel-saga-node") is None
    assert completed["state"]["data"]["effects_committed"] is False
    assert completed["state"]["data"]["graph_compensations"] == []
