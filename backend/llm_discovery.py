"""Provider model discovery and connection checks."""

from __future__ import annotations

import sys
from typing import Any

import httpx

from backend.coding_agent_runtime import CodingAgentRuntime, CodingAgentRuntimeError
from backend.llm_config import LLMConfigError, LLMConfigStore, ModelRef, ModelSelection, ProviderProfile
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


def _merge_native_catalog(
    provider_id: str,
    catalog: Any,
    existing: list[ModelRef],
    *,
    require_models: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(catalog, list) or len(catalog) > 1000 or (require_models and not catalog):
        raise LLMConfigError("Native model catalog is empty or invalid", code="llm_provider_error")
    discovered: dict[str, dict[str, Any]] = {}
    previous = {model.id: model for model in existing}
    for item in catalog:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not item["id"].strip()
            or len(item["id"]) > 256
        ):
            raise LLMConfigError("Native model catalog is invalid", code="llm_provider_error")
        model_id = item["id"].strip()
        if model_id in discovered:
            continue
        model = previous.get(model_id)
        data = model.model_dump(mode="json") if model else {"id": model_id, "source": "discovered"}
        data.update(provider_id=provider_id, model_id=model_id, api_mode="codex_native")
        if model is None or model.source != "manual":
            data["display_name"] = item.get("name") if isinstance(item.get("name"), str) else model_id
        data["availability"] = item.get("availability")
        try:
            discovered[model_id] = ModelRef.model_validate(data).model_dump(mode="json")
        except ValueError:
            raise LLMConfigError("Native model catalog is invalid", code="llm_provider_error") from None
    for model in existing:
        if model.id not in discovered:
            data = model.model_dump(mode="json")
            data["availability"] = {
                "native_inference": False,
                "coding": False,
                "reason": "coding_catalog_missing",
            }
            discovered[model.id] = data
    return list(discovered.values())


async def _codex_catalog(runtime: CodingAgentRuntime) -> list[dict[str, Any]]:
    """Use one visible coding catalog and independently precheck native inference."""
    try:
        coding_catalog = await runtime.models("codex")
    except CodingAgentRuntimeError as exc:
        code = (
            "llm_auth_failed"
            if exc.code == "coding_agent_auth_required"
            else "llm_provider_unavailable"
            if exc.code == "coding_agent_not_installed"
            else "llm_provider_error"
        )
        raise LLMConfigError("Unable to load managed Codex models", code=code) from None
    coding_models = coding_catalog.get("models") if isinstance(coding_catalog, dict) else None
    if not isinstance(coding_models, list) or not coding_models or len(coding_models) > 1000:
        raise LLMConfigError("Coding model catalog is empty or invalid", code="llm_provider_error")
    visible = []
    for model in coding_models:
        if not isinstance(model, dict):
            raise LLMConfigError("Coding model catalog is invalid", code="llm_provider_error")
        if model.get("hidden") is True:
            continue
        visible.append({"id": model.get("id"), "name": model.get("display_name")})
    # Validate all current IDs before starting the inference precheck or mutating a Profile.
    _merge_native_catalog("ambient-codex", visible, [], require_models=True)
    from backend.codex_llm import NativeCodexTransport

    native_catalog = await NativeCodexTransport(runtime).model_availability()
    if not isinstance(native_catalog, list) or len(native_catalog) > 1000:
        raise LLMConfigError("Native model catalog is invalid", code="llm_provider_error")
    _merge_native_catalog("ambient-codex", native_catalog, [], require_models=True)
    native_support: dict[str, bool] = {}
    for item in native_catalog:
        if type(item.get("native_inference")) is not bool:
            raise LLMConfigError("Native model availability is invalid", code="llm_provider_error")
        model_id = item["id"].strip()
        native_support[model_id] = native_support.get(model_id, True) and item["native_inference"]
    for item in visible:
        model_id = item["id"].strip()
        native_inference = native_support.get(model_id, False)
        item["availability"] = {
            "native_inference": native_inference,
            "coding": True,
            "reason": None
            if native_inference
            else "native_profile_unsupported"
            if model_id in native_support
            else "native_catalog_missing",
        }
    return visible


def _native_profile(store: LLMConfigStore) -> ProviderProfile | None:
    profiles = [profile for profile in store.list_providers() if profile["preset"] == "codex_native"]
    enabled = next((profile for profile in profiles if profile["enabled"]), None)
    if profiles and enabled is None:
        raise LLMConfigError(
            "Enable the existing Codex model connection before syncing", code="llm_provider_unavailable"
        )
    return store.get_provider(enabled["id"]) if enabled else None


def _login_fingerprint(runtime: CodingAgentRuntime) -> tuple[Any, Any, Any]:
    session = runtime.auth_session("codex")
    return runtime.authentication_generation("codex"), session.get("id"), session.get("status")


async def _stable_codex_login(runtime: CodingAgentRuntime, expected: tuple | None = None) -> tuple[Any, Any, Any]:
    before = _login_fingerprint(runtime)
    status = await runtime.status("codex")
    after = _login_fingerprint(runtime)
    if not status.get("installed") or runtime.command("codex") is None:
        raise LLMConfigError("Install managed Codex before syncing models", code="llm_provider_unavailable")
    if (
        not status.get("authenticated")
        or status.get("auth_state") != "signed_in"
        or before != after
        or (expected is not None and after != expected)
    ):
        raise LLMConfigError(
            "Sign in to managed Codex before syncing models; the login must remain unchanged", code="llm_auth_failed"
        )
    return after


async def sync_codex_connection(store: LLMConfigStore, runtime: CodingAgentRuntime) -> dict[str, Any]:
    """Project the shared managed login into a primary/fast model connection."""
    if sys.platform != "linux":
        raise LLMConfigError(
            "Codex primary/fast models require the verified Linux execution platform",
            code="llm_capability_unsupported",
        )
    profile = _native_profile(store)
    native_generation = store.provider_generation()
    profile_generation = store.provider_generation(profile.id) if profile else None
    try:
        login = await _stable_codex_login(runtime)
        catalog = await _codex_catalog(runtime)
        # Validate the entire directory before any profile creation or mutation.
        _merge_native_catalog("ambient-codex", catalog, [], require_models=True)
        await _stable_codex_login(runtime, login)
    except LLMConfigError:
        raise
    except Exception:
        raise LLMConfigError("Unable to sync managed Codex models", code="llm_provider_error") from None

    # No awaits below: same-worker concurrent syncs see the preceding commit.
    if profile is not None:
        current = store.get_provider(profile.id)
        if (
            store.provider_generation(profile.id) != profile_generation
            or current.preset != profile.preset
            or current.connection != profile.connection
            or current.credential_refs != profile.credential_refs
            or not current.enabled
        ):
            raise LLMConfigError(
                "The Codex model connection changed during sync; retry after reviewing it",
                code="llm_provider_unavailable",
            )
    else:
        current = _native_profile(store)
        generation_change = store.provider_generation() - native_generation
        if (current is None and generation_change) or (current is not None and generation_change > 1):
            raise LLMConfigError(
                "The Codex model connection changed during sync; retry after reviewing it",
                code="llm_provider_unavailable",
            )
    if current is not None:
        models = _merge_native_catalog(current.id, catalog, current.models)
        return store.update_provider(current.id, {"models": models}, None)
    provider_ids = {provider["id"] for provider in store.list_providers()}
    provider_id = "ambient-codex"
    suffix = 2
    while provider_id in provider_ids:
        provider_id = f"ambient-codex-{suffix}"
        suffix += 1
    return store.create_provider(
        {
            "id": provider_id,
            "name": "Codex (managed login)",
            "preset": "codex_native",
            "models": _merge_native_catalog(provider_id, catalog, []),
        },
        {},
    )


async def discover_models(
    store: LLMConfigStore, provider_id: str, *, runtime: CodingAgentRuntime | None = None
) -> list[dict[str, Any]]:
    profile, preset, credentials = store.provider_runtime(provider_id)
    strategy = preset.get("discovery")
    if strategy == "codex_native":
        provider_generation = store.provider_generation(provider_id)
        runtime = runtime or CodingAgentRuntime(store.workspace_dir)
        try:
            login = await _stable_codex_login(runtime)
            catalog = await _codex_catalog(runtime)
            await _stable_codex_login(runtime, login)
        except LLMConfigError:
            raise
        except Exception:
            raise LLMConfigError("Unable to discover managed Codex models", code="llm_provider_error") from None
        current = store.get_provider(provider_id)
        if (
            store.provider_generation(provider_id) != provider_generation
            or current.preset != profile.preset
            or current.connection != profile.connection
            or current.credential_refs != profile.credential_refs
            or current.enabled != profile.enabled
        ):
            return [model.model_dump(mode="json") for model in current.models]
        models = _merge_native_catalog(provider_id, catalog, current.models)
        store.update_provider(provider_id, {"models": models}, None)
        return models
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

    # The network request may have outlived an edit in the settings UI.
    current = store.get_provider(provider_id)
    if (
        current.preset != profile.preset
        or current.connection != profile.connection
        or current.credential_refs != profile.credential_refs
        or current.enabled != profile.enabled
    ):
        return [model.model_dump(mode="json") for model in current.models]
    current, _, current_credentials = store.provider_runtime(provider_id)
    if current_credentials != credentials:
        return [model.model_dump(mode="json") for model in current.models]
    merged: dict[str, dict[str, Any]] = {item.id: item.model_dump(mode="json") for item in current.models}
    for item in discovered:
        merged.setdefault(item["id"], item)
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
    runtime: CodingAgentRuntime | None = None,
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
        available = [
            model
            for model in profile.models
            if profile.preset != "codex_native" or model.availability is None or model.availability.native_inference
        ]
        compatible = next((model for model in available if model.capabilities.tool_calling is True), None)
        chosen = compatible.id if compatible else (available[0].id if available else None)
    if not chosen:
        models = await discover_models(store, provider_id, runtime=runtime)
        chosen = next(
            (
                model["id"]
                for model in models
                if profile.preset != "codex_native"
                or (model.get("availability") or {}).get("native_inference") is not False
            ),
            None,
        )
        if not chosen and profile.preset == "codex_native" and models:
            return {
                "ok": False,
                "code": "llm_capability_unsupported",
                "message": "No Codex model compatible with primary/fast inference is available",
            }
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
