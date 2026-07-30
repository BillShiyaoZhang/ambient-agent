from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import _accept_websocket_safely, app, app_manager, get_db, run_live_broker
from backend.models import ChatSession
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
    test_session.add(ChatSession(id=session_id, title="WebSocket chat"))
    test_session.commit()

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            assert websocket.receive_json()["type"] == "active_sessions_list"
            websocket.send_json({"sender": "user", "content": "Hello Agent"})
            ack = websocket.receive_json()
            assert ack["type"] == "ack"
            assert ack["message"]["content"] == "Hello Agent"
            assert websocket.receive_json()["status"] == "running"
            reply = websocket.receive_json()
            if reply["type"] == "session_title_updated":
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


def test_browser_websocket_rejects_untrusted_origin():
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(
                "/ws/run-live?session_id=origin-check",
                headers={"origin": "https://attacker.example"},
            ):
                pass

    assert denied.value.code == 4403


def test_chat_websocket_rejects_session_path_escape(tmp_path):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))

    def override_get_db():
        yield storage

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as denied:
                with client.websocket_connect("/ws/chat?session_id=..%2Fescaped"):
                    pass
    finally:
        app.dependency_overrides.clear()

    assert denied.value.code == 4400
    assert not (tmp_path / "workspace" / "escaped.json").exists()


def test_chat_websocket_rejects_malformed_and_oversized_messages_without_disconnecting(test_session):
    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    test_session.add(ChatSession(id="protocol-bounds", title="Protocol bounds"))
    test_session.commit()
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/chat?session_id=protocol-bounds") as websocket:
                assert websocket.receive_json()["type"] == "active_sessions_list"

                websocket.send_json(["not", "an", "object"])
                assert websocket.receive_json()["code"] == "invalid_message"

                websocket.send_json({"sender": "user", "content": ["not text"]})
                assert websocket.receive_json()["code"] == "invalid_message_content"

                websocket.send_json({"type": "unknown-control-message"})
                assert websocket.receive_json()["code"] == "unsupported_message_type"

                websocket.send_json({"sender": "user", "content": "x" * (256 * 1024 + 1)})
                assert websocket.receive_json()["code"] == "message_too_large"

                websocket.send_json({"sender": "user", "content": ""})
                assert websocket.receive_json()["code"] == "invalid_message_content"
    finally:
        app.dependency_overrides.clear()

    assert test_session.get_messages("protocol-bounds") == []


def test_websocket_run_live_projects_only_the_subscribed_session():
    session_id = f"run-live-{uuid4().hex}"
    event = {
        "schema_version": 1,
        "run_id": "run-one",
        "session_id": session_id,
        "step_id": "stage_code",
        "attempt": 1,
        "stream_id": "run-one:stage_code:1:activity",
        "chunk_sequence": 1,
        "kind": "activity_delta",
        "delta": "hello",
        "replace": True,
        "created_at": "2026-07-26T00:00:00Z",
    }

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/run-live?session_id={session_id}") as websocket:
            assert websocket.receive_json() == {
                "type": "run_live_ready",
                "session_id": session_id,
            }
            run_live_broker.publish("another-session", {**event, "session_id": "another-session"})
            run_live_broker.publish(session_id, event)
            assert websocket.receive_json() == {"type": "run_live_event", "event": event}

    assert run_live_broker.subscriber_count(session_id) == 0


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
    test_session.add(ChatSession(id=session_id, title="Inline widget"))
    test_session.commit()

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            assert websocket.receive_json()["type"] == "active_sessions_list"
            websocket.send_json({"sender": "user", "content": "Give me weather details"})
            assert websocket.receive_json()["type"] == "ack"
            assert websocket.receive_json()["status"] == "running"
            error = websocket.receive_json()
            if error["type"] == "session_title_updated":
                error = websocket.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "unverified_inline_artifact"
            assert websocket.receive_json()["status"] == "idle"

    assert not (Path(test_session.apps_dir) / "weather-card").exists()
    app.dependency_overrides.clear()


def test_chat_websocket_cannot_create_a_missing_or_deleting_session(test_session):
    from backend import main

    def override_get_db():
        yield test_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as missing:
                with client.websocket_connect("/ws/chat?session_id=missing-session"):
                    pass
            assert missing.value.code == 4404
            assert test_session.get(ChatSession, "missing-session") is None

            test_session.add(ChatSession(id="deleting-session", title="Deleting"))
            test_session.commit()
            main.deleting_chat_sessions.add("deleting-session")
            with pytest.raises(WebSocketDisconnect) as deleting:
                with client.websocket_connect("/ws/chat?session_id=deleting-session"):
                    pass
            assert deleting.value.code == 4404
    finally:
        main.deleting_chat_sessions.discard("deleting-session")
        app.dependency_overrides.clear()
