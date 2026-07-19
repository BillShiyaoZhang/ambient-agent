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
