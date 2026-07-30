import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from backend import main as main_module
from backend.main import app, get_db
from backend.models import ChatMessage, ChatSession, LLMAuditLog
from backend.run_service import RunStore
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="session")
def session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)
    yield storage


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "Ambient Agent is running"}


@pytest.mark.asyncio
async def test_database_initialization(session):
    # Create a message
    msg = ChatMessage(session_id="test-session", sender="user", content="Hello Agent")
    session.add(msg)
    session.commit()
    session.refresh(msg)

    # Read the message back
    assert msg.id is not None
    assert msg.sender == "user"
    assert msg.content == "Hello Agent"
    assert msg.timestamp is not None


def test_browser_control_plane_cors_uses_frontend_origin_allowlist():
    with TestClient(app) as client:
        allowed = client.options(
            "/api/sessions",
            headers={
                "origin": "http://localhost:5173",
                "access-control-request-method": "POST",
            },
        )
        denied = client.options(
            "/api/sessions",
            headers={
                "origin": "https://attacker.example",
                "access-control-request-method": "POST",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_session_create_rejects_path_escape(tmp_path):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))

    def override_get_db():
        yield storage

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/sessions",
                json={"id": "../escaped", "title": "Unsafe", "language": "en"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert not (tmp_path / "workspace" / "escaped.json").exists()


def test_corrupt_canvas_api_fails_closed_without_overwriting_source(tmp_path):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    canvas_file = tmp_path / "workspace" / "canvas.json"
    original = '{"version":3,"open_app_ids":['
    canvas_file.write_text(original, encoding="utf-8")

    def override_get_db():
        yield storage

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            loaded = client.get("/api/canvas")
            saved = client.post("/api/canvas", json={"version": 3, "open_app_ids": []})
    finally:
        app.dependency_overrides.clear()

    assert loaded.status_code == 503
    assert loaded.json()["detail"]["code"] == "workspace_state_corrupt"
    assert loaded.headers["cache-control"] == "no-store"
    assert saved.status_code == 503
    assert canvas_file.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_delete_session_purges_terminal_runs_and_audit_but_refuses_active_work(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    run_store = RunStore(str(workspace))
    for session_id in ("delete-me", "active-session"):
        storage.add(ChatSession(id=session_id, title=session_id))
    storage.commit()

    completed = run_store.create_run(
        owner_id="ambient-agent:delete-me",
        action_id="chat",
        action_title="Completed chat",
        source_type="chat",
        source_id="delete-me",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "private"},
        status="succeeded",
    )
    active = run_store.create_run(
        owner_id="ambient-agent:active-session",
        action_id="chat",
        action_title="Active chat",
        source_type="chat",
        source_id="active-session",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "still running"},
    )
    storage.add(
        LLMAuditLog(
            provider="test",
            model="test",
            prompt="private",
            response="private",
            session_id="delete-me",
            run_id=completed["id"],
        )
    )
    storage.commit()

    def override_get_db():
        yield storage

    monkeypatch.setattr(main_module, "run_store", run_store)
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            blocked = await client.delete("/api/sessions/active-session")
            deleted = await client.delete("/api/sessions/delete-me")
    finally:
        app.dependency_overrides.clear()

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "session_has_active_runs"
    assert blocked.json()["detail"]["run_ids"] == [active["id"]]
    assert storage.get(ChatSession, "active-session") is not None
    assert run_store.get_run(active["id"]) is not None

    assert deleted.status_code == 200
    assert deleted.json()["removed"] == {"session": True, "runs": 1, "audit_logs": 1}
    assert storage.get(ChatSession, "delete-me") is None
    assert run_store.get_run(completed["id"]) is None
    assert storage.get_audit_logs() == []


@pytest.mark.asyncio
async def test_delete_session_purge_race_leaves_audit_and_session_untouched(tmp_path, monkeypatch):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    storage.add(ChatSession(id="race-session", title="Race"))
    storage.add(
        LLMAuditLog(
            provider="test",
            model="test",
            prompt="keep until purge succeeds",
            response="private",
            session_id="race-session",
            run_id="race-run",
        )
    )
    storage.commit()

    class RacingRunStore:
        @staticmethod
        def session_runs_for_purge(_session_id):
            return {"race-run"}

        @staticmethod
        def purge_session(_session_id):
            from backend.run_service import ActiveSessionRunsError

            raise ActiveSessionRunsError(["new-active-run"])

    def override_get_db():
        yield storage

    monkeypatch.setattr(main_module, "run_store", RacingRunStore())
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/sessions/race-session")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["run_ids"] == ["new-active-run"]
    assert storage.get(ChatSession, "race-session") is not None
    assert [log.prompt for log in storage.get_audit_logs()] == ["keep until purge succeeds"]


@pytest.mark.asyncio
async def test_delete_session_unlink_failure_reports_partial_progress(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    storage.add(ChatSession(id="unlink-failure", title="Unlink"))
    storage.add(
        LLMAuditLog(
            provider="test",
            model="test",
            prompt="private",
            response="private",
            session_id="unlink-failure",
        )
    )
    storage.commit()
    run_store = RunStore(str(workspace))
    completed = run_store.create_run(
        owner_id="ambient-agent:unlink-failure",
        action_id="chat",
        action_title="Completed chat",
        source_type="chat",
        source_id="unlink-failure",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "private"},
        status="succeeded",
    )

    def fail_unlink(_session_id):
        raise OSError("simulated unlink failure")

    def override_get_db():
        yield storage

    monkeypatch.setattr(storage, "delete_session", fail_unlink)
    monkeypatch.setattr(main_module, "run_store", run_store)
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.delete("/api/sessions/unlink-failure")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "session_delete_incomplete",
        "message": "Conversation deletion is incomplete; retry to remove its session file",
        "removed": {"session": False, "runs": 1, "audit_logs": 1},
    }
    assert storage.get(ChatSession, "unlink-failure") is not None
    assert run_store.get_run(completed["id"]) is None
    assert storage.get_audit_logs() == []
