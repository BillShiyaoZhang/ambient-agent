from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from fastapi.testclient import TestClient

import backend.main as main_module
from backend.llm_config import LLMConfigStore
from backend.llm_service import LLMTransportError
from backend.coding_agent_runtime import CodingAgentRuntime
from backend.models import ChatSession
from backend.workspace_storage import WorkspaceStorage
from backend.coding_agent import CodingAgentConfigStore, CodingAgentConfigError


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
        assert (
            client.post(
                "/api/sessions",
                json={"id": session_id, "title": "Needs model", "language": "en"},
            ).status_code
            == 200
        )
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


def test_codex_native_provider_api_and_primary_selection(tmp_path, monkeypatch):
    _, store = _isolate_llm(tmp_path, monkeypatch)
    with TestClient(main_module.app) as client:
        catalog = client.get("/api/llm/catalog").json()
        native = next(item for item in catalog if item["id"] == "codex_native")
        assert native["fields"] == native["advanced_fields"] == []
        response = client.post(
            "/api/llm/providers",
            json={
                "profile": {
                    "id": "native",
                    "name": "Native",
                    "preset": "codex_native",
                    "models": [{"id": "gpt-5.6-luna"}],
                }
            },
        )
        assert response.status_code == 201
        assert response.json()["credentials"] == response.json()["connection"] == {}
        selected = client.patch(
            "/api/llm/settings", json={"default_model": {"provider_id": "native", "model_id": "gpt-5.6-luna"}}
        )
        assert selected.status_code == 200
    assert store.resolve_default().api_mode == "codex_native"


def test_codex_connection_sync_api_uses_trusted_workspace_state_and_retains_session_binding(tmp_path, monkeypatch):
    storage, store = _isolate_llm(tmp_path, monkeypatch)
    store.create_provider({"id": "api", "name": "API", "preset": "ollama", "models": [{"id": "api-model"}]}, {})
    storage.add(
        ChatSession(id="bound-chat", title="Bound", model_selection={"provider_id": "api", "model_id": "api-model"})
    )
    storage.commit()
    coding = CodingAgentConfigStore(store.workspace_dir)
    monkeypatch.setattr(main_module, "coding_agent_config_store", coding)
    calls = []

    async def sync(selected_store, runtime):
        calls.append((selected_store, runtime))
        return selected_store.create_provider(
            {"id": "ambient-codex", "name": "Codex", "preset": "codex_native", "models": [{"id": "primary-model"}]}, {}
        )

    monkeypatch.setattr(main_module, "sync_codex_connection", sync, raising=False)
    before_binding = storage.get(ChatSession, "bound-chat").model_selection
    with TestClient(main_module.app) as client:
        response = client.post(
            "/api/llm/connections/codex/sync", json={"command": "private-marker", "auth_path": "private-marker"}
        )
    assert response.status_code == 200
    assert calls == [(store, coding.runtime)]
    assert response.json()["credentials"] == response.json()["connection"] == {}
    assert "private-marker" not in response.text
    assert storage.get(ChatSession, "bound-chat").model_selection == before_binding
    assert store.get_settings() == {"default_model": None, "fast_model": None}


@pytest.mark.parametrize(
    "code,status",
    [
        ("llm_auth_failed", 401),
        ("llm_provider_unavailable", 422),
        ("llm_capability_unsupported", 422),
        ("llm_provider_error", 502),
    ],
)
def test_codex_connection_sync_api_reports_structured_failure(monkeypatch, code, status):
    async def fail_sync(*_args):
        raise LLMTransportError("Unable to sync managed Codex models", code=code)

    monkeypatch.setattr(main_module, "sync_codex_connection", fail_sync, raising=False)
    with TestClient(main_module.app) as client:
        response = client.post("/api/llm/connections/codex/sync")
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


@pytest.mark.parametrize("action", ["discover-models", "test"])
def test_native_discovery_api_uses_managed_runtime_login_generation(tmp_path, monkeypatch, action):
    _, store = _isolate_llm(tmp_path, monkeypatch)
    store.create_provider({"id": "native", "name": "Native", "preset": "codex_native"}, {})
    coding = CodingAgentConfigStore(store.workspace_dir)
    monkeypatch.setattr(main_module, "coding_agent_config_store", coding)
    monkeypatch.setattr(CodingAgentRuntime, "command", lambda _runtime, _agent_id: ["managed-codex"])
    monkeypatch.setattr(
        CodingAgentRuntime,
        "status",
        AsyncMock(return_value={"installed": True, "authenticated": True, "auth_state": "signed_in"}),
    )
    monkeypatch.setattr(CodingAgentRuntime, "_run_probe", AsyncMock(return_value=(0, "signed out")))
    monkeypatch.setattr(
        CodingAgentRuntime,
        "models",
        AsyncMock(return_value={"models": [{"id": "shared", "display_name": "Shared"}]}),
    )
    login = {"id": "same-account", "status": "signed_in"}
    coding.runtime._auth_sessions["codex"] = login.copy()
    selected_runtimes = []

    class NativeTransport:
        def __init__(self, runtime):
            selected_runtimes.append(runtime)

        async def model_availability(self):
            # Actual managed logout increments generation, even if a subsequent
            # login presents the same account/status by the next status probe.
            await coding.runtime.logout("codex")
            coding.runtime._auth_sessions["codex"] = login.copy()
            return [{"id": "shared", "native_inference": True}]

    async def generate(*_args, **_kwargs):
        from backend.llm_service import LLMResult

        return LLMResult(text="OK")

    monkeypatch.setattr("backend.codex_llm.NativeCodexTransport", NativeTransport)
    monkeypatch.setattr("backend.llm_discovery.LLMService.generate", generate)
    before = store.config_path.read_bytes()
    with TestClient(main_module.app) as client:
        response = client.post(f"/api/llm/providers/native/{action}", json={})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "llm_auth_failed"
    assert selected_runtimes == [coding.runtime]
    assert store.config_path.read_bytes() == before


def test_native_connection_test_api_reports_a_catalog_without_compatible_inference_models(tmp_path, monkeypatch):
    _, store = _isolate_llm(tmp_path, monkeypatch)
    profile = store.create_provider(
        {
            "id": "native",
            "name": "Native",
            "preset": "codex_native",
            "models": [
                {
                    "id": "coding-only",
                    "availability": {"native_inference": False, "coding": True, "reason": "native_catalog_missing"},
                }
            ],
        },
        {},
    )
    monkeypatch.setattr("backend.llm_discovery.discover_models", AsyncMock(return_value=profile["models"]))
    with TestClient(main_module.app) as client:
        response = client.post("/api/llm/providers/native/test", json={})
    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "code": "llm_capability_unsupported",
        "message": "No Codex model compatible with primary/fast inference is available",
    }


@pytest.mark.parametrize(
    "injection",
    [
        {"profile": {"connection": {"auth_path": "private-marker"}}},
        {"profile": {"command": "private-marker"}},
        {"profile": {"models": [{"id": "gpt-5.6-luna", "api_mode": "responses"}]}},
        {"credentials": {"api_key": {"source": "stored", "value": "private-marker"}}},
    ],
)
def test_codex_native_provider_api_rejects_injections_safely(tmp_path, monkeypatch, injection):
    _, store = _isolate_llm(tmp_path, monkeypatch)
    payload = {"profile": {"id": "native", "name": "Native", "preset": "codex_native"}}
    payload["profile"].update(injection.get("profile", {}))
    if "credentials" in injection:
        payload["credentials"] = injection["credentials"]
    with TestClient(main_module.app) as client:
        response = client.post("/api/llm/providers", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "llm_invalid_configuration"
    assert "private-marker" not in response.text
    assert store.list_providers() == []


def test_codex_native_primary_cannot_be_inherited_by_opencode_before_run(tmp_path, monkeypatch):
    _, store = _isolate_llm(tmp_path, monkeypatch)
    store.create_provider(
        {"id": "native", "name": "Native", "preset": "codex_native", "models": [{"id": "gpt-5.6-luna"}]}, {}
    )
    store.update_settings({"default_model": {"provider_id": "native", "model_id": "gpt-5.6-luna"}})
    coding = CodingAgentConfigStore(store.workspace_dir)
    monkeypatch.setattr(main_module, "coding_agent_config_store", coding)
    with pytest.raises(CodingAgentConfigError) as failure:
        main_module._snapshot_model_config(ChatSession(id="native-chat", title="Native"))
    assert failure.value.code == "coding_agent_model_binding_unsupported"
    coding.update_settings({"default_agent": "codex"})
    snapshot = main_module._snapshot_model_config(ChatSession(id="native-chat", title="Native"))
    assert snapshot["primary"]["model_id"] == "gpt-5.6-luna"
    assert snapshot["coding_model"] is None


def test_explicit_opencode_codex_native_binding_is_rejected_by_api(tmp_path, monkeypatch):
    _, store = _isolate_llm(tmp_path, monkeypatch)
    store.create_provider(
        {"id": "native", "name": "Native", "preset": "codex_native", "models": [{"id": "gpt-5.6-luna"}]}, {}
    )
    monkeypatch.setattr(main_module, "coding_agent_config_store", CodingAgentConfigStore(store.workspace_dir))
    with TestClient(main_module.app) as client:
        response = client.patch(
            "/api/coding-agents/opencode/model",
            json={"mode": "shared_binding", "provider_id": "native", "model_id": "gpt-5.6-luna"},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "coding_agent_model_binding_unsupported"


def test_opencode_native_chat_rejection_creates_no_run(tmp_path, monkeypatch):
    storage, store = _isolate_llm(tmp_path, monkeypatch)
    store.create_provider(
        {"id": "native", "name": "Native", "preset": "codex_native", "models": [{"id": "gpt-5.6-luna"}]}, {}
    )
    store.update_settings({"default_model": {"provider_id": "native", "model_id": "gpt-5.6-luna"}})
    monkeypatch.setattr(main_module, "coding_agent_config_store", CodingAgentConfigStore(store.workspace_dir))
    session_id = f"native-binding-{uuid4().hex}"
    storage.add(ChatSession(id=session_id, title="Native binding"))
    storage.commit()
    with TestClient(main_module.app) as client:
        before = {run["id"] for run in main_module.run_store.list_runs(limit=500)}
        with client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
            assert websocket.receive_json()["type"] == "active_sessions_list"
            websocket.send_json({"sender": "user", "content": "hello"})
            assert websocket.receive_json()["type"] == "ack"
            error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "coding_agent_model_binding_unsupported"
        assert {run["id"] for run in main_module.run_store.list_runs(limit=500)} == before
