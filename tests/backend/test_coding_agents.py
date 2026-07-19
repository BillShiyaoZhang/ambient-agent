import re

import pytest
from fastapi.testclient import TestClient

import backend.main as main_module
import backend.codex_service as codex_module
from backend.coding_agent import CodingAgentConfigStore
from backend.codex_service import _codex_environment, _event_update, run_codex_agent
from backend.opencode_service import OpenCodeStagedResult
import scripts.codex_host_bridge as bridge_module


def test_coding_agent_settings_persist_and_catalog_reports_commands(tmp_path, monkeypatch):
    store = CodingAgentConfigStore(tmp_path / "workspace")
    monkeypatch.setenv("OPENCODE_COMMAND", "missing-opencode-test-binary")

    assert store.get_settings() == {"default_agent": "opencode"}
    assert store.update_settings({"default_agent": "codex"}) == {"default_agent": "codex"}
    assert CodingAgentConfigStore(tmp_path / "workspace").get_settings() == {"default_agent": "codex"}
    assert {item["id"] for item in store.catalog()} == {"opencode", "codex"}
    assert all(item["available"] is False for item in store.catalog())
    codex = next(item for item in store.catalog() if item["id"] == "codex")
    assert codex["auth_mode"] == "codex_native"
    assert codex["uses_run_model"] is False
    assert codex["execution_target"] == "host"
    assert codex["command_env"] == "CODEX_HOST_COMMAND"


def test_codex_environment_excludes_ambient_provider_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "global-provider-secret")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "global-provider-config")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "native-codex-token")

    environment = _codex_environment()

    assert "OPENAI_API_KEY" not in environment
    assert "OPENCODE_CONFIG_CONTENT" not in environment
    assert environment["CODEX_ACCESS_TOKEN"] == "native-codex-token"


def test_coding_agent_api_lists_and_updates_selection(tmp_path, monkeypatch):
    store = CodingAgentConfigStore(tmp_path / "workspace")
    monkeypatch.setattr(main_module, "coding_agent_config_store", store)

    with TestClient(main_module.app) as client:
        listed = client.get("/api/coding-agents")
        updated = client.patch("/api/coding-agents/settings", json={"default_agent": "codex"})
        invalid = client.patch("/api/coding-agents/settings", json={"default_agent": "unknown"})

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["agents"]] == ["opencode", "codex"]
    assert updated.json() == {"default_agent": "codex"}
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "coding_agent_not_found"


def test_codex_event_projection_extracts_messages_and_progress():
    message, update = _event_update(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Widget complete"}}
    )
    assert message == "Widget complete"
    assert update == "Widget complete"
    assert _event_update(
        {"type": "item.started", "item": {"type": "command_execution", "command": "inspect files"}}
    ) == (None, "\n🛠️ Codex: inspect files")


@pytest.mark.asyncio
async def test_codex_runner_generates_retained_staging_artifact(tmp_path, monkeypatch):
    apps_dir = tmp_path / "apps"
    monkeypatch.setenv("APPS_DIR", str(apps_dir))
    updates = []
    request = {}

    async def fake_host_bridge(**kwargs):
        request.update(kwargs)
        kwargs["staging_dir"].joinpath("controller.js").write_text(
            "export default function App() { return null; }", encoding="utf-8"
        )
        kwargs["on_update"]("done")
        return "done"

    monkeypatch.setattr(codex_module, "_run_codex_via_host_bridge", fake_host_bridge)

    result = await run_codex_agent("codex-widget", "build it", language="en", on_update=updates.append, promote=False)

    assert isinstance(result, OpenCodeStagedResult)
    assert result.output == "done"
    assert result.staging_dir.is_dir()
    assert (result.staging_dir / "controller.js").is_file()
    assert request["app_id"] == "codex-widget"
    assert re.fullmatch(r"\.codex-widget\.staging-[0-9a-f]{32}", request["staging_dir"].name)
    assert "located in the directory '.'" in request["prompt"]
    assert updates[-1] == "done"


def test_host_bridge_rejects_unauthorized_and_escaped_paths(tmp_path, monkeypatch):
    apps_dir = tmp_path / "workspace" / "apps"
    staging = apps_dir / ".safe-widget.staging-0123456789abcdef0123456789abcdef"
    staging.mkdir(parents=True)
    monkeypatch.setenv("AMBIENT_HOST_WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("CODEX_HOST_BRIDGE_TOKEN", "a" * 32)
    monkeypatch.setattr(bridge_module, "_host_codex_command", lambda: ["codex"])

    async def fake_run(*args, **kwargs):
        return "done"

    monkeypatch.setattr(bridge_module, "_run_codex_exec", fake_run)
    body = {
        "app_id": "safe-widget",
        "staging_dir": staging.name,
        "prompt": "build it",
        "timeout_seconds": 10,
    }
    with TestClient(bridge_module.app) as client:
        unauthorized = client.post("/v1/run", json=body)
        escaped = client.post(
            "/v1/run",
            headers={"Authorization": f"Bearer {'a' * 32}"},
            json={**body, "staging_dir": "../../outside"},
        )
        accepted = client.post(
            "/v1/run",
            headers={"Authorization": f"Bearer {'a' * 32}"},
            json=body,
        )

    assert unauthorized.status_code == 401
    assert escaped.status_code == 422
    assert accepted.status_code == 200
    assert '"type": "result"' in accepted.text


def test_run_snapshot_freezes_selected_coding_agent(tmp_path, monkeypatch):
    storage, _ = _configure_model(tmp_path, monkeypatch)
    coding_store = CodingAgentConfigStore(tmp_path / "workspace")
    coding_store.update_settings({"default_agent": "codex"})
    monkeypatch.setattr(main_module, "coding_agent_config_store", coding_store)
    chat = storage.get(main_module.ChatSession, "snapshot-session")

    snapshot = main_module._snapshot_model_config(chat)

    assert snapshot["coding_agent"] == "codex"
    assert snapshot["primary"] == {"provider_id": "local", "model_id": "test-model"}


def _configure_model(tmp_path, monkeypatch):
    from backend.llm_config import LLMConfigStore
    from backend.models import ChatSession
    from backend.workspace_storage import WorkspaceStorage

    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    llm_store = LLMConfigStore(storage.workspace_dir)
    llm_store.create_provider(
        {"id": "local", "name": "Local", "preset": "ollama", "models": [{"id": "test-model"}]},
        {},
    )
    llm_store.update_settings({"default_model": {"provider_id": "local", "model_id": "test-model"}})
    storage.add(ChatSession(id="snapshot-session", title="Snapshot"))
    storage.commit()
    monkeypatch.setattr(main_module, "llm_config_store", llm_store)
    return storage, llm_store
