import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.harness import AgentOrchestrator
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.graph_db import GraphDatabase
from backend.models import ChatSession
from backend.workspace_storage import WorkspaceStorage


def _setup_workspace(tmp_path, monkeypatch):
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)
    db = GraphDatabase(workspace_dir)
    return workspace_dir, db


@pytest.mark.asyncio
async def test_harness_routes_graph_mutation_without_opencode(monkeypatch, tmp_path):
    workspace_dir, db = _setup_workspace(tmp_path, monkeypatch)

    # Replace main's graph_db with our tmp one
    from backend import main

    main.graph_db = db

    # Mock IntentRouter to plan a graph_mutation
    plan = IntentPlan(
        kind=IntentKind.GRAPH_MUTATION,
        rationale="user adds a task",
        actions=[{"action": "create_node", "id": "t-route-1", "type": "Task", "properties": {"title": "buy milk"}}],
    )
    mock_route = AsyncMock(return_value=plan)
    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # Track that OpenCode is NOT invoked
    opencode_called = {"count": 0}

    async def fake_opencode(app_id, instruction, on_update=None):
        opencode_called["count"] += 1
        return "should not run"

    monkeypatch.setattr("backend.main.run_opencode_agent_acp", fake_opencode)

    # Database session
    db_storage = MagicMock(spec=WorkspaceStorage)
    db_storage.get.return_value = ChatSession(id="sess-gr", title="Test")
    db_storage.get_messages.return_value = []
    db_storage.add = MagicMock()
    db_storage.commit = MagicMock()
    db_storage.refresh = lambda obj: None

    app_manager = MagicMock()
    app_manager.list_apps.return_value = []

    orch = AgentOrchestrator(db_session=db_storage, app_manager=app_manager)

    preview_payloads = []

    async def on_update(data):
        if isinstance(data, dict):
            preview_payloads.append(data)

    agent_msg, widget = await orch.handle_message(session_id="sess-gr", content="add buy milk", on_update=on_update)

    # OpenCode NOT invoked
    assert opencode_called["count"] == 0
    # But the node was written to the graph
    node = db.get_node("t-route-1")
    assert node is not None
    assert node["properties"]["title"] == "buy milk"
    # A mutation_preview payload was pushed
    assert any(p.get("type") == "mutation_preview" for p in preview_payloads)
    preview = next(p for p in preview_payloads if p.get("type") == "mutation_preview")
    assert preview["session_id"] == "sess-gr"
    assert preview["ticket_id"]
    # Agent reply contains a description of the mutation
    assert "buy milk" in agent_msg.content


@pytest.mark.asyncio
async def test_harness_routes_graph_query(monkeypatch, tmp_path):
    workspace_dir, db = _setup_workspace(tmp_path, monkeypatch)
    db.create_node(node_id="t-q-1", node_type="Task", properties={"title": "answer", "status": "pending"})

    from backend import main

    main.graph_db = db

    plan = IntentPlan(
        kind=IntentKind.GRAPH_QUERY,
        rationale="user asks a question",
        query={"type": "Task"},
    )
    mock_route = AsyncMock(return_value=plan)
    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    opencode_called = {"count": 0}

    async def fake_opencode(app_id, instruction, on_update=None):
        opencode_called["count"] += 1
        return "should not run"

    monkeypatch.setattr("backend.main.run_opencode_agent_acp", fake_opencode)

    db_storage = MagicMock(spec=WorkspaceStorage)
    db_storage.get.return_value = ChatSession(id="sess-q", title="Test")
    db_storage.get_messages.return_value = []
    db_storage.add = MagicMock()
    db_storage.commit = MagicMock()
    db_storage.refresh = lambda obj: None

    app_manager = MagicMock()
    app_manager.list_apps.return_value = []

    orch = AgentOrchestrator(db_session=db_storage, app_manager=app_manager)

    agent_msg, widget = await orch.handle_message(
        session_id="sess-q", content="any pending tasks?", on_update=AsyncMock()
    )

    assert opencode_called["count"] == 0
    # Reply mentions the matching node
    assert "answer" in agent_msg.content


@pytest.mark.asyncio
async def test_harness_publishes_pinned_ticket_in_history(monkeypatch, tmp_path):
    workspace_dir, db = _setup_workspace(tmp_path, monkeypatch)

    from backend import main

    main.graph_db = db

    plan = IntentPlan(
        kind=IntentKind.GRAPH_MUTATION,
        rationale="user adds a task",
        actions=[{"action": "create_node", "id": "t-pin-1", "type": "Task", "properties": {"title": "pin me"}}],
    )
    mock_route = AsyncMock(return_value=plan)
    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    monkeypatch.setattr("backend.main.run_opencode_agent_acp", AsyncMock())

    db_storage = MagicMock(spec=WorkspaceStorage)
    db_storage.get.return_value = ChatSession(id="sess-pin", title="Test")
    db_storage.get_messages.return_value = []
    db_storage.add = MagicMock()
    db_storage.commit = MagicMock()
    db_storage.refresh = lambda obj: None

    orch = AgentOrchestrator(db_session=db_storage, app_manager=MagicMock())
    payload = []

    async def on_update(data):
        if isinstance(data, dict):
            payload.append(data)

    await orch.handle_message(session_id="sess-pin", content="x", on_update=on_update)

    # Pin the ticket
    from backend.mutation_tickets import MutationTicketManager

    mgr = MutationTicketManager(db)
    ticket_id = next(p["ticket_id"] for p in payload if p.get("type") == "mutation_preview")
    await mgr.pin("sess-pin", ticket_id)
    row = db.load_mutation_history(ticket_id)
    assert row is not None
    assert row["pinned"] == 1
