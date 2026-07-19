"""Provider model discovery and connection checks."""

from __future__ import annotations

from typing import Any

import httpx

from backend.llm_config import LLMConfigStore, ModelRef, ModelSelection
from backend.llm_service import LLMService


_DEFAULT_BASES = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
}


def _model(model_id: str, name: str | None = None, source: str = "discovered") -> dict[str, Any]:
    return ModelRef(id=model_id, display_name=name or model_id, source=source).model_dump(mode="json")


async def discover_models(store: LLMConfigStore, provider_id: str) -> list[dict[str, Any]]:
    profile, preset, credentials = store.provider_runtime(provider_id)
    strategy = preset.get("discovery")
    base_url = (
        profile.connection.get("base_url") or preset.get("default_base_url") or _DEFAULT_BASES.get(profile.preset)
    )
    discovered: list[dict[str, Any]] = []
    headers: dict[str, str] = {}
    key = credentials.get("api_key")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if strategy == "ollama" and base_url:
                response = await client.get(f"{str(base_url).rstrip('/')}/api/tags")
                response.raise_for_status()
                discovered = [_model(item["name"]) for item in response.json().get("models", []) if item.get("name")]
            elif strategy == "openai" and base_url:
                response = await client.get(f"{str(base_url).rstrip('/')}/models", headers=headers)
                response.raise_for_status()
                discovered = [
                    _model(item["id"], item.get("name")) for item in response.json().get("data", []) if item.get("id")
                ]
            elif strategy == "anthropic" and base_url:
                anthropic_headers = {"anthropic-version": "2023-06-01"}
                if key:
                    anthropic_headers["x-api-key"] = key
                response = await client.get(f"{str(base_url).rstrip('/')}/models", headers=anthropic_headers)
                response.raise_for_status()
                discovered = [
                    _model(item["id"], item.get("display_name"))
                    for item in response.json().get("data", [])
                    if item.get("id")
                ]
            elif strategy == "gemini" and base_url and key:
                response = await client.get(f"{str(base_url).rstrip('/')}/models", params={"key": key})
                response.raise_for_status()
                discovered = [
                    _model(str(item["name"]).removeprefix("models/"), item.get("displayName"))
                    for item in response.json().get("models", [])
                    if item.get("name")
                ]
    except (httpx.HTTPError, ValueError, KeyError):
        discovered = []

    merged: dict[str, dict[str, Any]] = {item.id: item.model_dump(mode="json") for item in profile.models}
    for item in discovered:
        merged[item["id"]] = item
    try:
        import litellm

        prefix = preset.get("metadata_prefix") or preset.get("litellm_prefix")
        if prefix:
            if not discovered:
                for model_name in litellm.model_cost:
                    if model_name.startswith(f"{prefix}/"):
                        model_id = model_name.removeprefix(f"{prefix}/")
                        merged.setdefault(model_id, _model(model_id, source="catalog"))
            for model_id, item in merged.items():
                info = litellm.model_cost.get(f"{prefix}/{model_id}") or litellm.model_cost.get(model_id) or {}
                capabilities = item.setdefault("capabilities", {})
                if capabilities.get("tool_calling") is None and info.get("supports_function_calling") is not None:
                    capabilities["tool_calling"] = bool(info["supports_function_calling"])
                if capabilities.get("vision") is None and info.get("supports_vision") is not None:
                    capabilities["vision"] = bool(info["supports_vision"])
                if capabilities.get("reasoning") is None and info.get("supports_reasoning") is not None:
                    capabilities["reasoning"] = bool(info["supports_reasoning"])
                if capabilities.get("context_window") is None and info.get("max_input_tokens"):
                    capabilities["context_window"] = int(info["max_input_tokens"])
    except Exception:
        pass
    allowed_prefixes = tuple(str(item) for item in preset.get("discovered_model_prefixes") or [])
    models = [
        item
        for item in merged.values()
        if not allowed_prefixes
        or item.get("source") == "manual"
        or str(item.get("id", "")).startswith(allowed_prefixes)
    ]
    store.update_provider(provider_id, {"models": models}, None)
    return models


async def test_provider(
    store: LLMConfigStore,
    provider_id: str,
    model_id: str | None = None,
    *,
    test_tools: bool = False,
) -> dict[str, Any]:
    profile = store.get_provider(provider_id)
    chosen = model_id
    if not chosen:
        settings = store.get_settings()
        for setting_name in ("default_model", "fast_model"):
            selection = settings.get(setting_name)
            if selection and selection.get("provider_id") == provider_id:
                chosen = selection.get("model_id")
                break
    if not chosen:
        compatible = next((model for model in profile.models if model.capabilities.tool_calling is True), None)
        chosen = compatible.id if compatible else (profile.models[0].id if profile.models else None)
    if not chosen:
        models = await discover_models(store, provider_id)
        chosen = models[0]["id"] if models else None
    if not chosen:
        return {"ok": False, "code": "llm_model_not_found", "message": "Add a model before testing"}
    tools = None
    prompt = "Reply with OK."
    if test_tools:
        prompt = "Call the ambient_tool_test function with value OK. Do not answer in text."
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "ambient_tool_test",
                    "description": "Validate function calling",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                },
            }
        ]
    result = await LLMService(store).generate(
        ModelSelection(provider_id=provider_id, model_id=chosen),
        [{"role": "user", "content": prompt}],
        tools,
    )
    if test_tools:
        passed = bool(result.tool_calls)
        current = store.get_provider(provider_id)
        models = []
        for model in current.models:
            data = model.model_dump(mode="json")
            if model.id == chosen:
                data["capabilities"]["tool_calling"] = passed
                data["capabilities"]["verification"] = "verified" if passed else "unsupported"
            models.append(data)
        store.update_provider(provider_id, {"models": models}, None)
        return {
            "ok": passed,
            "model_id": chosen,
            "code": None if passed else "llm_capability_unsupported",
            "message": "Tool call verified" if passed else "Model did not return a tool call",
        }
    return {"ok": True, "model_id": chosen, "message": result.text[:200]}
