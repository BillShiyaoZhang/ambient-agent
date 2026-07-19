import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app_store import CapabilityAction, CapabilityInvocation
from backend.run_service import RunCoordinator, RunStore


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

    claimed = store.claim_next("worker-a", global_limit=4, owner_limit=1)
    assert claimed["status"] == "running"
    completed = store.transition(first["id"], "succeeded", summary="done", result={"ok": True})
    assert completed["progress"] == 1
    assert completed["result"] == {"ok": True}
    with pytest.raises(ValueError, match="invalid run transition"):
        store.transition(first["id"], "running")


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
