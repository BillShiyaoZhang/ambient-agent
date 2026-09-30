"""Conditional compensation contract for both graph adapters."""

import os
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntent, SubIntentKind
from backend.capabilities.models import RuntimeContract
from backend.graph_db import GraphDatabase
from backend.run_service import AgentRunState, Failed, RunStore, Wait


@pytest.fixture(params=["sqlite", "neo4j"])
def graph(request, tmp_path):
    if request.param == "sqlite":
        yield GraphDatabase(str(tmp_path))
        return
    uri = os.getenv("AMBIENT_TEST_NEO4J_URI")
    if not uri or os.getenv("AMBIENT_TEST_NEO4J_ISOLATED") != "1":
        pytest.skip("requires an explicitly isolated AMBIENT_TEST_NEO4J_URI")
    from neo4j import GraphDatabase as Driver

    from backend.neo4j_graph_db import Neo4jGraphDatabase

    driver = Driver.driver(uri, auth=("neo4j", os.environ["AMBIENT_TEST_NEO4J_PASSWORD"]))
    # This gate must target a disposable test server, never a user's graph.
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
    db = Neo4jGraphDatabase(driver, workspace_dir=str(tmp_path))
    try:
        yield db
    finally:
        db.close()


def compensate(db, effect, key="undo-a"):
    return db.apply_actions_atomic(
        effect["reverse_actions"], expected_state=effect.get("compensation_guard"), idempotency_key=key
    )


def test_compensation_preserves_concurrent_different_field_edit(graph):
    graph.create_node("a", "Task", {"title": "Before", "status": "pending"})
    effect = graph.apply_actions_atomic([{"action": "update_node_property", "id": "a", "properties": {"title": "A"}}])
    graph.update_node_property("a", {"status": "completed"})
    with pytest.raises(ValueError, match="compensation conflict"):
        compensate(graph, effect)
    assert graph.get_node("a")["properties"] == {"title": "A", "status": "completed"}


def test_compensation_is_atomic_and_detects_new_incident_edges(graph):
    effect = graph.apply_actions_atomic(
        [
            {"action": "create_node", "id": "a", "type": "Task", "properties": {"title": "A"}},
            {"action": "create_node", "id": "b", "type": "Task", "properties": {"title": "B"}},
        ]
    )
    graph.create_edge("a", "b", "LINKS", {"owner": "independent"})
    with pytest.raises(ValueError, match="compensation conflict"):
        compensate(graph, effect)
    assert graph.get_node("a") and graph.get_node("b")
    assert len(graph.get_edges("a")) == 1


def test_compensation_without_conflict_and_idempotent_recovery(graph):
    graph.create_node("a", "Task", {"title": "Before"})
    effect = graph.apply_actions_atomic([{"action": "update_node_property", "id": "a", "properties": {"title": "A"}}])
    result = compensate(graph, effect)
    assert graph.get_node("a")["properties"]["title"] == "Before"
    graph.update_node_property("a", {"title": "Later"})
    assert compensate(graph, effect) == result
    assert graph.get_node("a")["properties"]["title"] == "Later"


def test_compensation_of_edge_rejects_changed_edge_properties(graph):
    graph.create_node("a", "Task", {"title": "A"})
    graph.create_node("b", "Task", {"title": "B"})
    effect = graph.apply_actions_atomic(
        [{"action": "create_edge", "from_id": "a", "to_id": "b", "type": "LINKS", "properties": {"owner": "A"}}]
    )
    graph.create_edge("a", "b", "LINKS", {"owner": "B"})
    with pytest.raises(ValueError, match="compensation conflict"):
        compensate(graph, effect)
    assert graph.get_edges("a")[0]["properties"] == {"owner": "B"}


def proposal(field):
    return {"reused_schemas": [{"id": "Task", "extended_properties": {field: "string"}}], "new_schemas": []}


def test_schema_restore_preserves_independent_approved_extension(graph):
    effect = graph.apply_schema_proposal_atomic(proposal("audit_a"), idempotency_key="schema-a")
    graph.apply_schema_proposal_atomic(proposal("audit_b"), idempotency_key="schema-b")
    graph.create_node("b", "Task", {"title": "B", "audit_b": "retained"})
    with pytest.raises(ValueError, match="compensation conflict"):
        graph.restore_schema_snapshot(effect["snapshot"], idempotency_key="schema-a")
    assert "audit_a" in graph.get_schema("Task")["properties"]
    assert "audit_b" in graph.get_schema("Task")["properties"]
    graph.update_node_property("b", {"title": "Still valid"})


def test_schema_restore_without_conflict_and_duplicate_recovery(graph):
    effect = graph.apply_schema_proposal_atomic(proposal("audit_a"), idempotency_key="schema-a")
    graph.restore_schema_snapshot(effect["snapshot"], idempotency_key="schema-a")
    assert "audit_a" not in graph.get_schema("Task")["properties"]
    graph.apply_schema_proposal_atomic(proposal("audit_b"), idempotency_key="schema-b")
    graph.restore_schema_snapshot(effect["snapshot"], idempotency_key="schema-a")
    assert "audit_b" in graph.get_schema("Task")["properties"]


def test_schema_restore_refuses_removing_new_entity_in_use(graph):
    effect = graph.apply_schema_proposal_atomic(
        {
            "reused_schemas": [],
            "new_schemas": [
                {"id": "AuditItem", "name": "Audit item", "properties": {"title": "string"}, "subclass_of": "Thing"}
            ],
        },
        idempotency_key="schema-new",
    )
    graph.create_node("a", "AuditItem", {"title": "In use"})
    with pytest.raises(ValueError, match="compensation conflict"):
        graph.restore_schema_snapshot(effect["snapshot"], idempotency_key="schema-new")
    assert graph.get_schema("AuditItem") and graph.get_node("a")


def test_schema_restore_refuses_removing_new_field_in_use(graph):
    effect = graph.apply_schema_proposal_atomic(proposal("audit_a"), idempotency_key="schema-a")
    graph.create_node("a", "Task", {"title": "A", "audit_a": "retained"})
    with pytest.raises(ValueError, match="compensation conflict"):
        graph.restore_schema_snapshot(effect["snapshot"], idempotency_key="schema-a")
    assert graph.get_node("a")["properties"]["audit_a"] == "retained"
    graph.update_node_property("a", {"title": "Still valid"})


def test_schema_restore_removes_unused_parent_and_child_from_same_effect(graph):
    effect = graph.apply_schema_proposal_atomic(
        {
            "reused_schemas": [],
            "new_schemas": [
                {"id": "AuditParent", "name": "Parent", "properties": {"title": "string"}, "subclass_of": "Thing"},
                {"id": "AuditChild", "name": "Child", "properties": {"title": "string"}, "subclass_of": "AuditParent"},
            ],
        },
        idempotency_key="schema-tree",
    )
    graph.restore_schema_snapshot(effect["snapshot"], idempotency_key="schema-tree")
    assert graph.get_schema("AuditParent") is None
    assert graph.get_schema("AuditChild") is None


def test_schema_commit_revalidates_parent_after_preflight(graph, monkeypatch):
    parent_effect = graph.apply_schema_proposal_atomic(
        {
            "reused_schemas": [],
            "new_schemas": [
                {"id": "AuditParent", "name": "Parent", "properties": {"title": "string"}, "subclass_of": "Thing"},
            ],
        },
        idempotency_key="schema-parent",
    )
    original = graph._validate_proposal_parents

    def validate_then_compensate(normalized):
        original(normalized)
        graph.restore_schema_snapshot(parent_effect["snapshot"], idempotency_key="schema-parent")

    monkeypatch.setattr(graph, "_validate_proposal_parents", validate_then_compensate)
    with pytest.raises(ValueError, match="missing parent"):
        graph.apply_schema_proposal_atomic(
            {
                "reused_schemas": [],
                "new_schemas": [
                    {
                        "id": "AuditChild",
                        "name": "Child",
                        "properties": {"title": "string"},
                        "subclass_of": "AuditParent",
                    },
                ],
            },
            idempotency_key="schema-child",
        )
    assert graph.get_schema("AuditParent") is None
    assert graph.get_schema("AuditChild") is None


def test_cleanup_retains_inflight_promotion_evidence(tmp_path, monkeypatch):
    import backend.agent.durable_workflow as workflow_module

    graph = GraphDatabase(str(tmp_path))
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=SimpleNamespace(),
        app_manager=SimpleNamespace(),
        graph_db=graph,
        llm_config_store=SimpleNamespace(),
        coding_agent_runner=None,
    )
    discarded = []
    monkeypatch.setattr(workflow, "_staged_result", lambda staged: staged)
    monkeypatch.setattr(workflow_module, "discard_coding_agent_staging", discarded.append)
    state = AgentRunState(data={"staged_app": {"app_id": "a"}, "effect_in_flight": "app_atomic_promote"})
    workflow.cleanup_state(state)
    assert state.data["staged_app"] == {"app_id": "a"}
    assert state.data["effect_in_flight"] == "app_atomic_promote"
    assert discarded == []


@pytest.mark.asyncio
async def test_legacy_checkpoint_without_guard_preserves_effect_for_reconciliation(tmp_path):
    graph = GraphDatabase(str(tmp_path))
    effect = graph.apply_actions_atomic(
        [{"action": "create_node", "id": "a", "type": "Task", "properties": {"title": "Retained"}}]
    )
    state = AgentRunState(
        data={
            "effects_committed": True,
            "graph_compensations": [{"ticket_id": effect["ticket_id"], "actions": effect["reverse_actions"]}],
        }
    )
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=SimpleNamespace(),
        app_manager=SimpleNamespace(),
        graph_db=graph,
        llm_config_store=SimpleNamespace(),
        coding_agent_runner=None,
    )
    failed = await workflow._failure(
        state, code="approval_denied", message="Denied", retryable=False, effect_state="none"
    )
    assert failed.effect_state == "unknown"
    assert failed.error_code == "graph_compensation_conflict"
    assert graph.get_node("a") is not None
    assert state.data["graph_compensations"]


def test_standalone_updates_merge_inside_write_transaction(graph, monkeypatch):
    import backend.graph_db as sqlite_module
    import backend.neo4j_graph_db as neo4j_module

    graph.create_node("a", "Task", {"title": "Before", "status": "pending"})
    started = threading.Event()
    second_done = threading.Event()
    original = sqlite_module.coerce_entity_properties

    def coerce(schema, properties):
        if properties.get("title") == "A":
            started.set()
            # An unlocked writer can commit B while A still holds its stale read.
            # A serialized writer instead completes B after this bounded wait.
            second_done.wait(timeout=1)
        return original(schema, properties)

    monkeypatch.setattr(sqlite_module, "coerce_entity_properties", coerce)
    monkeypatch.setattr(neo4j_module, "coerce_entity_properties", coerce)

    def update_b():
        assert started.wait(timeout=3)
        try:
            graph.update_node_property("a", {"status": "completed"})
        finally:
            second_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(graph.update_node_property, "a", {"title": "A"})
        second = pool.submit(update_b)
        first.result(timeout=6)
        second.result(timeout=6)
    assert graph.get_node("a")["properties"] == {"title": "A", "status": "completed"}


def test_standalone_schema_registration_merges_inside_write_transaction(graph, monkeypatch):
    started = threading.Event()
    second_done = threading.Event()
    original = graph._merge_entity_properties

    def merge(schema_id, old, new):
        if "audit_a" in new:
            started.set()
            second_done.wait(timeout=1)
        return original(schema_id, old, new)

    monkeypatch.setattr(graph, "_merge_entity_properties", merge)

    def register(field):
        return graph.register_schema("Task", "Task", "Task", {field: "string"})

    def register_b():
        assert started.wait(timeout=3)
        try:
            register("audit_b")
        finally:
            second_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(register, "audit_a")
        second = pool.submit(register_b)
        first.result(timeout=6)
        second.result(timeout=6)
    assert {"audit_a", "audit_b"} <= graph.get_schema("Task")["properties"].keys()


@pytest.mark.asyncio
async def test_workflow_conflict_retains_evidence_and_unknown_effect(tmp_path):
    graph = GraphDatabase(str(tmp_path))
    graph.create_node("a", "Task", {"title": "Before", "status": "pending"})
    effect = graph.apply_actions_atomic(
        [{"action": "update_node_property", "id": "a", "properties": {"title": "A"}}], session_id="s"
    )
    graph.update_node_property("a", {"status": "completed"})
    state = AgentRunState(
        session_id="s",
        data={
            "effects_committed": True,
            "graph_compensations": [
                {
                    "ticket_id": effect["ticket_id"],
                    "actions": effect["reverse_actions"],
                    "guard": effect.get("compensation_guard"),
                }
            ],
        },
    )
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=SimpleNamespace(),
        app_manager=SimpleNamespace(),
        graph_db=graph,
        llm_config_store=SimpleNamespace(),
        coding_agent_runner=None,
    )
    failed = await workflow._failure(
        state, code="approval_denied", message="Denied", retryable=False, effect_state="none"
    )
    assert failed.effect_state == "unknown"
    assert failed.error_code == "graph_compensation_conflict"
    assert state.data["graph_compensations"]
    assert graph.get_node("a")["properties"]["status"] == "completed"


def create_workflow_run(tmp_path, graph, state):
    store = RunStore(str(tmp_path))
    workflow = DurableAgentWorkflow(
        workspace_dir=str(tmp_path),
        run_store=store,
        app_manager=SimpleNamespace(apps_dir=tmp_path / "apps"),
        graph_db=graph,
        llm_config_store=SimpleNamespace(resolve=lambda selection: None),
        coding_agent_runner=None,
    )
    run = store.create_run(
        owner_id="session:audit",
        action_id="chat",
        action_title="Audit",
        source_type="chat",
        source_id="audit",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    return store, workflow, run


async def commit_workflow_step(store, workflow, run, worker):
    claimed = store.claim_next(worker, global_limit=4, owner_limit=1)
    assert claimed and claimed["id"] == run["id"]
    state = AgentRunState.model_validate(claimed["state"])
    step_key = state.phase
    attempt = store.begin_step_attempt(run["id"], step_key, lease_owner=worker, lease_epoch=claimed["lease_epoch"])
    outcome = await workflow(claimed, state)
    current = store.commit_step(
        run["id"],
        step_key,
        attempt=attempt,
        lease_owner=worker,
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=outcome,
    )
    return outcome, current


@pytest.mark.asyncio
async def test_actual_multi_intent_denial_preserves_concurrent_commit(graph, tmp_path):
    graph.create_node("shared-task", "Task", {"title": "Original", "status": "pending"})
    intent = IntentPlan(
        kind=IntentKind.MULTI_INTENT,
        sub_intents=[
            SubIntent(
                kind=SubIntentKind.GRAPH_MUTATION,
                actions=[{"action": "update_node_property", "id": "shared-task", "properties": {"title": "Run A"}}],
            ),
            SubIntent(
                kind=SubIntentKind.GRAPH_MUTATION,
                actions=[
                    {"action": "create_node", "id": "second-task", "type": "Task", "properties": {"title": "Denied"}}
                ],
            ),
        ],
    )
    state = AgentRunState(
        session_id="audit",
        phase="multi_preflight",
        workflow_type="multi_intent",
        workflow_version=DurableAgentWorkflow.VERSION,
        intent=intent.to_dict(),
        model_snapshot={"primary": {"provider_id": "mock", "model_id": "mock"}},
    )
    store, workflow, run = create_workflow_run(tmp_path, graph, state)
    approvals = 0
    for turn in range(20):
        outcome, current = await commit_workflow_step(store, workflow, run, f"worker-{turn}")
        if isinstance(outcome, Wait):
            approvals += 1
            if approvals == 2:
                graph.apply_actions_atomic(
                    [{"action": "update_node_property", "id": "shared-task", "properties": {"status": "completed"}}],
                    idempotency_key="independent-b",
                )
            store.resolve_interaction(
                outcome.interaction_id, {"approved": approvals == 1}, expected_run_version=current["version"]
            )
        if current["status"] == "needs_attention":
            break
    else:
        pytest.fail("The conflicting saga did not enter needs_attention")
    assert graph.get_node("shared-task")["properties"] == {"title": "Run A", "status": "completed"}
    assert graph.get_node("second-task") is None
    assert current["state"]["data"]["graph_compensations"]


@pytest.mark.asyncio
async def test_actual_publication_failure_preserves_independent_schema_commit(graph, tmp_path, monkeypatch):
    import backend.agent.durable_workflow as workflow_module

    apps = tmp_path / "apps"
    staging = apps / (".audit-one.staging-" + "a" * 32)
    staging.mkdir(parents=True)
    controller = staging / "controller.js"
    controller.write_text("export default function App() { return null; }", encoding="utf-8")
    approved_schema = proposal("audit_a")
    contract = RuntimeContract.create(
        app_id="audit-one", schemas=graph.effective_schemas(approved_schema), capabilities=[]
    ).to_dict()
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": "audit-one",
                "title": "Audit",
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": [schema["id"] for schema in contract["schemas"]],
                "capabilities": [],
            }
        ),
        encoding="utf-8",
    )
    state = AgentRunState(
        session_id="audit",
        phase="promote",
        workflow_type="widget_create",
        workflow_version=DurableAgentWorkflow.VERSION,
        intent=IntentPlan(kind=IntentKind.WIDGET_CREATE, app_id="audit-one", instruction="Audit").to_dict(),
        model_snapshot={"primary": {"provider_id": "mock", "model_id": "mock"}},
        data={
            "verification_passed": True,
            "approved_schema": approved_schema,
            "runtime_contract": contract,
            "staged_app": {"app_id": "audit-one", "staging_dir": str(staging), "live_dir": str(apps / "audit-one")},
        },
    )
    store, workflow, run = create_workflow_run(tmp_path, graph, state)

    def fail_after_independent_commit(result):
        graph.apply_schema_proposal_atomic(proposal("audit_b"), idempotency_key="independent-schema-b")
        graph.create_node("independent-task", "Task", {"title": "B", "audit_b": "retained"})
        raise OSError("Publication failed after the independent transaction committed")

    monkeypatch.setattr(workflow_module, "validate_coding_agent_staging", lambda result: controller)
    monkeypatch.setattr(workflow_module, "promote_coding_agent_staging", fail_after_independent_commit)
    outcome, current = await commit_workflow_step(store, workflow, run, "publication-worker")
    assert isinstance(outcome, Failed)
    assert outcome.error_code == "schema_compensation_conflict"
    assert current["status"] == "needs_attention"
    assert "audit_b" in graph.get_schema("Task")["properties"]
    assert current["state"]["data"]["schema_snapshot"]
    graph.update_node_property("independent-task", {"title": "Still valid"})
