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


def test_websocket_rework_loops_flow(test_session, monkeypatch):
    monkeypatch.setenv("FORCE_INTERACTIVE", "true")

    # 1. Mock routing
    async def mock_route(content, existing_apps, db_session=None):
        return True, "rework-app", content

    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    # 2. Mock Plan Generation
    plan_counter = 0

    async def mock_generate_plan(*args, **kwargs):
        nonlocal plan_counter
        plan_counter += 1
        return f"Plan Version {plan_counter}"

    monkeypatch.setattr("backend.plan_generation.PlanGenerationService.generate_plan", mock_generate_plan)

    # 3. Mock Schema alignment
    schema_counter = 0

    async def mock_align_schemas(*args, **kwargs):
        nonlocal schema_counter
        schema_counter += 1
        return {"reused_schemas": [], "new_schemas": []}

    monkeypatch.setattr("backend.schema_alignment.SchemaAlignmentService.align_schemas", mock_align_schemas)

    # 4. Mock ACP OpenCode agent call
    opencode_counter = 0

    async def mock_run_opencode(app_id, instruction, on_update):
        nonlocal opencode_counter
        opencode_counter += 1
        # Retrieve app_manager from main to write test files
        from backend.main import app_manager

        app_manager.create_or_update_app(
            app_id=app_id, title="Rework App", html="<div>Reworked app</div>", css="", js="// code content"
        )
        return f"OpenCode ran {opencode_counter} times"

    monkeypatch.setattr("backend.main.run_opencode_agent_acp", mock_run_opencode)

    # 5. Mock Schema Verification
    verify_counter = 0

    async def mock_verify(app_id, widget_code, registered_schemas, db_session=None):
        nonlocal verify_counter
        verify_counter += 1
        if verify_counter == 1:
            return "❌ DISCREPANCY DETECTED: Missing type validation"
        else:
            return "✅ Schema Verification PASSED"

    monkeypatch.setattr("backend.schema_verification.SchemaVerificationService.verify", mock_verify)

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db

    # Save a chat session to the DB
    session_obj = ChatSession(id="session-rework", title="Rework Loops Chat")
    test_session.add(session_obj)
    test_session.commit()

    client = TestClient(app)

    with client.websocket_connect("/ws/chat?session_id=session-rework") as websocket:
        websocket.send_json({"sender": "user", "content": "Create complex app"})

        # Expect active list
        assert websocket.receive_json()["type"] == "active_sessions_list"
        # Expect user ACK
        assert websocket.receive_json()["type"] == "ack"
        # Expect running status update
        assert websocket.receive_json()["type"] == "session_status_update"

        # Plan thinking & plan request 1
        assert "正在为您制定开发计划" in websocket.receive_json()["message"]["content"]
        plan_req = websocket.receive_json()
        assert plan_req["type"] == "plan_approval_request"
        assert plan_req["plan"] == "Plan Version 1"
        plan_request_id = plan_req["request_id"]

        # Expect waiting message
        assert "等待开发计划" in websocket.receive_json()["message"]["content"]

        # Approve Plan 1
        websocket.send_json(
            {
                "type": "plan_approval_response",
                "request_id": plan_request_id,
                "approved": "approve",
                "plan": "Plan Version 1",
                "feedback": "",
            }
        )

        # Schema alignment thinking & request 1
        assert "正在对齐数据库 Schema" in websocket.receive_json()["message"]["content"]
        schema_req = websocket.receive_json()
        assert schema_req["type"] == "schema_approval_request"
        schema_request_id = schema_req["request_id"]

        # Expect waiting message
        assert "等待数据库 Schema" in websocket.receive_json()["message"]["content"]

        # Send rework_plan response back to request Plan Rework!
        websocket.send_json(
            {
                "type": "schema_approval_response",
                "request_id": schema_request_id,
                "approved": "rework_plan",
                "proposal": {},
                "feedback": "Please rework plan to be simpler",
            }
        )

        # Expect returning message
        assert "正在返回开发计划制定阶段" in websocket.receive_json()["message"]["content"]

        # Plan thinking & plan request 2 (reworked plan!)
        assert "正在为您制定开发计划" in websocket.receive_json()["message"]["content"]
        plan_req_2 = websocket.receive_json()
        assert plan_req_2["type"] == "plan_approval_request"
        assert plan_req_2["plan"] == "Plan Version 2"
        plan_request_id_2 = plan_req_2["request_id"]

        assert "等待开发计划" in websocket.receive_json()["message"]["content"]

        # Approve Plan 2
        websocket.send_json(
            {
                "type": "plan_approval_response",
                "request_id": plan_request_id_2,
                "approved": "approve",
                "plan": "Plan Version 2",
                "feedback": "",
            }
        )

        # Schema alignment thinking & request 2
        assert "正在对齐数据库 Schema" in websocket.receive_json()["message"]["content"]
        schema_req_2 = websocket.receive_json()
        assert schema_req_2["type"] == "schema_approval_request"
        schema_request_id_2 = schema_req_2["request_id"]

        assert "等待数据库 Schema" in websocket.receive_json()["message"]["content"]

        # Approve Schema 2
        websocket.send_json(
            {
                "type": "schema_approval_response",
                "request_id": schema_request_id_2,
                "approved": "approve",
                "proposal": {"reused_schemas": [], "new_schemas": []},
                "feedback": "",
            }
        )

        # OpenCode starts execution
        assert "正在启动 OpenCode 开发者智能体" in websocket.receive_json()["message"]["content"]

        # Verification starts execution
        assert "正在校验代码与 Database Schema" in websocket.receive_json()["message"]["content"]

        # Expect Verification Report showing discrepancies (fails verification 1)
        verify_report_msg = websocket.receive_json()
        assert "Database Schema Verification Report" in verify_report_msg["message"]["content"]
        assert "❌ DISCREPANCY DETECTED" in verify_report_msg["message"]["content"]

        # Expect Verification Approval request payload
        verify_req = websocket.receive_json()
        assert verify_req["type"] == "verification_approval_request"
        verify_request_id = verify_req["request_id"]
        assert "❌ DISCREPANCY DETECTED" in verify_req["report"]

        # Expect waiting message
        assert "等待 Schema 校验警告处理指令" in websocket.receive_json()["message"]["content"]

        # Send Rework Code response to request Auto-Fix!
        websocket.send_json(
            {
                "type": "verification_approval_response",
                "request_id": verify_request_id,
                "approved": "rework_code",
                "feedback": "Please fix type validations",
            }
        )

        # Expect returning message
        assert "正在请求 OpenCode 自动修复代码对齐问题" in websocket.receive_json()["message"]["content"]

        # OpenCode starts execution again (run 2)
        assert "正在启动 OpenCode 开发者智能体" in websocket.receive_json()["message"]["content"]

        # Verification starts execution again (run 2)
        assert "正在校验代码与 Database Schema" in websocket.receive_json()["message"]["content"]

        # Expect Verification Report showing PASS
        verify_report_msg_2 = websocket.receive_json()
        assert "✅ Schema Verification PASSED" in verify_report_msg_2["message"]["content"]

        # Expect final reply and execution logs
        reply_msg = websocket.receive_json()
        assert reply_msg["type"] == "reply"
        assert "OpenCode ran 2 times" in reply_msg["message"]["content"]
        assert "✅ Schema Verification PASSED" in reply_msg["message"]["content"]

        # Expect widget delivery message
        widget_msg = websocket.receive_json()
        assert widget_msg["type"] == "widget"
        assert widget_msg["widget"]["id"] == "rework-app"

        # Expect idle status update
        status_idle = websocket.receive_json()
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    app.dependency_overrides.clear()
