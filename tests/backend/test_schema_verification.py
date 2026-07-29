import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app, app_manager, coding_agent_config_store, get_db
from backend.models import ChatSession
from backend.coding_agent_acp import OpenCodeStagedResult
from backend.schema_diff import VerificationDiff
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)

    old_apps_dir = app_manager.apps_dir
    app_manager.apps_dir = storage.apps_dir

    yield storage
    app_manager.apps_dir = old_apps_dir


@pytest.fixture(name="client")
def client_fixture():
    with TestClient(app) as client:
        yield client


def test_websocket_plan_then_schema_then_verify_flow(test_session, monkeypatch, client):
    monkeypatch.setenv("FORCE_INTERACTIVE", "true")
    coding_settings = {**coding_agent_config_store.get_settings(), "default_agent": "opencode"}
    monkeypatch.setattr("backend.main.coding_agent_config_store.get_settings", lambda: coding_settings)

    # 1. Mock routing to treat as coding task
    async def mock_route(content, existing_apps=None, db_session=None, **_kwargs):
        from backend.agent.intent_plan import IntentKind, IntentPlan

        return IntentPlan(
            kind=IntentKind.WIDGET_MODIFY,
            rationale="test",
            app_id="test-app",
            instruction=content,
        )

    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # 2. Mock Plan Generation
    async def mock_generate_plan(*args, **kwargs):
        return "Plan: Build stopwatch"

    monkeypatch.setattr("backend.plan_generation.PlanGenerationService.generate_plan", mock_generate_plan)

    # 3. Mock Schema alignment
    async def mock_align_schemas(*args, **kwargs):
        assert kwargs.get("approved_plan") == "Plan: Build stopwatch"
        return {"reused_schemas": [], "new_schemas": []}

    monkeypatch.setattr("backend.schema_alignment.SchemaAlignmentService.align_schemas", mock_align_schemas)

    # 4. Mock ACP OpenCode agent call
    async def mock_run_opencode(
        app_id,
        instruction,
        language="zh",
        on_update=None,
        promote=True,
        **_kwargs,
    ):
        assert instruction
        assert language == "zh"
        assert on_update is not None
        assert promote is False
        apps_dir = Path(app_manager.apps_dir)
        apps_dir.mkdir(parents=True, exist_ok=True)
        staging_dir = apps_dir / f".{app_id}.staging-{uuid4().hex}"
        staging_dir.mkdir()
        (staging_dir / "controller.js").write_text(
            "export default function App() { return ambient.html`<div>00:00</div>`; }",
            encoding="utf-8",
        )
        (staging_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 2,
                    "id": app_id,
                    "title": "Stopwatch App",
                    "description": "",
                    "app_version": "0.1.0",
                    "intents": [],
                    "schema_refs": [],
                    "capabilities": [],
                }
            ),
            encoding="utf-8",
        )
        return OpenCodeStagedResult(
            output="OpenCode successfully ran",
            app_id=app_id,
            staging_dir=staging_dir,
            live_dir=apps_dir / app_id,
        )

    monkeypatch.setattr("backend.main.run_coding_agent", mock_run_opencode)

    # 5. Mock Schema Verification
    async def mock_diff(*args, **kwargs):
        return VerificationDiff()

    monkeypatch.setattr("backend.schema_verification.SchemaVerificationService.diff", mock_diff)

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    # Save a chat session to the DB
    session_id = f"session-schema-{uuid4().hex}"
    session_obj = ChatSession(id=session_id, title="Test Order Chat")
    test_session.add(session_obj)
    test_session.commit()

    with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
        websocket.send_json({"sender": "user", "content": "Build me a stopwatch"})

        # Expect active list on connect
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"

        # Expect user ACK
        ack = websocket.receive_json()
        assert ack["type"] == "ack"

        # Expect running status update
        status_running = websocket.receive_json()
        assert status_running["type"] == "session_status_update"
        assert status_running["status"] == "running"

        # Expect Plan Approval Request modal
        plan_req = websocket.receive_json()
        assert plan_req["type"] == "plan_approval_request"
        assert plan_req["plan"] == "Plan: Build stopwatch"
        plan_request_id = plan_req["request_id"]

        # Approve Plan
        websocket.send_json(
            {
                "type": "plan_approval_response",
                "request_id": plan_request_id,
                "approved": "approve",
                "plan": "Plan: Build stopwatch",
                "feedback": "",
            }
        )

        # Expect Schema Approval Request modal
        schema_req = websocket.receive_json()
        assert schema_req["type"] == "schema_approval_request"
        schema_request_id = schema_req["request_id"]

        # Approve Schema
        websocket.send_json(
            {
                "type": "schema_approval_response",
                "request_id": schema_request_id,
                "approved": "approve",
                "proposal": {"reused_schemas": [], "new_schemas": []},
                "feedback": "",
            }
        )

        # Process output stays on the Run stream. The legacy socket receives
        # only the final answer and App delivery. Their
        # projection order is not part of the public WebSocket contract.
        tail = []
        for _ in range(4):
            event = websocket.receive_json()
            if event.get("type") == "session_status_update" and event.get("status") == "idle":
                status_idle = event
                break
            tail.append(event)
        else:  # pragma: no cover - keeps a hung/malformed projection failure explicit
            pytest.fail("workflow did not reach idle after promotion")

        final_answer = next(event for event in tail if event.get("type") == "reply")
        assert "已生成、验证并发布" in final_answer["message"]["content"]
        assert "Execution Log" not in final_answer["message"]["content"]
        assert "Schema Verification" not in final_answer["message"]["content"]

        # Expect widget delivery message
        widget_msg = next(event for event in tail if event.get("type") == "widget")
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "test-app"

        # Expect idle status update
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    app.dependency_overrides.clear()
