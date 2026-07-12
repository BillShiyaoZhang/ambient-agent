import json

import pytest

from backend.agent.router import IntentRouter
from backend.router_context import RouterContext
from backend.workspace_storage import WorkspaceStorage


@pytest.mark.asyncio
async def test_router_logs_route_stage_in_audit(monkeypatch, tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    import os

    os.makedirs(workspace_dir, exist_ok=True)
    storage = WorkspaceStorage(workspace_dir)

    captured: dict = {}

    async def mock_capture(provider, model, messages, tools=None):
        captured["messages"] = messages
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "classify_intent",
                        "arguments": json.dumps(
                            {"kind": "converse", "rationale": "ok"}
                        ),
                    },
                }
            ],
        }

    monkeypatch.setattr("backend.agent.router.call_llm_api", mock_capture)

    ctx = RouterContext()
    await IntentRouter.route("hello", ctx, db_session=storage)

    logs = storage.get_audit_logs()
    assert any(log.stage == "route" for log in logs)
