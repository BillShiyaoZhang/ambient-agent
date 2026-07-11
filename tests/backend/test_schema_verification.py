import pytest
from fastapi.testclient import TestClient

from backend.main import app, get_db
from backend.models import ChatSession
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)
    yield storage

def test_websocket_plan_then_schema_then_verify_flow(test_session, monkeypatch):
    monkeypatch.setenv("FORCE_INTERACTIVE", "true")

    # 1. Mock routing to treat as coding task
    async def mock_route(content, existing_apps, db_session=None):
        return True, "test-app", content
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
    async def mock_run_opencode(app_id, instruction, on_update):
        # Retrieve app_manager from main to write test files
        from backend.main import app_manager
        app_manager.create_or_update_app(
            app_id=app_id,
            title="Stopwatch App",
            html="<title>Stopwatch App</title><div>00:00</div>",
            css="",
            js="console.log('stopwatch active');"
        )
        return "OpenCode successfully ran"
    monkeypatch.setattr("backend.main.run_opencode_agent_acp", mock_run_opencode)

    # 5. Mock Schema Verification
    async def mock_verify(app_id, widget_code, registered_schemas, db_session=None):
        return "✅ Schema Verification PASSED"
    monkeypatch.setattr("backend.schema_verification.SchemaVerificationService.verify", mock_verify)

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    # Save a chat session to the DB
    session_obj = ChatSession(id="session-456", title="Test Order Chat")
    test_session.add(session_obj)
    test_session.commit()

    client = TestClient(app)

    with client.websocket_connect("/ws/chat?session_id=session-456") as websocket:
        websocket.send_json({
            "sender": "user",
            "content": "Build me a stopwatch"
        })

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

        # PHASE 1: Plan Generation thinking
        plan_thinking = websocket.receive_json()
        assert plan_thinking["type"] == "reply"
        assert "正在为您制定开发计划" in plan_thinking["message"]["content"]

        # Expect Plan Approval Request modal
        plan_req = websocket.receive_json()
        assert plan_req["type"] == "plan_approval_request"
        assert plan_req["plan"] == "Plan: Build stopwatch"
        plan_request_id = plan_req["request_id"]

        # Expect Plan Waiting message
        waiting_plan = websocket.receive_json()
        assert "等待开发计划 Plan 确认中" in waiting_plan["message"]["content"]

        # Approve Plan
        websocket.send_json({
            "type": "plan_approval_response",
            "request_id": plan_request_id,
            "approved": "approve",
            "plan": "Plan: Build stopwatch",
            "feedback": ""
        })

        # PHASE 2: Schema Alignment thinking
        schema_thinking = websocket.receive_json()
        assert schema_thinking["type"] == "reply"
        assert "正在对齐数据库 Schema" in schema_thinking["message"]["content"]

        # Expect Schema Approval Request modal
        schema_req = websocket.receive_json()
        assert schema_req["type"] == "schema_approval_request"
        schema_request_id = schema_req["request_id"]

        # Expect Schema Waiting message
        waiting_schema = websocket.receive_json()
        assert "等待数据库 Schema 确认中" in waiting_schema["message"]["content"]

        # Approve Schema
        websocket.send_json({
            "type": "schema_approval_response",
            "request_id": schema_request_id,
            "approved": "approve",
            "proposal": {"reused_schemas": [], "new_schemas": []},
            "feedback": ""
        })

        # Expect ACP OpenCode start update
        opencode_start = websocket.receive_json()
        assert "正在启动 OpenCode 开发者智能体" in opencode_start["message"]["content"]

        # Expect Verification start update
        verify_start = websocket.receive_json()
        assert "正在校验代码与 Database Schema" in verify_start["message"]["content"]

        # Expect final Verification report message
        verify_report = websocket.receive_json()
        assert "Database Schema Verification Report" in verify_report["message"]["content"]
        assert "✅ Schema Verification PASSED" in verify_report["message"]["content"]

        # Expect final log + verification report reply
        final_log = websocket.receive_json()
        print("DEBUG FINAL_LOG:", final_log)
        assert "OpenCode Execution Log" in final_log["message"]["content"]
        assert "✅ Schema Verification PASSED" in final_log["message"]["content"]

        # Expect widget delivery message
        widget_msg = websocket.receive_json()
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "test-app"

        # Expect idle status update
        status_idle = websocket.receive_json()
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    app.dependency_overrides.clear()
