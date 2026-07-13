import asyncio
import os

import pytest

from backend.graph_db import GraphDatabase
from backend.mutation_tickets import (
    MutationTicketManager,
    compute_reverse_actions,
)


def _make_db(tmp_path) -> GraphDatabase:
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    return GraphDatabase(workspace_dir)


def test_compute_reverse_actions_create_node():
    forward = [
        {"action": "create_node", "id": "n1", "type": "Task", "properties": {"title": "x"}},
    ]
    reverse = compute_reverse_actions(forward)
    assert reverse[0]["action"] == "delete_node"
    assert reverse[0]["id"] == "n1"


def test_compute_reverse_actions_delete_node():
    forward = [{"action": "delete_node", "id": "n1"}]
    reverse = compute_reverse_actions(forward)
    # Reverse of delete is restore from snapshot — handled by caller
    assert reverse == []


def test_compute_reverse_actions_update_node_restores_old_props():
    forward = [{"action": "update_node_property", "id": "n1", "properties": {"status": "done"}}]
    old_props = {"status": "pending", "title": "x"}
    reverse = compute_reverse_actions(forward, snapshot_before={"n1": old_props})
    assert reverse[0]["action"] == "update_node_property"
    assert reverse[0]["id"] == "n1"
    assert reverse[0]["properties"] == {"status": "pending"}


def test_compute_reverse_actions_create_edge():
    forward = [{"action": "create_edge", "from_id": "a", "to_id": "b", "type": "ASSOCIATED_WITH"}]
    reverse = compute_reverse_actions(forward)
    assert reverse[0]["action"] == "delete_edge"
    assert reverse[0]["from_id"] == "a"


def test_graph_db_history_table(tmp_path):
    db = _make_db(tmp_path)
    db.record_mutation_history(
        ticket_id="ticket-1",
        session_id="sess-1",
        forward_actions=[{"action": "create_node", "id": "n1", "type": "Task", "properties": {}}],
        reverse_actions=[{"action": "delete_node", "id": "n1"}],
        snapshot_before={},
    )
    row = db.load_mutation_history("ticket-1")
    assert row is not None
    assert row["session_id"] == "sess-1"
    assert row["forward_actions"][0]["id"] == "n1"


def test_graph_db_pin_mutation_persists(tmp_path):
    db = _make_db(tmp_path)
    db.record_mutation_history(
        ticket_id="ticket-2",
        session_id="sess",
        forward_actions=[],
        reverse_actions=[],
        snapshot_before={},
    )
    db.pin_mutation_history("ticket-2")
    row = db.load_mutation_history("ticket-2")
    assert row["pinned"] == 1


@pytest.mark.asyncio
async def test_mutation_ticket_manager_record_and_rollback(tmp_path):
    db = _make_db(tmp_path)
    # Setup an existing node so we can test reverse snapshot
    db.create_node(node_id="n1", node_type="Task", properties={"status": "pending"})

    mgr = MutationTicketManager(db)

    forward = [
        {"action": "update_node_property", "id": "n1", "properties": {"status": "done"}},
    ]
    # Snapshot before
    snapshot_before = {"n1": dict(db.get_node("n1")["properties"])}

    ticket = mgr.record(
        session_id="sess-x",
        forward_actions=forward,
        snapshot_before=snapshot_before,
    )
    assert ticket.ticket_id
    assert ticket.session_id == "sess-x"
    assert mgr.get("sess-x", ticket.ticket_id) is not None

    # Apply the forward actions on DB to simulate post-mutation state
    db.update_node_property("n1", {"status": "done"})
    assert db.get_node("n1")["properties"]["status"] == "done"

    # Rollback
    reverses = await mgr.rollback("sess-x", ticket.ticket_id)
    assert reverses
    # Apply reverse actions
    for r in reverses:
        if r["action"] == "update_node_property":
            db.update_node_property(r["id"], r["properties"])

    assert db.get_node("n1")["properties"]["status"] == "pending"


@pytest.mark.asyncio
async def test_mutation_ticket_manager_soft_expiry_60s(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    mgr = MutationTicketManager(db, soft_window_seconds=0.05)

    forward = [{"action": "create_node", "id": "n1", "type": "Task", "properties": {"title": "x"}}]
    ticket = mgr.record(session_id="sess", forward_actions=forward, snapshot_before={})

    # Wait past expiry
    await asyncio.sleep(0.1)

    # Soft ticket no longer in memory but persistent history remains unpinned
    assert mgr.get("sess", ticket.ticket_id) is None
    row = db.load_mutation_history(ticket.ticket_id)
    assert row is not None
    assert row["pinned"] == 0


@pytest.mark.asyncio
async def test_mutation_ticket_manager_pinning_persists(tmp_path):
    db = _make_db(tmp_path)
    mgr = MutationTicketManager(db, soft_window_seconds=0.05)

    forward = [{"action": "create_node", "id": "n1", "type": "Task", "properties": {"title": "x"}}]
    ticket = mgr.record(session_id="sess", forward_actions=forward, snapshot_before={})

    await mgr.pin("sess", ticket.ticket_id)

    # Let soft window elapse — pinned ticket should still be retrievable
    await asyncio.sleep(0.1)
    row = db.load_mutation_history(ticket.ticket_id)
    assert row is not None
    assert row["pinned"] == 1

    # Rolling back pinned ticket must still execute reverse
    reverses = await mgr.rollback("sess", ticket.ticket_id)
    assert reverses and reverses[0]["action"] == "delete_node"


@pytest.mark.asyncio
async def test_mutation_ticket_manager_rollback_unknown_returns_empty(tmp_path):
    db = _make_db(tmp_path)
    mgr = MutationTicketManager(db)
    reverses = await mgr.rollback("sess", "nonexistent")
    assert reverses == []
