from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app, app_manager, get_db
from backend.models import ChatSession
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)

    old_apps_dir = app_manager.apps_dir
    app_manager.apps_dir = storage.apps_dir

    yield storage
    app_manager.apps_dir = old_apps_dir


def test_websocket_plan_confirmation_flow(test_session, monkeypatch):
    monkeypatch.setenv("FORCE_INTERACTIVE", "true")

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

    # 2. Mock Schema alignment to return empty proposal (approved immediately in test)
    async def mock_align_schemas(*args, **kwargs):
        return {"reused_schemas": [], "new_schemas": []}

    monkeypatch.setattr("backend.schema_alignment.SchemaAlignmentService.align_schemas", mock_align_schemas)

    # 3. Mock Plan Generation to return a standard plan
    async def mock_generate_plan(*args, **kwargs):
        return "Initial Test Plan"

    monkeypatch.setattr("backend.plan_generation.PlanGenerationService.generate_plan", mock_generate_plan)

    # 4. Mock ACP OpenCode agent call
    mock_run_opencode = AsyncMock(return_value="OpenCode successfully ran")
    monkeypatch.setattr("backend.main.run_opencode_agent_acp", mock_run_opencode)

    # Mock Schema Verification to pass
    async def mock_verify(*args, **kwargs):
        return "✅ Schema Verification PASSED"

    monkeypatch.setattr("backend.schema_verification.SchemaVerificationService.verify", mock_verify)

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    # Save a chat session to the DB
    session_obj = ChatSession(id="session-123", title="Active Test Chat")
    test_session.add(session_obj)
    test_session.commit()

    client = TestClient(app)

    with client.websocket_connect("/ws/chat?session_id=session-123") as websocket:
        websocket.send_json({"sender": "user", "content": "Create a new visual card"})

        # Expect active list on connect
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"

        # Expect user ACK
        ack = websocket.receive_json()
        assert ack["type"] == "ack"

        # Expect session status running update
        status_running = websocket.receive_json()
        assert status_running["type"] == "session_status_update"
        assert status_running["session_id"] == "session-123"
        assert status_running["status"] == "running"

        # PHASE 1: Plan Generation thinking update
        status_plan_msg = websocket.receive_json()
        assert status_plan_msg["type"] == "reply"
        assert "正在为您制定开发计划" in status_plan_msg["message"]["content"]

        # Expect Plan Approval Request modal payload
        plan_req = websocket.receive_json()
        assert plan_req["type"] == "plan_approval_request"
        assert plan_req["app_id"] == "test-app"
        assert plan_req["plan"] == "Initial Test Plan"
        request_id = plan_req["request_id"]

        # Expect waiting message for plan
        waiting_msg = websocket.receive_json()
        assert waiting_msg["type"] == "reply"
        assert "等待开发计划" in waiting_msg["message"]["content"]

        # Send approved response for plan back
        websocket.send_json(
            {
                "type": "plan_approval_response",
                "request_id": request_id,
                "approved": "approve",
                "plan": "Initial Test Plan",
                "feedback": "",
            }
        )

        # PHASE 2: Schema alignment thinking update
        status_schema = websocket.receive_json()
        assert status_schema["type"] == "reply"
        assert "正在对齐数据库 Schema" in status_schema["message"]["content"]

        # Expect Schema Approval Request modal payload
        schema_req = websocket.receive_json()
        assert schema_req["type"] == "schema_approval_request"
        schema_request_id = schema_req["request_id"]

        # Expect waiting message for schema
        waiting_schema_msg = websocket.receive_json()
        assert "等待数据库 Schema 确认中" in waiting_schema_msg["message"]["content"]

        # Send approved response for schema back
        websocket.send_json(
            {
                "type": "schema_approval_response",
                "request_id": schema_request_id,
                "approved": "approve",
                "proposal": {"reused_schemas": [], "new_schemas": []},
                "feedback": "",
            }
        )

        # Expect confirmation message
        confirmed_msg = websocket.receive_json()
        assert confirmed_msg["type"] == "reply"
        assert "启动 OpenCode 开发者智能体" in confirmed_msg["message"]["content"]

        # Expect verification start update
        verify_start = websocket.receive_json()
        assert "正在校验代码与 Database Schema" in verify_start["message"]["content"]

        # Expect verification report message
        verify_report = websocket.receive_json()
        assert any(x in verify_report["message"]["content"] for x in ["Database Schema Verification Report", "数据库 Schema 校验报告"])

        # Expect final reply and execution logs
        reply_msg = websocket.receive_json()
        assert reply_msg["type"] == "reply"
        assert any(x in reply_msg["message"]["content"] for x in ["OpenCode Execution Log", "OpenCode 执行日志"])
        assert any(x in reply_msg["message"]["content"] for x in ["Database Schema Verification Report", "数据库 Schema 校验报告"])

        # Expect session status idle update
        status_idle = websocket.receive_json()
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    app.dependency_overrides.clear()
