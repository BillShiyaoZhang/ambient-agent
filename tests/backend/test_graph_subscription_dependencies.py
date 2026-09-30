from unittest.mock import AsyncMock

import pytest

from backend.graph_db import GraphDatabase
from backend.graph_subscription import SubscriptionManager


@pytest.mark.asyncio
@pytest.mark.parametrize("target_type", [None, "Person"])
async def test_included_target_edit_refreshes_subscription(tmp_path, target_type):
    db = GraphDatabase(str(tmp_path))
    db.create_node("task", "Task", {"title": "Task"})
    db.create_node("person", "Person", {"name": "Before"})
    db.create_edge("task", "person", "ASSIGNED_TO")
    include = {"relation": "ASSIGNED_TO"}
    if target_type:
        include["target_type"] = target_type
    manager = SubscriptionManager()
    manager.register("socket", "tasks", {"type": "Task", "include": [include]}, db)
    send = AsyncMock()
    db.update_node_property("person", {"name": "After"})
    await manager.broadcast_updates(db, send, mutated_types={"Person"})
    send.assert_awaited_once()
    assert send.call_args.args[1]["data"][0]["relations"][0]["target"]["properties"]["name"] == "After"
    await manager.broadcast_updates(db, send, mutated_types={"Person"})
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_relation_changes_with_unknown_type_hint_refresh_subscription(tmp_path):
    db = GraphDatabase(str(tmp_path))
    db.create_node("task", "Task", {"title": "Task"})
    db.create_node("person", "Person", {"name": "Person"})
    manager = SubscriptionManager()
    manager.register(
        "socket", "tasks", {"type": "Task", "include": [{"relation": "ASSIGNED_TO", "target_type": "Person"}]}, db
    )
    send = AsyncMock()
    db.create_edge("task", "person", "ASSIGNED_TO")
    await manager.broadcast_updates(db, send, mutated_types=set())
    send.assert_awaited_once()
    assert len(send.call_args.args[1]["data"][0]["relations"]) == 1
