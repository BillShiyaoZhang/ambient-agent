import pytest

from backend.graph_db import GraphDatabase


def test_atomic_batch_rolls_back_every_action_on_failure(tmp_path):
    db = GraphDatabase(str(tmp_path))
    db.create_node("existing", "Task", {"title": "Existing"})

    with pytest.raises(ValueError, match="Target node"):
        db.apply_actions_atomic(
            [
                {"action": "create_node", "id": "partial", "type": "Task", "properties": {"title": "No"}},
                {"action": "create_edge", "from_id": "existing", "to_id": "missing", "type": "LINKS"},
            ]
        )

    assert db.get_node("partial") is None
    assert db.get_edges("existing") == []


def test_delete_reverse_action_restores_node_and_incident_edges(tmp_path):
    db = GraphDatabase(str(tmp_path))
    db.create_node("left", "Task", {"title": "Left"})
    db.create_node("right", "Task", {"title": "Right"})
    db.create_edge("left", "right", "LINKS", {"weight": 3})

    deleted = db.apply_actions_atomic(
        [{"action": "delete_node", "id": "left"}],
        session_id="session-1",
    )
    assert db.get_node("left") is None

    db.apply_actions_atomic(deleted["reverse_actions"])
    assert db.get_node("left")["properties"]["title"] == "Left"
    assert db.get_edges("left")[0]["properties"] == {"weight": 3}


def test_graph_effect_ledger_replays_result_without_duplicate_write(tmp_path):
    db = GraphDatabase(str(tmp_path))
    actions = [{"action": "create_node", "type": "Task", "properties": {"title": "Once"}}]

    first = db.apply_actions_atomic(actions, session_id="session-1", idempotency_key="run-1:commit")
    replay = db.apply_actions_atomic(actions, session_id="session-1", idempotency_key="run-1:commit")

    assert replay == first
    assert len([node for node in db.nodes.values() if node["properties"].get("title") == "Once"]) == 1
    with pytest.raises(ValueError, match="different actions"):
        db.apply_actions_atomic(
            [{"action": "create_node", "id": "other", "type": "Task", "properties": {"title": "Other"}}],
            idempotency_key="run-1:commit",
        )


def test_schema_effect_ledger_preserves_original_undo_snapshot(tmp_path):
    db = GraphDatabase(str(tmp_path))
    proposal = {
        "reused_schemas": [
            {"id": "Task", "reason": "test", "extended_properties": {"priority": "string"}}
        ],
        "new_schemas": [],
    }

    first = db.apply_schema_proposal_atomic(proposal, idempotency_key="run-1:schema")
    replay = db.apply_schema_proposal_atomic(proposal, idempotency_key="run-1:schema")

    assert replay == first
    assert "priority" in db.get_schema("Task")["properties"]
    db.restore_schema_snapshot(first["snapshot"], idempotency_key="run-1:schema")
    assert "priority" not in db.get_schema("Task")["properties"]

    retried = db.apply_schema_proposal_atomic(proposal, idempotency_key="run-1:schema")
    assert "priority" in db.get_schema("Task")["properties"]
    assert retried["snapshot"] == first["snapshot"]
