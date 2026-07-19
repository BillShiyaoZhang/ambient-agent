import json
import stat

import pytest

from backend.llm_config import (
    LLMConfigStore,
    LLMConfigurationRequired,
    ModelSelection,
)


def test_catalog_covers_common_cloud_china_enterprise_local_and_generic_presets(tmp_path):
    store = LLMConfigStore(str(tmp_path))

    catalog = {item["id"]: item for item in store.catalog()}

    assert {
        "openai",
        "anthropic",
        "gemini",
        "openrouter",
        "minimax",
        "minimaxi",
        "moonshot",
        "dashscope",
        "azure",
        "bedrock",
        "vertex_ai",
        "ollama",
        "lmstudio",
        "openai_compatible",
        "openai_responses",
        "anthropic_compatible",
        "litellm_proxy",
        "custom_litellm",
    } <= catalog.keys()
    assert any(field["id"] == "api_key" and field["secret"] for field in catalog["openai"]["fields"])
    assert any(field["id"] == "base_url" for field in catalog["openai_compatible"]["fields"])


def test_minimax_global_and_china_use_separate_compatible_endpoints(tmp_path):
    store = LLMConfigStore(str(tmp_path))
    catalog = {item["id"]: item for item in store.catalog()}

    assert catalog["minimax"]["category"] == "global"
    assert catalog["minimax"]["default_base_url"] == "https://api.minimax.io/v1"
    assert catalog["minimaxi"]["category"] == "china"
    assert catalog["minimaxi"]["default_base_url"] == "https://api.minimaxi.com/v1"

    for preset, expected_base in (
        ("minimax", "https://api.minimax.io/v1"),
        ("minimaxi", "https://api.minimaxi.com/v1"),
    ):
        provider_id = f"{preset}-token-plan"
        store.create_provider(
            {
                "id": provider_id,
                "name": provider_id,
                "preset": preset,
                "models": [{"id": "MiniMax-M2.7"}],
            },
            {"api_key": {"source": "stored", "value": "sk-cp-test"}},
        )
        resolved = store.resolve(ModelSelection(provider_id=provider_id, model_id="MiniMax-M2.7"))
        assert resolved.litellm_model == "openai/MiniMax-M2.7"
        assert resolved.connection["base_url"] == expected_base


def test_pre_split_minimax_profile_migrates_to_china_without_touching_credentials(tmp_path):
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    (llm_dir / "config.json").write_text(
        json.dumps({
            "version": 1,
            "providers": [{
                "id": "old-minimax",
                "name": "MiniMax",
                "preset": "minimax",
                "connection": {},
                "credential_refs": {"api_key": {"source": "stored"}},
                "models": [{"id": "MiniMax-M2.7"}],
            }],
            "settings": {"default_model": None, "fast_model": None},
        }),
        encoding="utf-8",
    )
    (llm_dir / "secrets.json").write_text(
        json.dumps({"old-minimax:api_key": "sk-cp-cn"}),
        encoding="utf-8",
    )

    store = LLMConfigStore(str(tmp_path))
    resolved = store.resolve(ModelSelection(provider_id="old-minimax", model_id="MiniMax-M2.7"))

    assert resolved.preset == "minimaxi"
    assert resolved.connection["base_url"] == "https://api.minimaxi.com/v1"
    assert resolved.credentials["api_key"] == "sk-cp-cn"
    persisted = json.loads((llm_dir / "config.json").read_text(encoding="utf-8"))
    assert persisted["version"] == 3
    assert persisted["providers"][0]["preset"] == "minimaxi"


def test_minimax_migration_removes_non_chat_catalog_models_but_keeps_selected_model(tmp_path):
    llm_dir = tmp_path / "llm"
    llm_dir.mkdir()
    (llm_dir / "config.json").write_text(
        json.dumps({
            "version": 2,
            "providers": [{
                "id": "minimaxi-cn",
                "name": "MiniMax CN",
                "preset": "minimaxi",
                "connection": {},
                "models": [
                    {"id": "speech-02-hd", "source": "catalog"},
                    {"id": "MiniMax-M3", "source": "catalog"},
                ],
            }],
            "settings": {
                "default_model": {"provider_id": "minimaxi-cn", "model_id": "MiniMax-M3"},
                "fast_model": None,
            },
        }),
        encoding="utf-8",
    )

    store = LLMConfigStore(str(tmp_path))

    assert [model["id"] for model in store.list_providers()[0]["models"]] == ["MiniMax-M3"]


def test_stored_secret_is_separated_redacted_and_mode_0600(tmp_path):
    store = LLMConfigStore(str(tmp_path))
    public = store.create_provider(
        {
            "id": "personal-openai",
            "name": "Personal OpenAI",
            "preset": "openai",
            "models": [{"id": "gpt-test", "display_name": "GPT Test"}],
        },
        {"api_key": {"source": "stored", "value": "sk-super-secret"}},
    )

    config_text = (tmp_path / "llm" / "config.json").read_text(encoding="utf-8")
    secret_file = tmp_path / "llm" / "secrets.json"
    assert "sk-super-secret" not in config_text
    assert "sk-super-secret" in secret_file.read_text(encoding="utf-8")
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert public["credentials"]["api_key"] == {
        "source": "stored",
        "configured": True,
        "masked": "••••cret",
    }
    assert "value" not in public["credentials"]["api_key"]


def test_environment_credential_is_resolved_without_copying_value(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "from-environment")
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {
            "id": "gateway",
            "name": "Gateway",
            "preset": "openai_compatible",
            "connection": {"base_url": "https://example.test/v1"},
            "models": [{"id": "model-a"}],
        },
        {"api_key": {"source": "env", "env_var": "TEST_PROVIDER_KEY"}},
    )

    resolved = store.resolve(ModelSelection(provider_id="gateway", model_id="model-a"))

    assert resolved.credentials["api_key"] == "from-environment"
    assert "from-environment" not in (tmp_path / "llm" / "config.json").read_text(encoding="utf-8")
    assert "from-environment" not in (tmp_path / "llm" / "secrets.json").read_text(encoding="utf-8")


def test_default_and_fast_model_resolution_and_missing_configuration(tmp_path):
    store = LLMConfigStore(str(tmp_path))
    with pytest.raises(LLMConfigurationRequired) as exc:
        store.resolve_default()
    assert exc.value.code == "llm_configuration_required"

    store.create_provider(
        {
            "id": "local",
            "name": "Local",
            "preset": "ollama",
            "connection": {"base_url": "http://localhost:11434"},
            "models": [{"id": "large"}, {"id": "small"}],
        },
        {},
    )
    store.update_settings(
        {
            "default_model": {"provider_id": "local", "model_id": "large"},
            "fast_model": {"provider_id": "local", "model_id": "small"},
        }
    )

    assert store.resolve_default().model_id == "large"
    assert store.resolve_fast().model_id == "small"


def test_patch_omitted_credential_keeps_it_and_clear_removes_it(tmp_path):
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {"id": "p", "name": "P", "preset": "openai", "models": [{"id": "m"}]},
        {"api_key": {"source": "stored", "value": "secret"}},
    )

    store.update_provider("p", {"name": "Renamed"}, None)
    assert store.resolve(ModelSelection(provider_id="p", model_id="m")).credentials["api_key"] == "secret"

    store.update_provider("p", {}, {"api_key": {"clear": True}})
    assert "api_key" not in store.resolve(ModelSelection(provider_id="p", model_id="m")).credentials
    data = json.loads((tmp_path / "llm" / "secrets.json").read_text(encoding="utf-8"))
    assert data == {}
