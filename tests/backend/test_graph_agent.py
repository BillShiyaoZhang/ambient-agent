import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.harness import AgentOrchestrator
from backend.agent.providers import CloudLLMProvider
from backend.graph_db import GraphDatabase
from backend.models import ChatSession
from backend.workspace_storage import WorkspaceStorage


@pytest.mark.asyncio
async def test_agent_react_graph_mutations(monkeypatch, tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    db = GraphDatabase(workspace_dir)

    # Reload Graph DB in tool registry and in main
    from backend import main
    main.graph_db = db

    # Mock LLM API calls
    mock_call_api = AsyncMock()

    actions = [
        {
            "action": "create_node",
            "id": "t-react-1",
            "type": "Task",
            "properties": {"title": "ReAct Task"}
        }
    ]

    # First turn: tool call to mutate_graph
    # Second turn: final message
    mock_call_api.side_effect = [
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "mutate_graph",
                        "arguments": json.dumps({"actions_json": json.dumps(actions)})
                    }
                }
            ]
        },
        {
            "content": "I have created the task successfully.",
            "tool_calls": None
        }
    ]

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_api)

    # Set provider to a CloudLLMProvider which invokes _run_tool_loop calling call_llm_api
    provider = CloudLLMProvider(model="test-model")
    monkeypatch.setattr("backend.agent.harness.get_llm_provider", lambda p, m: provider)

    # Mock IntentRouter to route to conversational path
    mock_route = AsyncMock(return_value=(False, None, "create a task"))
    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # Mock workspace storage database session
    db_storage = MagicMock(spec=WorkspaceStorage)
    db_storage.get.return_value = ChatSession(id="sess-react", title="ReAct Test")

    app_manager = MagicMock()
    app_manager.list_apps.return_value = []

    orchestrator = AgentOrchestrator(db_session=db_storage, app_manager=app_manager)

    on_update = AsyncMock()

    agent_msg, widget = await orchestrator.handle_message(
        session_id="sess-react",
        content="create a task",
        on_update=on_update
    )

    # Check that tool was executed and node exists in graph_db
    assert db.get_node("t-react-1") is not None
    assert db.get_node("t-react-1")["properties"]["title"] == "ReAct Task"

    # Check final response
    assert agent_msg.content == "I have created the task successfully."

