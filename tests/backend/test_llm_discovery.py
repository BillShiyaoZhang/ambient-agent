import pytest

from backend.llm_config import LLMConfigStore
from backend.llm_discovery import discover_models, test_provider as check_provider
from backend.llm_service import LLMResult


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": [
                {"id": "MiniMax-M2.7", "name": "MiniMax M2.7"},
                {"id": "MiniMax-M2.7", "name": "duplicate"},
                {"id": "speech-2.8-hd", "name": "Speech"},
            ]
        }


class _Client:
    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return _Response()


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["minimax", "minimaxi"])
async def test_minimax_discovery_keeps_unique_agent_chat_models(tmp_path, monkeypatch, preset):
    monkeypatch.setattr("backend.llm_discovery.httpx.AsyncClient", _Client)
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {
            "id": preset,
            "name": preset,
            "preset": preset,
            "models": [
                {"id": "speech-02-hd", "source": "catalog"},
                {"id": "MiniMax-M2.5", "source": "manual"},
            ],
        },
        {"api_key": {"source": "stored", "value": "sk-cp-test"}},
    )

    models = await discover_models(store, preset)

    assert [model["id"] for model in models] == ["MiniMax-M2.5", "MiniMax-M2.7"]


@pytest.mark.asyncio
async def test_connection_test_prefers_provider_default_over_first_non_chat_model(tmp_path, monkeypatch):
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {
            "id": "minimaxi-cn",
            "name": "MiniMax CN",
            "preset": "minimaxi",
            "models": [
                {"id": "speech-02-hd", "source": "catalog"},
                {"id": "MiniMax-M3", "source": "catalog", "capabilities": {"tool_calling": True}},
            ],
        },
        {"api_key": {"source": "stored", "value": "sk-cp-test"}},
    )
    store.update_settings({"default_model": {"provider_id": "minimaxi-cn", "model_id": "MiniMax-M3"}})
    seen = {}

    async def fake_generate(_self, selection, _messages, _tools=None):
        seen["model_id"] = selection.model_id
        return LLMResult(text="OK")

    monkeypatch.setattr("backend.llm_discovery.LLMService.generate", fake_generate)

    result = await check_provider(store, "minimaxi-cn")

    assert result["ok"] is True
    assert result["model_id"] == "MiniMax-M3"
    assert seen["model_id"] == "MiniMax-M3"


@pytest.mark.asyncio
async def test_rediscovery_preserves_saved_model_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.llm_discovery.httpx.AsyncClient", _Client)
    store = LLMConfigStore(str(tmp_path))
    saved_model = {
        "id": "MiniMax-M2.7",
        "display_name": "My verified model",
        "api_mode": "responses",
        "source": "manual",
        "capabilities": {"tool_calling": True, "vision": False, "verification": "verified"},
    }
    store.create_provider(
        {"id": "minimax", "name": "MiniMax", "preset": "minimax", "models": [saved_model]},
        {"api_key": {"source": "stored", "value": "test-key"}},
    )
    store.update_settings({"default_model": {"provider_id": "minimax", "model_id": saved_model["id"]}})

    models = await discover_models(store, "minimax")

    model = next(item for item in models if item["id"] == saved_model["id"])
    assert model["api_mode"] == "responses"
    assert model["display_name"] == saved_model["display_name"]
    assert model["source"] == "manual"
    assert model["capabilities"]["verification"] == "verified"
    assert model["capabilities"]["tool_calling"] is True
    assert model["capabilities"]["vision"] is False
    assert store.get_settings()["default_model"]["model_id"] == saved_model["id"]
    assert len([item for item in models if item["id"] == saved_model["id"]]) == 1


@pytest.mark.asyncio
async def test_rediscovery_merges_edits_saved_while_request_is_pending(tmp_path, monkeypatch):
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {"id": "minimax", "name": "MiniMax", "preset": "minimax", "models": []},
        {"api_key": {"source": "stored", "value": "test-key"}},
    )

    class EditingClient(_Client):
        async def get(self, *_args, **_kwargs):
            store.update_provider(
                "minimax",
                {"models": [{"id": "MiniMax-M2.7", "api_mode": "responses", "source": "manual"}]},
                None,
            )
            return _Response()

    monkeypatch.setattr("backend.llm_discovery.httpx.AsyncClient", EditingClient)
    models = await discover_models(store, "minimax")
    assert models[0]["api_mode"] == "responses"
    assert models[0]["source"] == "manual"


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["connection", "credentials", "preset", "enabled"])
async def test_discovery_discards_stale_provider_response(tmp_path, monkeypatch, changed):
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {
            "id": "review",
            "name": "Review",
            "preset": "openai",
            "models": [],
            "connection": {"base_url": "https://old.example/v1"},
        },
        {"api_key": {"source": "stored", "value": "old-test-key"}},
    )

    class EditingClient(_Client):
        async def get(self, *_args, **_kwargs):
            changes = {"models": []}
            credentials = None
            if changed == "connection":
                changes["connection"] = {"base_url": "https://new.example/v1"}
            elif changed == "preset":
                changes["preset"] = "openai_responses"
            elif changed == "enabled":
                changes["enabled"] = False
            else:
                credentials = {"api_key": {"source": "stored", "value": "new-test-key"}}
            store.update_provider("review", changes, credentials)
            return _Response()

    monkeypatch.setattr("backend.llm_discovery.httpx.AsyncClient", EditingClient)
    assert await discover_models(store, "review") == []
    assert store.get_provider("review").models == []
