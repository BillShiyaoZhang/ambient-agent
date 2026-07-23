from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import _accept_websocket_safely, app, app_manager, get_db
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    old_apps_dir = app_manager.apps_dir
    app_manager.apps_dir = storage.apps_dir
    yield storage
    app_manager.apps_dir = old_apps_dir


def test_websocket_chat_flow(test_session, monkeypatch):
    async def mock_route(content, existing_apps=None, db_session=None, **_kwargs):
        from backend.agent.intent_plan import IntentKind, IntentPlan

        return IntentPlan(kind=IntentKind.CONVERSE, rationale="chitchat", instruction=content)

    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    async def mock_call_llm_api(provider, model, prompt, tools=None):
        return "I am your Ambient Agent. You said: 'Hello Agent'"

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    session_id = f"websocket-chat-{uuid4().hex}"

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            assert websocket.receive_json()["type"] == "active_sessions_list"
            websocket.send_json({"sender": "user", "content": "Hello Agent"})
            ack = websocket.receive_json()
            assert ack["type"] == "ack"
            assert ack["message"]["content"] == "Hello Agent"
            assert websocket.receive_json()["status"] == "running"
            thinking = websocket.receive_json()
            if thinking["type"] == "session_title_updated":
                thinking = websocket.receive_json()
            assert thinking["type"] == "reply"
            reply = websocket.receive_json()
            assert reply["type"] == "reply"
            assert "Hello Agent" in reply["message"]["content"]
            assert websocket.receive_json()["status"] == "idle"
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_aborted_or_duplicate_websocket_handshake_is_ignored_without_asgi_error():
    class StaleHandshake:
        async def accept(self):
            raise RuntimeError(
                "Expected ASGI message 'websocket.send' or 'websocket.close', but got 'websocket.accept'."
            )

    assert await _accept_websocket_safely(StaleHandshake()) is False


def test_websocket_converse_rejects_unverified_inline_widget(test_session, monkeypatch):
    async def mock_route(content, existing_apps=None, db_session=None, **_kwargs):
        from backend.agent.intent_plan import IntentKind, IntentPlan

        return IntentPlan(kind=IntentKind.CONVERSE, rationale="chitchat", instruction=content)

    monkeypatch.setattr("backend.agent.router.IntentRouter.route", mock_route)

    async def mock_call_llm_api(provider, model, prompt, tools=None):
        return '<ambient-widget id="weather-card" title="Weather"><js-script>export default null;</js-script></ambient-widget>'

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    session_id = f"websocket-inline-widget-{uuid4().hex}"

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            assert websocket.receive_json()["type"] == "active_sessions_list"
            websocket.send_json({"sender": "user", "content": "Give me weather details"})
            assert websocket.receive_json()["type"] == "ack"
            assert websocket.receive_json()["status"] == "running"
            thinking = websocket.receive_json()
            if thinking["type"] == "session_title_updated":
                thinking = websocket.receive_json()
            assert thinking["type"] == "reply"
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "unverified_inline_artifact"
            assert websocket.receive_json()["status"] == "idle"

    assert not (Path(test_session.apps_dir) / "weather-card").exists()
    app.dependency_overrides.clear()
