import asyncio
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app_store import CapabilityAction, CapabilityInvocation
from backend.run_service import (
    AgentRunState,
    Cancelled,
    Continue,
    Failed,
    PendingRunEvent,
    RunBudget,
    RunCoordinator,
    RunStore,
    RunVersionConflict,
    StaleLeaseError,
    Succeeded,
    Wait,
)


def create(store: RunStore, **overrides):
    values = {
        "owner_id": "mcp:acme:calendar",
        "action_id": "run",
        "action_title": "Run",
        "source_type": "user",
        "source_id": None,
        "adapter_type": "mcp_tool",
        "runtime_id": "calendar-backend",
        "tool_name": "run",
        "input_data": {"value": 1},
    }
    values.update(overrides)
    return store.create_run(**values)


def test_run_state_machine_idempotency_and_retry_history(tmp_path):
    store = RunStore(str(tmp_path))
    first = create(store, idempotency_key="stable")
    duplicate = create(store, idempotency_key="stable")
    assert duplicate["id"] == first["id"]
    with pytest.raises(ValueError, match="different input or correlation"):
        create(store, idempotency_key="stable", input_data={"value": 2})

    claimed = store.claim_next("worker-a", global_limit=4, owner_limit=1)
    assert claimed["status"] == "running"
    completed = store.transition(first["id"], "succeeded", summary="done", result={"ok": True})
    assert completed["progress"] == 1
    assert completed["result"] == {"ok": True}
    with pytest.raises(ValueError, match="invalid run transition"):
        store.transition(first["id"], "running")


def test_run_correlation_is_durable_and_part_of_idempotency_identity(tmp_path):
    store = RunStore(str(tmp_path))
    correlation = {"projection_type": "capability_call_response", "call_id": "call-1"}
    run = create(store, idempotency_key="call-1", correlation=correlation)
    restored = store.get_run(run["id"], include_events=True)

    assert restored["correlation"] == correlation
    assert restored["events"][0]["payload"]["correlation"] == correlation
    with pytest.raises(ValueError, match="different input or correlation"):
        create(
            store,
            idempotency_key="call-1",
            correlation={"projection_type": "capability_call_response", "call_id": "call-2"},
        )


def test_event_envelope_redacts_secrets_and_bounds_payloads(tmp_path):
    store = RunStore(str(tmp_path))
    run = create(store)

    store.append_event(
        run["id"],
        "adapter_debug",
        {"authorization": "Bearer should-not-leak", "nested": {"token": "secret"}, "text": "x" * 70_000},
    )

    event = store.get_run(run["id"], include_events=True)["events"][-1]
    assert event["event_id"]
    assert event["stream_epoch"]
    assert event["redacted"] == 1
    assert event["payload"]["authorization"] == "[REDACTED]"
    assert event["payload"]["nested"]["token"] == "[REDACTED]"
    assert "should-not-leak" not in str(event["payload"])
    assert "TRUNCATED" in event["payload"]["text"]


def test_bounded_claims_skip_busy_owner_and_keep_fifo(tmp_path):
    store = RunStore(str(tmp_path))
    first = create(store, owner_id="app:a")
    second = create(store, owner_id="app:a", action_id="second")
    other = create(store, owner_id="app:b")

    assert store.claim_next("worker", 4, 1)["id"] == first["id"]
    assert store.claim_next("worker", 4, 1)["id"] == other["id"]
    assert store.get_run(second["id"])["status"] == "queued"


def test_recovery_is_safe_and_interactions_are_persistent(tmp_path):
    store = RunStore(str(tmp_path))
    safe = create(store, status="running", recovery="restart_safe", action_id="safe")
    opaque = create(store, status="running", recovery="manual", action_id="opaque")
    store.recover_orphaned("new-worker")
    assert store.get_run(safe["id"])["status"] == "queued"
    assert store.get_run(opaque["id"])["status"] == "needs_attention"

    waiting = create(store, status="running", action_id="wait")
    interaction = store.create_interaction(waiting["id"], "permission", "Allow?", {"scope": "mail"})
    store.transition(waiting["id"], "waiting_user")
    resolved = store.resolve_interaction(interaction["id"], {"approved": True})
    assert resolved["response"] == {"approved": True}
    assert store.get_run(waiting["id"])["interactions"][0]["status"] == "resolved"


@pytest.mark.asyncio
async def test_coordinator_executes_mcp_action_and_records_step(tmp_path):
    action = CapabilityAction(
        id="calculate",
        title="Calculate",
        input_schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}},
        invocation=CapabilityInvocation(type="mcp_tool", app_id="math-backend", tool_name="calculate"),
        recovery="restart_safe",
    )

    class Catalog:
        def get_action(self, catalog_id, action_id):
            return action if (catalog_id, action_id) == ("mcp:acme:math", "calculate") else None

    class Apps:
        def get_manifest(self, app_id):
            return SimpleNamespace(mcp_server={"command": ["math"], "args": []}) if app_id == "math-backend" else None

    client = SimpleNamespace(call=AsyncMock(return_value={"answer": 4}))
    backend = SimpleNamespace(
        is_mcp_approved=lambda *args: True,
        get_or_start_mcp_client=AsyncMock(return_value=client),
        is_agent_approved=lambda *args: True,
    )
    store = RunStore(str(tmp_path))
    coordinator = RunCoordinator(store, Catalog(), Apps(), backend)
    await coordinator.start()
    try:
        run = coordinator.submit("mcp:acme:math", "calculate", {"x": 2})
        completed = await coordinator.wait_terminal(run["id"], timeout=2)
    finally:
        await coordinator.shutdown()

    assert completed["status"] == "succeeded"
    # A remote manifest assertion is not an enforceable idempotency contract.
    assert completed["recovery"] == "manual"
    assert completed["result"] == {"answer": 4}
    assert completed["steps"][0]["status"] == "succeeded"
    client.call.assert_awaited_once_with("tools/call", {"name": "calculate", "arguments": {"x": 2}})


@pytest.mark.asyncio
async def test_coordinator_persists_permission_wait_without_occupying_queue(tmp_path):
    action = CapabilityAction(
        id="send",
        title="Send",
        input_schema={"type": "object"},
        invocation=CapabilityInvocation(type="mcp_tool", app_id="mail-backend", tool_name="send"),
    )

    class Catalog:
        def get_action(self, *_args):
            return action

    class Apps:
        def get_manifest(self, _app_id):
            return SimpleNamespace(mcp_server={"command": ["mail"], "args": []})

    backend = SimpleNamespace(is_mcp_approved=lambda *args: False, is_agent_approved=lambda *args: True)
    store = RunStore(str(tmp_path))
    coordinator = RunCoordinator(store, Catalog(), Apps(), backend)
    await coordinator.start()
    try:
        run = coordinator.submit("mcp:acme:mail", "send", {})
        for _ in range(40):
            waiting = store.get_run(run["id"])
            if waiting["status"] == "waiting_user":
                break
            await asyncio.sleep(0.01)
    finally:
        await coordinator.shutdown()

    assert waiting["status"] == "waiting_user"
    assert waiting["interactions"][0]["type"] == "permission"


@pytest.mark.asyncio
async def test_direct_mcp_resource_uses_durable_permission_and_resumes(tmp_path):
    class Catalog:
        def get_action(self, *_args):
            return None

    class Apps:
        def get_manifest(self, app_id):
            return (
                SimpleNamespace(mcp_server={"command": ["mcp-server"], "args": ["--stdio"]})
                if app_id == "docs"
                else None
            )

    approved = False
    client = SimpleNamespace(call=AsyncMock(return_value={"contents": [{"text": "ok"}]}))

    def approve_mcp(*_args):
        nonlocal approved
        approved = True

    backend = SimpleNamespace(
        is_mcp_approved=lambda *_args: approved,
        approve_mcp=approve_mcp,
        is_agent_approved=lambda *_args: True,
        get_or_start_mcp_client=AsyncMock(return_value=client),
    )
    events: list[dict] = []

    async def mirror(payload):
        events.append(payload)

    store = RunStore(str(tmp_path))
    coordinator = RunCoordinator(store, Catalog(), Apps(), backend)
    await coordinator.start()
    try:
        run = coordinator.submit_direct_mcp_request(
            "docs",
            "resources/read",
            {"uri": "doc://one"},
            source_type="chat",
            source_id="session-1",
            event_callback=mirror,
        )
        for _ in range(100):
            waiting = store.get_run(run["id"])
            if waiting["status"] == "waiting_user":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("MCP request never entered waiting_user")

        interaction = waiting["interactions"][0]
        assert waiting["lease_owner"] is None
        assert interaction["payload"]["request_id"] == interaction["id"]
        assert interaction["payload"]["run_version"] == waiting["version"]
        assert events[-1]["type"] == "backend_permission_request"

        coordinator.resolve_interaction(
            interaction["id"],
            {"approved": True, "run_version": waiting["version"]},
        )
        completed = await coordinator.wait_terminal(run["id"], timeout=2)
    finally:
        await coordinator.shutdown()

    assert completed["status"] == "succeeded"
    assert completed["result"] == {"contents": [{"text": "ok"}]}
    client.call.assert_awaited_once_with("resources/read", {"uri": "doc://one"})


@pytest.mark.asyncio
async def test_cancelling_running_agent_message_with_unknown_effect_needs_attention(tmp_path):
    class Catalog:
        def get_action(self, *_args):
            return None

    class Apps:
        def get_manifest(self, app_id):
            return SimpleNamespace(agent_url="https://agent.invalid/events") if app_id == "remote" else None

    started = asyncio.Event()

    async def handle_agent_message(_app_id, _manifest, _message, _emit):
        started.set()
        await asyncio.Event().wait()

    backend = SimpleNamespace(
        is_agent_approved=lambda *_args: True,
        is_mcp_approved=lambda *_args: True,
        handle_agent_message=handle_agent_message,
    )
    store = RunStore(str(tmp_path))
    coordinator = RunCoordinator(store, Catalog(), Apps(), backend)
    await coordinator.start()
    try:
        run = coordinator.submit_direct_agent_message(
            "remote",
            {"messages": [{"role": "user", "content": "act"}]},
            source_type="chat",
            source_id="session-1",
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        coordinator.cancel(run["id"])
        completed = await coordinator.wait_terminal(run["id"], timeout=2)
    finally:
        await coordinator.shutdown()

    assert completed["status"] == "needs_attention"
    assert completed["steps"][0]["status"] == "cancelled"
    assert completed["checkpoint"]["outcome"]["effect_state"] == "unknown"


@pytest.mark.asyncio
async def test_cancelling_internal_step_with_effect_in_flight_needs_attention(tmp_path):
    class Catalog:
        def get_action(self, *_args):
            return None

    started = asyncio.Event()

    async def reducer(_run, state):
        state.data["effect_in_flight"] = "graph_atomic_commit"
        started.set()
        await asyncio.Event().wait()

    store = RunStore(str(tmp_path))
    coordinator = RunCoordinator(store, Catalog(), SimpleNamespace(), SimpleNamespace())
    coordinator.register_internal_agent_executor(reducer)
    await coordinator.start()
    try:
        run = coordinator.submit_internal_agent(
            owner_id="ambient-agent:session-1",
            action_id="chat",
            title="Agent task",
            session_id="session-1",
            input_data={"content": "commit"},
            state=AgentRunState(
                workflow_type="graph_mutation",
                workflow_version=2,
                session_id="session-1",
                phase="graph_commit",
            ),
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        coordinator.cancel(run["id"])
        completed = await coordinator.wait_terminal(run["id"], timeout=2)
    finally:
        await coordinator.shutdown()

    assert completed["status"] == "needs_attention"
    assert completed["state"]["data"]["effect_in_flight"] == "graph_atomic_commit"
    assert completed["checkpoint"]["outcome"]["effect_state"] == "unknown"


def test_needs_attention_requires_durable_effect_reconciliation(tmp_path):
    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="multi_intent",
        workflow_version=2,
        session_id="session-1",
        phase="wait_schema",
        data={
            "effects_committed": True,
            "graph_compensations": [{"ticket_id": "ticket-1", "actions": []}],
        },
    )
    run = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        source_type="chat",
        source_id="session-1",
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )

    attention = store.request_cancel(run["id"])
    assert attention["status"] == "needs_attention"
    attention_with_events = store.get_run(run["id"], include_events=True)
    assert attention_with_events["events"][-1]["payload"]["effect_state"] == "unknown"
    with pytest.raises(ValueError, match="effect reconciliation"):
        store.request_cancel(run["id"])

    reconciled = store.reconcile_effect(
        run["id"],
        "confirmed_not_committed",
        note="remote ledger has no matching operation",
    )
    assert reconciled["status"] == "failed"
    assert reconciled["error"]["effect_state"] == "none"
    assert reconciled["error"]["reconciliation"] == "confirmed_not_committed"
    assert reconciled["state"]["data"]["effects_committed"] is False
    assert reconciled["state"]["data"]["graph_compensations"] == []
    reconciled_with_events = store.get_run(run["id"], include_events=True)
    assert any(event["type"] == "effect_reconciled" for event in reconciled_with_events["events"])


def test_confirmed_committed_effect_cannot_be_retried(tmp_path):
    class Catalog:
        def get_action(self, *_args):
            return None

    store = RunStore(str(tmp_path))
    run = create(store, status="needs_attention", recovery="manual")
    reconciled = store.reconcile_effect(run["id"], "confirmed_committed")
    coordinator = RunCoordinator(store, Catalog(), SimpleNamespace(), SimpleNamespace())

    assert reconciled["error"]["effect_state"] == "committed"
    with pytest.raises(ValueError, match="committed external effect"):
        coordinator.retry(run["id"])


def test_agent_retry_gets_a_fresh_active_time_window_and_keeps_usage_counters(tmp_path):
    class Catalog:
        def get_action(self, *_args):
            return None

    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="widget_create",
        workflow_version=2,
        session_id="session-1",
        phase="stage_code",
        budget=RunBudget(model_turns=5, tokens_used=7_033, cost_usd=0.75),
        data={"active_seconds": 266.9, "approved_plan": "Build the app"},
        last_error={"code": "verifier_unavailable", "message": "Node.js is missing", "effect_state": "none"},
    )
    run = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    failed = store.transition(
        run["id"],
        "failed",
        error={"code": "verifier_unavailable", "message": "Node.js is missing", "effect_state": "none"},
    )
    coordinator = RunCoordinator(store, Catalog(), SimpleNamespace(), SimpleNamespace())

    retried = coordinator.retry(failed["id"])

    assert retried["state"]["data"]["active_seconds"] == 0.0
    assert retried["state"]["data"]["approved_plan"] == "Build the app"
    assert retried["state"]["budget"]["model_turns"] == 5
    assert retried["state"]["budget"]["tokens_used"] == 7_033
    assert retried["state"]["budget"]["cost_usd"] == 0.75


def test_widget_retry_without_retained_staging_restarts_code_generation(tmp_path):
    class Catalog:
        def get_action(self, *_args):
            return None

    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="widget_create",
        workflow_version=2,
        session_id="session-1",
        phase="verify",
        budget=RunBudget(model_turns=5, tokens_used=7_957),
        data={
            "active_seconds": 312.6,
            "approved_plan": "Build the weather app",
            "approved_schema": {"reused_schemas": [{"id": "Place"}]},
            "verification_report": "stale report",
            "verification_options": ["rework_code"],
            "verification_passed": False,
            "verification_override": True,
            "code_feedback": "stale feedback",
        },
        last_error={
            "code": "budget_exhausted",
            "message": "Active time exhausted",
            "effect_state": "none",
        },
    )
    run = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    failed = store.transition(
        run["id"],
        "failed",
        error={"code": "budget_exhausted", "message": "Active time exhausted", "effect_state": "none"},
    )
    coordinator = RunCoordinator(store, Catalog(), SimpleNamespace(), SimpleNamespace())

    retried = coordinator.retry(failed["id"])

    assert retried["state"]["phase"] == "stage_code"
    assert retried["state"]["data"]["active_seconds"] == 0.0
    assert retried["state"]["data"]["approved_plan"] == "Build the weather app"
    assert retried["state"]["data"]["approved_schema"] == {"reused_schemas": [{"id": "Place"}]}
    for stale_key in (
        "verification_report",
        "verification_options",
        "verification_passed",
        "verification_override",
        "code_feedback",
    ):
        assert stale_key not in retried["state"]["data"]


def test_agent_state_and_step_outcomes_are_strictly_serializable():
    defaults = RunBudget()
    assert defaults.max_tokens == 64_000
    assert defaults.max_cost_usd == 5.0
    state = AgentRunState(
        workflow_type="widget",
        workflow_version=2,
        session_id="session-1",
        phase="plan",
        budget=RunBudget(max_model_turns=4, max_tokens=10_000),
        artifact_refs=[{"kind": "staging_app", "id": "app-1"}],
        data={"approved_plan": "build a chart", "multi_intent_cursor": 2},
    )

    restored = AgentRunState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert Continue(next_phase="verify").model_dump()["kind"] == "continue"
    assert Wait(interaction_id="request-1").model_dump()["kind"] == "wait"
    assert Succeeded(result={"ok": True}).model_dump()["kind"] == "succeeded"
    assert Failed(error_code="timeout", message="timed out").model_dump()["kind"] == "failed"
    assert Cancelled().model_dump()["kind"] == "cancelled"


def test_schema_migration_preserves_legacy_rows_and_adds_attempt_dimension(tmp_path):
    state_dir = tmp_path / ".ambient"
    state_dir.mkdir()
    db_path = state_dir / "runs.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE runs (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, action_id TEXT NOT NULL,
                action_title TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT,
                adapter_type TEXT NOT NULL, runtime_id TEXT NOT NULL, tool_name TEXT,
                input_json TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                summary TEXT NOT NULL DEFAULT '', result_json TEXT, error_json TEXT,
                checkpoint_json TEXT, artifacts_json TEXT NOT NULL DEFAULT '[]',
                recovery TEXT NOT NULL DEFAULT 'manual', parent_run_id TEXT, retry_of TEXT,
                idempotency_key TEXT, attempt INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                finished_at TEXT, lease_owner TEXT, lease_expires_at TEXT
            );
            CREATE TABLE run_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE run_interactions (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, type TEXT NOT NULL,
                prompt TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                response_json TEXT, created_at TEXT NOT NULL, resolved_at TEXT
            );
            CREATE TABLE run_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                step_key TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
                output_json TEXT, started_at TEXT, finished_at TEXT, UNIQUE(run_id, step_key)
            );
            """
        )
        values = (
            "legacy-run",
            "ambient-agent:session-1",
            "chat",
            "Agent task",
            "chat",
            "session-1",
            "internal",
            "internal:agent",
            None,
            "{}",
            "waiting_user",
            0,
            "",
            None,
            None,
            None,
            "[]",
            "manual",
            None,
            None,
            None,
            1,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            None,
            "old-worker",
            "2026-01-01T01:00:00+00:00",
        )
        connection.execute(
            f"INSERT INTO runs VALUES ({','.join('?' for _ in values)})",
            values,
        )
        replay_values = (
            "replayable-run",
            "ambient-agent:session-2",
            "chat",
            "Agent task",
            "chat",
            "session-2",
            "internal",
            "internal:agent",
            None,
            json.dumps({"content": "resume me", "sender": "user", "user_message_id": 7}),
            "queued",
            0,
            "",
            None,
            None,
            None,
            "[]",
            "manual",
            None,
            None,
            None,
            1,
            "2026-01-01T00:00:01+00:00",
            "2026-01-01T00:00:01+00:00",
            None,
            None,
            None,
            None,
        )
        connection.execute(
            f"INSERT INTO runs VALUES ({','.join('?' for _ in replay_values)})",
            replay_values,
        )
        connection.execute(
            """INSERT INTO run_steps(run_id, step_key, status, attempt, started_at)
               VALUES ('legacy-run', 'plan', 'failed', 1, '2026-01-01T00:00:00+00:00')"""
        )

    store = RunStore(str(tmp_path))
    migrated = store.get_run("legacy-run", include_events=True)
    assert migrated["status"] == "needs_attention"
    assert migrated["workflow_type"] == "legacy"
    assert migrated["workflow_version"] == 1
    assert migrated["lease_epoch"] == 0
    assert migrated["version"] == 2
    assert migrated["steps"][0]["attempt"] == 1
    assert migrated["events"][-1]["type"] == "migration_attention_required"
    assert migrated["events"][-1]["event_id"]
    assert migrated["events"][-1]["schema_version"] == 1
    assert migrated["events"][-1]["stream_epoch"]
    assert migrated["events"][-1]["trace_id"] == "legacy-run"

    replayable = store.get_run("replayable-run", include_events=True)
    assert replayable["status"] == "queued"
    assert replayable["adapter_type"] == "internal_agent"
    assert replayable["recovery"] == "restart_safe"
    assert replayable["workflow_type"] == "agent_chat"
    assert replayable["workflow_version"] == 2
    assert replayable["state"]["phase"] == "route"
    assert replayable["state"]["session_id"] == "session-2"
    assert replayable["state"]["data"] == {
        "workspace_dir": str(tmp_path.resolve()),
        "user_message_id": 7,
    }
    assert replayable["events"][-1]["type"] == "migration_replayable_upgraded"

    with sqlite3.connect(db_path) as connection:
        step_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='run_steps'"
        ).fetchone()[0]
    assert "UNIQUE(run_id, step_key, attempt)" in step_sql


def test_fenced_atomic_commit_rejects_old_worker_and_records_step_attempts(tmp_path):
    store = RunStore(str(tmp_path))
    initial_state = AgentRunState(workflow_type="widget", session_id="session-1", phase="plan")
    run = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        source_type="chat",
        source_id="session-1",
        state=initial_state,
        workflow_type="widget",
    )
    first_claim = store.claim_next("worker-a", 4, 1, lease_seconds=-1)
    assert first_claim["id"] == run["id"]
    first_attempt = store.begin_step_attempt(
        run["id"],
        "plan",
        lease_owner="worker-a",
        lease_epoch=first_claim["lease_epoch"],
    )
    assert first_attempt == 1

    assert store.recover_orphaned() == 1
    second_claim = store.claim_next("worker-b", 4, 1)
    second_attempt = store.begin_step_attempt(
        run["id"],
        "plan",
        lease_owner="worker-b",
        lease_epoch=second_claim["lease_epoch"],
    )
    assert second_attempt == 2
    with pytest.raises(StaleLeaseError):
        store.commit_step(
            run["id"],
            "plan",
            attempt=first_attempt,
            lease_owner="worker-a",
            lease_epoch=first_claim["lease_epoch"],
            state=initial_state,
            outcome=Succeeded(result={"stale": True}),
        )

    next_state = initial_state.model_copy(update={"phase": "done"})
    completed = store.commit_step(
        run["id"],
        "plan",
        attempt=second_attempt,
        lease_owner="worker-b",
        lease_epoch=second_claim["lease_epoch"],
        state=next_state,
        outcome=Succeeded(summary="done", result={"ok": True}),
    )
    assert completed["status"] == "succeeded"
    assert completed["state"]["phase"] == "done"
    assert [step["attempt"] for step in completed["steps"]] == [1, 2]
    assert completed["steps"][0]["status"] == "interrupted"
    assert completed["steps"][1]["status"] == "succeeded"
    committed_event = next(
        event for event in store.get_run(run["id"], include_events=True)["events"] if event["type"] == "step_committed"
    )
    assert committed_event["duration_ms"] is not None
    assert committed_event["model_usage"] == {"model_turns": 0, "tokens": 0, "cost_usd": 0.0}


def test_wait_interaction_and_projection_events_commit_atomically_with_fencing(tmp_path):
    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="widget_modify",
        workflow_version=2,
        session_id="session-1",
        phase="wait_plan",
    )
    run = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        source_type="chat",
        source_id="session-1",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    stale_claim = store.claim_next("worker-a", 1, 1, lease_seconds=-1)
    stale_attempt = store.begin_step_attempt(
        run["id"],
        "wait_plan",
        lease_owner="worker-a",
        lease_epoch=stale_claim["lease_epoch"],
    )
    wait = Wait(
        interaction_id="plan-approval-1",
        interaction_type="plan_approval",
        interaction_prompt="Approve plan",
        interaction_payload={"type": "plan_approval_request", "request_id": "plan-approval-1"},
        events=[
            PendingRunEvent(
                type="plan_approval_request",
                payload={"type": "plan_approval_request", "request_id": "plan-approval-1"},
            )
        ],
    )

    assert store.recover_orphaned() == 1
    with pytest.raises(StaleLeaseError):
        store.commit_step(
            run["id"],
            "wait_plan",
            attempt=stale_attempt,
            lease_owner="worker-a",
            lease_epoch=stale_claim["lease_epoch"],
            state=state,
            outcome=wait,
        )
    assert store.get_interaction("plan-approval-1") is None
    assert not any(event["type"] == "plan_approval_request" for event in store.events_after(0))

    fresh_claim = store.claim_next("worker-b", 1, 1)
    fresh_attempt = store.begin_step_attempt(
        run["id"],
        "wait_plan",
        lease_owner="worker-b",
        lease_epoch=fresh_claim["lease_epoch"],
    )
    waiting = store.commit_step(
        run["id"],
        "wait_plan",
        attempt=fresh_attempt,
        lease_owner="worker-b",
        lease_epoch=fresh_claim["lease_epoch"],
        state=state,
        outcome=wait,
    )
    assert waiting["status"] == "waiting_user"
    assert store.get_interaction("plan-approval-1")["status"] == "pending"
    committed_types = [event["type"] for event in store.get_run(run["id"], include_events=True)["events"]]
    assert "interaction_requested" in committed_types
    assert "plan_approval_request" in committed_types
    assert "step_committed" in committed_types


def test_session_lane_is_fifo_and_waiting_run_keeps_ownership(tmp_path):
    store = RunStore(str(tmp_path))
    first = create(
        store,
        owner_id="session-owner",
        action_id="first",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        source_type="chat",
        source_id="session-1",
        state=AgentRunState(session_id="session-1"),
    )
    second = create(
        store,
        owner_id="session-owner",
        action_id="second",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        source_type="chat",
        source_id="session-1",
        state=AgentRunState(session_id="session-1"),
    )
    other = create(
        store,
        owner_id="other-owner",
        action_id="other",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        source_type="chat",
        source_id="session-2",
        state=AgentRunState(session_id="session-2"),
    )

    claimed = store.claim_next("worker", 4, 4)
    assert claimed["id"] == first["id"]
    interaction = store.create_interaction(first["id"], "approval", "Continue?", {})
    waiting = store.transition(first["id"], "waiting_user")
    assert waiting["lease_owner"] is None

    assert store.claim_next("worker", 4, 4)["id"] == other["id"]
    assert store.get_run(second["id"])["status"] == "queued"
    assert store.claim_next("worker", 4, 4) is None

    resolved = store.resolve_interaction(
        interaction["id"],
        {"approved": True},
        expected_run_version=waiting["version"],
    )
    assert resolved["status"] == "resolved"
    assert store.claim_next("worker", 4, 4)["id"] == first["id"]


def test_interaction_version_and_cancellation_are_atomic(tmp_path):
    store = RunStore(str(tmp_path))
    run = create(store)
    claimed = store.claim_next("worker", 4, 1)
    interaction = store.create_interaction(run["id"], "approval", "Continue?", {})
    waiting = store.transition(
        run["id"],
        "waiting_user",
        expected_lease_owner="worker",
        expected_lease_epoch=claimed["lease_epoch"],
    )

    with pytest.raises(RunVersionConflict):
        store.resolve_interaction(
            interaction["id"],
            {"approved": True},
            expected_run_version=waiting["version"] - 1,
        )
    assert store.get_interaction(interaction["id"])["status"] == "pending"
    assert store.get_run(run["id"])["status"] == "waiting_user"

    cancelled = store.request_cancel(run["id"])
    assert cancelled["status"] == "cancelled"
    assert store.get_interaction(interaction["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="already resolved"):
        store.resolve_interaction(interaction["id"], {"approved": True})


@pytest.mark.parametrize("cancel_from", ["queued", "waiting_user"])
def test_cancelling_inactive_widget_run_discards_staging_without_publishing(
    tmp_path,
    cancel_from,
):
    apps_dir = tmp_path / "apps"
    live_dir = apps_dir / "cancel-app"
    staging_dir = apps_dir / ".cancel-app.staging-00000000000000000000000000000001"
    live_dir.mkdir(parents=True)
    staging_dir.mkdir()
    (live_dir / "controller.js").write_text("// old live controller", encoding="utf-8")
    (staging_dir / "controller.js").write_text("// unverified staged controller", encoding="utf-8")

    store = RunStore(str(tmp_path))
    step_key = "stage_code" if cancel_from == "queued" else "verify"
    state = AgentRunState(
        workflow_type="widget_modify",
        workflow_version=2,
        session_id=f"session-{cancel_from}",
        phase=step_key,
        data={
            "staged_app": {
                "output": "unverified output",
                "app_id": "cancel-app",
                "staging_dir": str(staging_dir),
                "live_dir": str(live_dir),
            },
            "verification_passed": False,
        },
    )
    run = create(
        store,
        action_id=f"cancel-{cancel_from}",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        source_type="chat",
        source_id=state.session_id,
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    claimed = store.claim_next("staging-worker", 1, 1)
    assert claimed is not None and claimed["id"] == run["id"]
    attempt = store.begin_step_attempt(
        run["id"],
        step_key,
        lease_owner="staging-worker",
        lease_epoch=claimed["lease_epoch"],
    )
    assert attempt == 1
    if cancel_from == "queued":
        outcome = Continue(next_phase="verify", summary="Staged App generated")
    else:
        state.phase = "wait_override"
        outcome = Wait(
            interaction_id=f"verification-{cancel_from}",
            interaction_type="verification_approval",
            interaction_prompt="Resolve verification findings",
            interaction_payload={"type": "verification_approval_request"},
        )
    inactive = store.commit_step(
        run["id"],
        step_key,
        attempt=attempt,
        lease_owner="staging-worker",
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=outcome,
    )
    assert inactive["status"] == cancel_from
    assert inactive["checkpoint"]["state"]["data"]["staged_app"]["staging_dir"] == str(staging_dir)
    assert staging_dir.is_dir()

    cancelled = store.request_cancel(run["id"])
    cancelled_with_events = store.get_run(run["id"], include_events=True)

    assert cancelled["status"] == "cancelled"
    assert not staging_dir.exists()
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == "// old live controller"
    assert "staged_app" not in cancelled["state"]["data"]
    assert "staged_app" not in cancelled["checkpoint"]["state"]["data"]
    assert cancelled["artifacts"] == []
    assert [
        event["payload"] for event in cancelled_with_events["events"] if event["type"] == "staged_artifact_discarded"
    ] == [{"app_id": "cancel-app", "reason": "run_cancelled"}]
    assert not any(event["type"] == "widget" for event in cancelled_with_events["events"])
    if cancel_from == "waiting_user":
        assert cancelled["interactions"][0]["status"] == "cancelled"


@pytest.mark.parametrize("deleted_before_restart", [False, True])
def test_staging_cancel_tombstone_recovers_both_crash_windows(
    tmp_path,
    monkeypatch,
    deleted_before_restart,
):
    apps_dir = tmp_path / "apps"
    live_dir = apps_dir / "crash-app"
    staging_dir = apps_dir / ".crash-app.staging-11111111111111111111111111111111"
    live_dir.mkdir(parents=True)
    staging_dir.mkdir()
    (live_dir / "controller.js").write_text("// live", encoding="utf-8")
    (staging_dir / "controller.js").write_text("// staged", encoding="utf-8")

    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="widget_modify",
        workflow_version=2,
        session_id="session-crash-cleanup",
        phase="stage_code",
        data={
            "staged_app": {
                "output": "staged output",
                "app_id": "crash-app",
                "staging_dir": str(staging_dir),
                "live_dir": str(live_dir),
            }
        },
    )
    run = create(
        store,
        action_id="cancel-crash-window",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        source_type="chat",
        source_id=state.session_id,
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    claimed = store.claim_next("cleanup-worker", 1, 1)
    attempt = store.begin_step_attempt(
        run["id"],
        "stage_code",
        lease_owner="cleanup-worker",
        lease_epoch=claimed["lease_epoch"],
    )
    store.commit_step(
        run["id"],
        "stage_code",
        attempt=attempt,
        lease_owner="cleanup-worker",
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=Continue(next_phase="verify", summary="staged"),
    )

    original_recovery = store.recover_pending_staging_cleanup
    recovery_calls = 0

    def stop_after_tombstone(run_id=None):
        nonlocal recovery_calls
        recovery_calls += 1
        if recovery_calls == 1:
            return original_recovery(run_id)
        raise RuntimeError("simulated process stop after tombstone commit")

    monkeypatch.setattr(store, "recover_pending_staging_cleanup", stop_after_tombstone)
    with pytest.raises(RuntimeError, match="simulated process stop"):
        store.request_cancel(run["id"])

    pending = store.get_run(run["id"])
    assert pending["status"] == "cancel_requested"
    assert "staged_app" not in pending["state"]["data"]
    assert pending["state"]["data"]["staged_app_cleanup_pending"]["staging_dir"] == str(staging_dir)
    assert "staged_app" not in pending["checkpoint"]["state"]["data"]
    assert "staged_app_cleanup_pending" in pending["checkpoint"]["state"]["data"]

    if deleted_before_restart:
        (staging_dir / "controller.js").unlink()
        staging_dir.rmdir()

    recovered_store = RunStore(str(tmp_path))
    recovered = recovered_store.get_run(run["id"], include_events=True)
    assert recovered["status"] == "cancelled"
    assert not staging_dir.exists()
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == "// live"
    assert "staged_app_cleanup_pending" not in recovered["state"]["data"]
    assert "staged_app_cleanup_pending" not in recovered["checkpoint"]["state"]["data"]
    assert [
        event["type"]
        for event in recovered["events"]
        if event["type"].startswith("staged_artifact_cleanup") or event["type"] == "staged_artifact_discarded"
    ][-2:] == ["staged_artifact_cleanup_requested", "staged_artifact_discarded"]


def test_unsafe_staging_cleanup_stays_attention_and_blocks_reconciliation(tmp_path):
    apps_dir = tmp_path / "apps"
    live_dir = apps_dir / "unsafe-app"
    unsafe_staging = tmp_path / "outside-staging"
    live_dir.mkdir(parents=True)
    unsafe_staging.mkdir()
    (live_dir / "controller.js").write_text("// live", encoding="utf-8")
    (unsafe_staging / "controller.js").write_text("// must remain", encoding="utf-8")
    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="widget_modify",
        workflow_version=2,
        session_id="session-unsafe-cleanup",
        phase="verify",
        data={
            "staged_app": {
                "app_id": "unsafe-app",
                "staging_dir": str(unsafe_staging),
                "live_dir": str(live_dir),
            }
        },
    )
    run = create(
        store,
        action_id="unsafe-cleanup",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        source_type="chat",
        source_id=state.session_id,
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )

    attention = store.request_cancel(run["id"])

    assert attention["status"] == "needs_attention"
    assert attention["error"]["code"] == "staged_artifact_cleanup_failed"
    assert unsafe_staging.is_dir()
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == "// live"
    assert "staged_app" in attention["state"]["data"]
    assert "staged_app_cleanup_pending" not in attention["state"]["data"]
    with pytest.raises(ValueError, match="cleanup must complete"):
        store.reconcile_effect(run["id"], "confirmed_not_committed")


def test_cancel_detects_artifact_promoted_before_checkpoint(tmp_path):
    apps_dir = tmp_path / "apps"
    live_dir = apps_dir / "promoted-app"
    staging_dir = apps_dir / ".promoted-app.staging-22222222222222222222222222222222"
    live_dir.mkdir(parents=True)
    controller = live_dir / "controller.js"
    controller.write_text("export default function App() { return null; }\n", encoding="utf-8")
    (live_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": "promoted-app",
                "title": "Promoted App",
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": [],
                "capabilities": [],
            }
        ),
        encoding="utf-8",
    )
    store = RunStore(str(tmp_path))
    state = AgentRunState(
        workflow_type="widget_modify",
        workflow_version=2,
        session_id="session-promoted-cancel",
        phase="promote",
        data={
            "verification_passed": True,
            "staged_app": {
                "app_id": "promoted-app",
                "staging_dir": str(staging_dir),
                "live_dir": str(live_dir),
            },
        },
    )
    run = create(
        store,
        action_id="cancel-promoted",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        source_type="chat",
        source_id=state.session_id,
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    (live_dir / ".ambient-promotion.json").write_text(
        json.dumps(
            {
                "run_id": run["id"],
                "artifact_hash": hashlib.sha256(controller.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    attention = store.request_cancel(run["id"])

    assert attention["status"] == "needs_attention"
    assert attention["error"]["code"] == "cancel_after_artifact_promotion"
    assert attention["error"]["effect_state"] == "committed"
    assert attention["state"]["data"]["effects_committed"] is True
    assert attention["state"]["data"]["non_compensable_effect"] is True
    assert "staged_app_cleanup_pending" not in attention["state"]["data"]
    assert controller.is_file()

    reconciled = store.reconcile_effect(run["id"], "confirmed_committed")
    assert reconciled["status"] == "failed"
    assert reconciled["error"]["effect_state"] == "committed"


def test_unknown_effect_outcomes_require_attention(tmp_path):
    store = RunStore(str(tmp_path))
    state = AgentRunState(session_id="session-1", phase="execute")
    run = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        source_type="chat",
        source_id="session-1",
        state=state,
    )
    claimed = store.claim_next("worker", 4, 1)
    attempt = store.begin_step_attempt(run["id"], "execute", lease_owner="worker", lease_epoch=claimed["lease_epoch"])
    result = store.commit_step(
        run["id"],
        "execute",
        attempt=attempt,
        lease_owner="worker",
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=Failed(
            error_code="external_disconnect",
            message="result is unknown",
            effect_state="unknown",
        ),
    )
    assert result["status"] == "needs_attention"
    assert result["finished_at"] is None
    assert result["error"]["effect_state"] == "unknown"


def test_cancel_fences_late_commit_and_handles_prestart_race(tmp_path):
    store = RunStore(str(tmp_path))
    state = AgentRunState(session_id="session-1", phase="execute")
    run = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        recovery="restart_safe",
        source_type="chat",
        source_id="session-1",
        state=state,
    )
    claimed = store.claim_next("worker", 4, 1)
    attempt = store.begin_step_attempt(run["id"], "execute", lease_owner="worker", lease_epoch=claimed["lease_epoch"])
    assert store.request_cancel(run["id"])["status"] == "cancel_requested"
    with pytest.raises(StaleLeaseError):
        store.commit_step(
            run["id"],
            "execute",
            attempt=attempt,
            lease_owner="worker",
            lease_epoch=claimed["lease_epoch"],
            state=state,
            outcome=Succeeded(result={"too_late": True}),
        )
    interrupted = store.finalize_cancel_requested(run["id"], "worker")
    assert interrupted["status"] == "needs_attention"
    assert interrupted["steps"][0]["status"] == "interrupted"

    prestart = create(store, action_id="prestart", recovery="restart_safe")
    store.claim_next("worker", 4, 1)
    store.request_cancel(prestart["id"])
    assert store.finalize_cancel_requested(prestart["id"], "worker")["status"] == "cancelled"


@pytest.mark.asyncio
async def test_scheduler_executes_internal_agent_and_gracefully_requeues(tmp_path):
    class Catalog:
        def get_action(self, *_args):
            return None

    coordinator = RunCoordinator(RunStore(str(tmp_path)), Catalog(), SimpleNamespace(), SimpleNamespace())

    async def reducer(_run, state):
        return Succeeded(summary="complete", result={"phase": state.phase})

    coordinator.register_internal_agent_executor(reducer)
    await coordinator.start()
    try:
        run = coordinator.submit_internal_agent(
            owner_id="ambient-agent:session-1",
            action_id="chat",
            title="Agent task",
            session_id="session-1",
            input_data={"content": "hello"},
        )
        completed = await coordinator.wait_terminal(run["id"], timeout=2)
    finally:
        await coordinator.shutdown()

    assert completed["status"] == "succeeded"
    assert completed["adapter_type"] == "internal_agent"
    assert completed["result"] == {"phase": "route"}


@pytest.mark.asyncio
async def test_shutdown_requeues_inflight_restart_safe_agent(tmp_path, monkeypatch):
    class Catalog:
        def get_action(self, *_args):
            return None

    monkeypatch.setenv("RUNNER_SHUTDOWN_GRACE_SECONDS", "0.1")
    store = RunStore(str(tmp_path))
    coordinator = RunCoordinator(store, Catalog(), SimpleNamespace(), SimpleNamespace())
    started = asyncio.Event()

    async def reducer(_run, _state):
        started.set()
        await asyncio.Event().wait()

    coordinator.register_internal_agent_executor(reducer)
    await coordinator.start()
    run = coordinator.submit_internal_agent(
        owner_id="ambient-agent:session-1",
        action_id="chat",
        title="Agent task",
        session_id="session-1",
        input_data={"content": "hello"},
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    await coordinator.shutdown()

    released = store.get_run(run["id"])
    assert released["status"] == "queued"
    assert released["lease_owner"] is None
    assert released["steps"][0]["status"] == "interrupted"


def test_legacy_internal_is_excluded_but_internal_agent_is_claimable(tmp_path):
    store = RunStore(str(tmp_path))
    legacy = create(store, adapter_type="internal", runtime_id="internal:agent", action_id="legacy")
    durable = create(
        store,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        action_id="durable",
        recovery="restart_safe",
        state=AgentRunState(),
    )

    assert store.claim_next("worker", 4, 4)["id"] == durable["id"]
    assert store.get_run(legacy["id"])["status"] == "queued"
