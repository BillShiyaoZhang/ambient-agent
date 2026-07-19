from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.main import app, app_manager, get_db
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)

    # Isolate apps directory for app_manager inside this test
    old_apps_dir = app_manager.apps_dir
    app_manager.apps_dir = storage.apps_dir

    yield storage

    # Restore original apps directory
    app_manager.apps_dir = old_apps_dir


def test_app_slash_command_uses_durable_router_without_opencode_bypass(test_session, monkeypatch):
    routed = []

    async def mock_route(content, *_args, **_kwargs):
        routed.append(content)
        return IntentPlan(
            kind=IntentKind.CLARIFY,
            rationale="slash commands use the same durable routing path",
            clarification_message="Please describe the app requirements.",
        )

    async def unexpected_opencode(*_args, **_kwargs):
        pytest.fail("WebSocket input must not bypass the durable workflow into OpenCode")

    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)
    monkeypatch.setattr("backend.main.run_opencode_agent_acp", unexpected_opencode)

    # Override get_db dependency
    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    session_id = f"opencode-routing-{uuid4().hex}"

    with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
        active_list = websocket.receive_json()
        assert active_list["type"] == "active_sessions_list"
        # Legacy slash input is accepted for compatibility, but it is now an
        # ordinary command submitted to the durable internal_agent adapter.
        websocket.send_json({"sender": "user", "content": "/app test-timer Add standard start/stop buttons"})

        # 1. Expect acknowledgment
        ack = websocket.receive_json()
        assert ack["type"] == "ack"

        # Scheduler completion may race the compatibility status projection;
        # both are tied to the same durable Run and are asserted as a set.
        projected = []
        for _ in range(4):
            event = websocket.receive_json()
            projected.append(event)
            if event.get("type") == "session_status_update" and event.get("status") == "idle":
                break

        status_running = next(
            event
            for event in projected
            if event.get("type") == "session_status_update" and event.get("status") == "running"
        )
        reply = next(event for event in projected if event.get("type") == "reply")
        assert reply["type"] == "reply"
        assert reply["message"]["content"] == "Please describe the app requirements."

        status_idle = next(
            event
            for event in projected
            if event.get("type") == "session_status_update" and event.get("status") == "idle"
        )
        assert status_idle["type"] == "session_status_update"
        assert status_idle["status"] == "idle"

    assert routed == ["/app test-timer Add standard start/stop buttons"]
    assert not (Path(test_session.apps_dir) / "test-timer").exists()

    app.dependency_overrides.clear()
