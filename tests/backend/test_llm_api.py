from uuid import uuid4

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.llm_config import LLMConfigStore
from backend.llm_service import LLMTransportError
from backend.models import ChatSession
from backend.workspace_storage import WorkspaceStorage


def _isolate_llm(tmp_path, monkeypatch):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    store = LLMConfigStore(storage.workspace_dir)
    monkeypatch.setattr(main_module, "db_storage", storage)
    monkeypatch.setattr(main_module, "llm_config_store", store)
    return storage, store


def test_provider_api_never_returns_secret_and_session_model_persists(tmp_path, monkeypatch):
    storage, _ = _isolate_llm(tmp_path, monkeypatch)
    storage.add(ChatSession(id="s1", title="Session"))
    storage.commit()

    with TestClient(main_module.app) as client:
        created = client.post(
            "/api/llm/providers",
            json={
                "profile": {
                    "id": "openai-main",
                    "name": "OpenAI",
                    "preset": "openai",
                    "models": [{"id": "gpt-test"}],
                },
                "credentials": {"api_key": {"source": "stored", "value": "sk-never-return"}},
            },
        )
        settings = client.patch(
            "/api/llm/settings",
            json={"default_model": {"provider_id": "openai-main", "model_id": "gpt-test"}},
        )
        selected = client.put(
            "/api/sessions/s1/model",
            json={"provider_id": "openai-main", "model_id": "gpt-test"},
        )
        listed = client.get("/api/llm/providers")

    assert created.status_code == 201
    assert settings.status_code == 200
    assert selected.status_code == 200
    assert listed.status_code == 200
    assert "sk-never-return" not in listed.text
    assert storage.get(ChatSession, "s1").model_selection.model_id == "gpt-test"


def test_delete_referenced_provider_returns_conflict(tmp_path, monkeypatch):
    _, store = _isolate_llm(tmp_path, monkeypatch)
    store.create_provider({"id": "local", "name": "Local", "preset": "ollama", "models": [{"id": "qwen"}]}, {})
    store.update_settings({"default_model": {"provider_id": "local", "model_id": "qwen"}})

    with TestClient(main_module.app) as client:
        response = client.delete("/api/llm/providers/local")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "llm_provider_in_use"


def test_removing_a_session_referenced_model_returns_conflict(tmp_path, monkeypatch):
    storage, store = _isolate_llm(tmp_path, monkeypatch)
    store.create_provider(
        {
            "id": "local",
            "name": "Local",
            "preset": "ollama",
            "models": [{"id": "qwen"}, {"id": "llama"}],
        },
        {},
    )
    storage.add(
        ChatSession(
            id="model-user",
            title="Session",
            model_selection={"provider_id": "local", "model_id": "qwen"},
        )
    )
    storage.commit()

    with TestClient(main_module.app) as client:
        response = client.patch(
            "/api/llm/providers/local",
            json={"profile": {"models": [{"id": "llama"}]}},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "llm_model_in_use"


def test_unconfigured_websocket_run_returns_actionable_error(tmp_path, monkeypatch):
    _isolate_llm(tmp_path, monkeypatch)
    session_id = f"needs-model-{uuid4().hex}"

    with TestClient(main_module.app) as client:
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            assert websocket.receive_json()["type"] == "active_sessions_list"
            websocket.send_json({"sender": "user", "content": "hello"})
            assert websocket.receive_json()["type"] == "ack"
            assert websocket.receive_json()["status"] == "running"
            error = websocket.receive_json()
            if error["type"] == "session_title_updated":
                error = websocket.receive_json()

    assert error["type"] == "llm_error"
    assert error["code"] == "llm_configuration_required"
    assert error["message"] == "Configure a default model before starting a task"
    assert error["action"] == "open_llm_settings"
    assert error["run_id"]


def test_upstream_provider_failure_is_not_reported_as_request_validation_error(monkeypatch):
    async def fail_test(*_args, **_kwargs):
        raise LLMTransportError("The LLM provider request failed", code="llm_provider_error")

    monkeypatch.setattr(main_module, "test_provider", fail_test)
    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/llm/providers/minimax/test",
            json={"model_id": "MiniMax-M2.7", "mode": "connection"},
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "llm_provider_error"
