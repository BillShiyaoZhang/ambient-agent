import json

from backend.llm_config import LLMConfigStore, ModelSelection
from backend.llm_runtime import use_model_selections
from backend.llm_service import set_default_llm_store
from backend.opencode_service import _opencode_runtime_env


def test_opencode_runtime_config_uses_run_model_snapshot_and_private_credentials(tmp_path, monkeypatch):
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {
            "id": "company-endpoint",
            "name": "Company Endpoint",
            "preset": "openai_compatible",
            "connection": {"base_url": "https://llm.example.test/v1", "headers": {"X-Tenant": "acme"}},
            "models": [{"id": "coder-model"}],
        },
        {"api_key": {"source": "stored", "value": "private-key"}},
    )
    set_default_llm_store(store)
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"permission":{"edit":"ask"}}')

    selection = ModelSelection(provider_id="company-endpoint", model_id="coder-model")
    with use_model_selections(selection):
        environment = _opencode_runtime_env()

    config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == "ambient-company-endpoint/coder-model"
    assert config["permission"] == {"edit": "ask"}
    assert config["provider"]["ambient-company-endpoint"] == {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Company Endpoint",
        "options": {
            "apiKey": "private-key",
            "baseURL": "https://llm.example.test/v1",
            "headers": {"X-Tenant": "acme"},
        },
        "models": {"coder-model": {"name": "coder-model"}},
    }
