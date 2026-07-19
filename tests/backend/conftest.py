import pytest

import backend.main as main
from backend.llm_config import LLMConfigStore
from backend.llm_service import set_default_llm_store


@pytest.fixture(autouse=True)
def isolated_main_llm_configuration(tmp_path, monkeypatch):
    """Give integration tests an explicit registry instead of legacy env defaults."""
    previous = main.llm_config_store
    store = LLMConfigStore(str(tmp_path / "llm-workspace"))
    store.create_provider(
        {
            "id": "test-provider",
            "name": "Test Provider",
            "preset": "openai",
            "models": [{"id": "test-model", "capabilities": {"tool_calling": True}}],
        },
        {},
    )
    store.update_settings(
        {
            "default_model": {"provider_id": "test-provider", "model_id": "test-model"},
            "fast_model": {"provider_id": "test-provider", "model_id": "test-model"},
        }
    )
    monkeypatch.setattr(main, "llm_config_store", store)

    set_default_llm_store(store)
    yield store
    set_default_llm_store(previous)
