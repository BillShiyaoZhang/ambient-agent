import asyncio
from types import SimpleNamespace

import pytest

import backend.llm_discovery as discovery
from backend.coding_agent import CodingAgentConfigStore
from backend.coding_agent_runtime import CodingAgentRuntime
from backend.llm_config import LLMConfigError, LLMConfigStore
from backend.llm_service import LLMTransportError


class _Runtime:
    def __init__(self):
        self.installed = True
        self.authenticated = True
        self.auth_state = "signed_in"
        self.session_id = "login-1"
        self.generation = 0
        self.status_calls = 0
        self.coding_catalog = [{"id": "primary-model", "display_name": "Primary model"}]

    def command(self, agent_id):
        assert agent_id == "codex"
        return ["managed-primary-codex"] if self.installed else None

    async def status(self, agent_id):
        assert agent_id == "codex"
        self.status_calls += 1
        return {"installed": self.installed, "authenticated": self.authenticated, "auth_state": self.auth_state}

    def auth_session(self, agent_id):
        assert agent_id == "codex"
        return {"id": self.session_id}

    def authentication_generation(self, agent_id):
        assert agent_id == "codex"
        return self.generation

    async def models(self, agent_id):
        assert agent_id == "codex"
        return {"agent_id": "codex", "source": "agent", "models": self.coding_catalog}


@pytest.fixture
def connection(tmp_path, monkeypatch):
    store = LLMConfigStore(str(tmp_path))
    runtime = _Runtime()
    state = SimpleNamespace(calls=0, catalog=[{"id": "primary-model", "name": "Primary model"}], after=None)

    class Transport:
        def __init__(self, selected_runtime):
            assert selected_runtime is runtime

        async def discover_models(self):
            state.calls += 1
            if state.after:
                await state.after()
            return state.catalog

        async def model_availability(self):
            catalog = await self.discover_models()
            if not isinstance(catalog, list):
                return catalog
            return [
                {**item, "native_inference": item.get("native_inference", True)} if isinstance(item, dict) else item
                for item in catalog
            ]

        async def generate(self, *_args):
            pytest.fail("Connection sync must not run inference")

    monkeypatch.setattr("backend.codex_llm.NativeCodexTransport", Transport)
    monkeypatch.setattr(discovery, "sys", SimpleNamespace(platform="linux"), raising=False)
    return store, runtime, state


@pytest.mark.asyncio
async def test_sync_shows_current_coding_catalog_with_independent_native_compatibility(connection):
    store, runtime, state = connection
    runtime.coding_catalog = [
        {"id": "newest", "display_name": "Newest"},
        {"id": "shared", "display_name": "Shared"},
        {"id": "unsafe", "display_name": "Unsupported profile"},
        {"id": "shared", "display_name": "Duplicate"},
        {"id": "hidden", "display_name": "Hidden", "hidden": True},
    ]
    state.catalog = [
        {"id": "shared", "name": "Old client name", "native_inference": True},
        {"id": "unsafe", "name": "Unsafe", "native_inference": False},
    ]

    result = await discovery.sync_codex_connection(store, runtime)

    assert [model["id"] for model in result["models"]] == ["newest", "shared", "unsafe"]
    assert [model["display_name"] for model in result["models"]] == ["Newest", "Shared", "Unsupported profile"]
    assert [model["availability"] for model in result["models"]] == [
        {"native_inference": False, "coding": True, "reason": "native_catalog_missing"},
        {"native_inference": True, "coding": True, "reason": None},
        {"native_inference": False, "coding": True, "reason": "native_profile_unsupported"},
    ]
    assert all(model["capabilities"]["verification"] == "unknown" for model in result["models"])


@pytest.mark.asyncio
async def test_sync_refreshes_discovered_names_and_availability_preserving_manual_names_and_old_bindings(connection):
    store, runtime, state = connection
    store.create_provider(
        {
            "id": "native",
            "name": "Native",
            "preset": "codex_native",
            "models": [
                {"id": "old", "display_name": "Saved old model"},
                {"id": "shared", "display_name": "Manual name"},
                {"id": "renamed", "display_name": "Old provider name", "source": "discovered"},
            ],
        },
        {},
    )
    store.update_settings({"default_model": {"provider_id": "native", "model_id": "old"}})
    runtime.coding_catalog = [
        {"id": "renamed", "display_name": "Current name"},
        {"id": "shared", "display_name": "Current shared name"},
    ]
    state.catalog = [{"id": "shared", "name": "Shared", "native_inference": True}]

    result = await discovery.sync_codex_connection(store, runtime)

    assert [model["id"] for model in result["models"]] == ["renamed", "shared", "old"]
    assert [model["display_name"] for model in result["models"]] == ["Current name", "Manual name", "Saved old model"]
    assert result["models"][-1]["availability"] == {
        "native_inference": False,
        "coding": False,
        "reason": "coding_catalog_missing",
    }
    assert store.get_settings()["default_model"] == {"provider_id": "native", "model_id": "old"}
    state.catalog = [{"id": "renamed", "name": "Renamed", "native_inference": True}]
    refreshed = await discovery.sync_codex_connection(store, runtime)
    assert refreshed["models"][0]["availability"] == {"native_inference": True, "coding": True, "reason": None}
    assert refreshed["models"][1]["availability"]["native_inference"] is False


@pytest.mark.asyncio
async def test_sync_projects_login_into_one_model_connection_without_binding_changes(connection):
    store, runtime, state = connection
    store.create_provider({"id": "api", "name": "API", "preset": "ollama", "models": [{"id": "api-model"}]}, {})
    store.update_settings({"default_model": {"provider_id": "api", "model_id": "api-model"}})
    coding = CodingAgentConfigStore(store.workspace_dir)
    coding.update_settings({"default_agent": "codex"})
    coding.update_agent_model("codex", {"mode": "native", "native_model": "coding-only-model"})
    before_settings = store.get_settings()
    before_coding = coding.get_settings()
    before_secrets = store.secrets_path.read_bytes()

    first = await discovery.sync_codex_connection(store, runtime)
    second = await discovery.sync_codex_connection(store, runtime)

    assert first == second
    assert first["id"] == "ambient-codex"
    assert first["preset"] == "codex_native"
    assert first["credentials"] == first["connection"] == {}
    assert "credential_refs" not in first
    assert [model["id"] for model in first["models"]] == ["primary-model"]
    assert first["models"][0]["api_mode"] == "codex_native"
    assert first["models"][0]["capabilities"]["verification"] == "unknown"
    assert len(store.list_providers()) == 2
    assert store.get_settings() == before_settings
    assert coding.get_settings() == before_coding
    assert state.calls == 2
    assert runtime.status_calls == 4
    assert store.secrets_path.read_bytes() == before_secrets


@pytest.mark.asyncio
async def test_sync_reuses_first_enabled_profile_preserving_names_capabilities_and_references(connection):
    store, runtime, state = connection
    store.create_provider({"id": "disabled", "name": "Disabled", "preset": "codex_native", "enabled": False}, {})
    old = {
        "id": "old-model",
        "display_name": "My model",
        "capabilities": {"tool_calling": True, "verification": "verified"},
    }
    store.create_provider({"id": "custom", "name": "Custom connection", "preset": "codex_native", "models": [old]}, {})
    store.create_provider({"id": "second", "name": "Second", "preset": "codex_native"}, {})
    store.update_settings(
        {
            "default_model": {"provider_id": "custom", "model_id": "old-model"},
            "fast_model": {"provider_id": "custom", "model_id": "old-model"},
        }
    )
    before = store.get_settings()
    state.catalog = [{"id": "old-model", "name": "Provider name"}, {"id": "new-model", "name": "New"}]
    runtime.coding_catalog = [
        {"id": "old-model", "display_name": "Provider name"},
        {"id": "new-model", "display_name": "New"},
    ]

    result = await discovery.sync_codex_connection(store, runtime)

    assert result["id"] == "custom"
    assert result["name"] == "Custom connection"
    assert result["models"][0]["display_name"] == "My model"
    assert result["models"][0]["capabilities"]["verification"] == "verified"
    assert result["models"][0]["capabilities"]["tool_calling"] is True
    assert [model["id"] for model in result["models"]] == ["old-model", "new-model"]
    assert store.get_settings() == before
    assert store.get_provider("disabled").enabled is False
    assert store.get_provider("second").models == []


@pytest.mark.asyncio
async def test_sync_retains_old_models_absent_from_current_catalog_and_pending_edits(connection):
    store, runtime, state = connection
    store.create_provider({"id": "custom", "name": "Original", "preset": "codex_native", "models": [{"id": "old"}]}, {})
    store.update_settings({"default_model": {"provider_id": "custom", "model_id": "old"}})

    async def edit():
        store.update_provider(
            "custom",
            {
                "name": "Renamed",
                "models": [
                    {"id": "old"},
                    {"id": "primary-model", "display_name": "Edited", "capabilities": {"vision": False}},
                ],
            },
            None,
        )

    state.after = edit
    result = await discovery.sync_codex_connection(store, runtime)
    assert result["name"] == "Renamed"
    assert [model["id"] for model in result["models"]] == ["primary-model", "old"]
    assert result["models"][0]["display_name"] == "Edited"
    assert result["models"][0]["capabilities"]["vision"] is False
    assert store.get_settings()["default_model"] == {"provider_id": "custom", "model_id": "old"}
    with pytest.raises(LLMConfigError) as failure:
        store.resolve_default()
    assert failure.value.code == "llm_capability_unsupported"


@pytest.mark.asyncio
async def test_sync_id_collision_and_concurrency_reuse_one_connection(connection):
    store, runtime, state = connection
    for provider_id in ("ambient-codex", "ambient-codex-2"):
        store.create_provider({"id": provider_id, "name": provider_id, "preset": "ollama"}, {})
    both_started = asyncio.Event()

    async def overlap():
        if state.calls == 2:
            both_started.set()
        await both_started.wait()

    state.after = overlap
    first, second = await asyncio.gather(
        discovery.sync_codex_connection(store, runtime), discovery.sync_codex_connection(store, runtime)
    )
    assert first["id"] == second["id"] == "ambient-codex-3"
    assert len(store.list_providers()) == 3
    assert store.get_provider("ambient-codex").preset == "ollama"


@pytest.mark.asyncio
async def test_sync_does_not_bypass_disabled_native_connection(connection):
    store, runtime, state = connection
    store.create_provider({"id": "disabled", "name": "Disabled", "preset": "codex_native", "enabled": False}, {})
    before = store.config_path.read_bytes()
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_provider_unavailable"
    assert store.config_path.read_bytes() == before
    assert state.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["darwin", "win32"])
async def test_sync_explains_unsupported_platform_before_runtime_or_catalog(connection, monkeypatch, platform):
    store, runtime, state = connection
    monkeypatch.setattr(discovery, "sys", SimpleNamespace(platform=platform), raising=False)
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_capability_unsupported"
    assert "Linux" in str(failure.value)
    assert "platform" in str(failure.value).lower()
    assert runtime.status_calls == state.calls == 0
    assert store.list_providers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing,code", [("installed", "llm_provider_unavailable"), ("authenticated", "llm_auth_failed")]
)
async def test_sync_requires_installation_and_login_before_and_after_discovery(connection, missing, code):
    store, runtime, state = connection
    setattr(runtime, missing, False)
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == code
    assert state.calls == 0
    setattr(runtime, missing, True)

    async def remove():
        setattr(runtime, missing, False)

    state.after = remove
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == code
    assert store.list_providers() == []


@pytest.mark.asyncio
async def test_sync_discards_logout_and_relogin_during_discovery(connection):
    store, runtime, state = connection

    async def relogin():
        runtime.session_id = "login-2"

    state.after = relogin
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_auth_failed"
    assert store.list_providers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_state", ["starting", "waiting", "signed_out"])
async def test_sync_rejects_unstable_login_even_when_old_credentials_remain_authenticated(connection, auth_state):
    store, runtime, state = connection
    runtime.auth_state = auth_state
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_auth_failed"
    assert state.calls == 0
    assert store.list_providers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disable", "delete", "preset"])
async def test_sync_rejects_stale_provider_without_recreating_it(connection, change):
    store, runtime, state = connection
    store.create_provider({"id": "custom", "name": "Custom", "preset": "codex_native"}, {})

    async def mutate():
        if change == "delete":
            store.delete_provider("custom")
        else:
            changes = {"enabled": False} if change == "disable" else {"preset": "ollama"}
            store.update_provider("custom", changes, None)

    state.after = mutate
    with pytest.raises(LLMConfigError):
        await discovery.sync_codex_connection(store, runtime)
    assert all(provider["id"] != "ambient-codex" for provider in store.list_providers())
    if change != "delete":
        assert store.get_provider("custom").models == []


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog", [[], None, {}, [{"id": "valid"}, {"id": ""}], [{"id": 2}], ["model"]])
async def test_sync_validates_entire_catalog_before_creating_any_profile(connection, catalog):
    store, runtime, state = connection
    state.catalog = catalog
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_provider_error"
    assert store.list_providers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "catalog",
    [
        None,
        {},
        {"models": None},
        {"models": []},
        {"models": [{"id": "valid"}, {"id": 2}]},
        {"models": ["model"]},
        {"models": [{"id": "x" * 257}]},
        {"models": [{"id": "hidden", "hidden": True}]},
    ],
)
async def test_sync_rejects_invalid_coding_catalog_before_native_precheck_or_profile_changes(
    connection, monkeypatch, catalog
):
    store, runtime, state = connection
    before = store.config_path.read_bytes()

    async def models(_agent_id):
        return catalog

    monkeypatch.setattr(runtime, "models", models)
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_provider_error"
    assert state.calls == 0
    assert store.config_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("native_inference", [None, 1, "true", {}])
async def test_sync_rejects_invalid_native_compatibility_without_registering_any_models(connection, native_inference):
    store, runtime, state = connection
    state.catalog = [{"id": "primary-model", "native_inference": native_inference}]
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_provider_error"
    assert store.list_providers() == []


@pytest.mark.asyncio
async def test_sync_catalog_failure_preserves_existing_configuration(connection):
    store, runtime, state = connection
    store.create_provider({"id": "custom", "name": "Custom", "preset": "codex_native", "models": [{"id": "saved"}]}, {})
    before = store.config_path.read_bytes()

    async def fail():
        raise LLMTransportError("Native Codex is unavailable", code="llm_capability_unsupported")

    state.after = fail
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_capability_unsupported"
    assert store.config_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete_recreate", "disable_enable"])
async def test_sync_rejects_same_id_provider_lifecycle_change_during_discovery(connection, change):
    store, runtime, state = connection
    profile = {"id": "custom", "name": "Custom", "preset": "codex_native"}
    store.create_provider(profile, {})

    async def mutate():
        if change == "delete_recreate":
            store.delete_provider("custom")
            store.create_provider(profile, {})
        else:
            store.update_provider("custom", {"enabled": False}, None)
            store.update_provider("custom", {"enabled": True}, None)

    state.after = mutate
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_provider_unavailable"
    assert store.get_provider("custom").models == []


@pytest.mark.asyncio
async def test_sync_pending_new_connection_does_not_recreate_connection_created_then_deleted(connection):
    store, runtime, state = connection

    async def create_delete():
        store.create_provider({"id": "ambient-codex", "name": "Codex", "preset": "codex_native"}, {})
        store.delete_provider("ambient-codex")

    state.after = create_delete
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_provider_unavailable"
    assert store.list_providers() == []


@pytest.mark.asyncio
async def test_sync_unknown_catalog_failure_does_not_expose_private_runtime_message(connection):
    store, runtime, state = connection

    async def fail():
        raise RuntimeError("private-auth-marker")

    state.after = fail
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_provider_error"
    assert "private-auth-marker" not in str(failure.value)
    assert store.list_providers() == []


@pytest.mark.asyncio
async def test_sync_rejects_login_change_while_status_probe_is_pending(connection, monkeypatch):
    store, runtime, state = connection

    async def stale_status(_agent_id):
        runtime.generation += 1
        return {"installed": True, "authenticated": True, "auth_state": "signed_in"}

    monkeypatch.setattr(runtime, "status", stale_status)
    with pytest.raises(LLMConfigError) as failure:
        await discovery.sync_codex_connection(store, runtime)
    assert failure.value.code == "llm_auth_failed"
    assert state.calls == 0
    assert store.list_providers() == []


@pytest.mark.asyncio
async def test_runtime_logout_invalidates_login_generation_before_cli_completes(tmp_path, monkeypatch):
    runtime = CodingAgentRuntime(tmp_path)
    monkeypatch.setattr(runtime, "command", lambda _agent_id: ["managed-codex"])
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def probe(*_args, **_kwargs):
        entered.set()
        await finish.wait()
        return 0, ""

    monkeypatch.setattr(runtime, "_run_probe", probe)
    before = runtime.authentication_generation("codex")
    operation = asyncio.create_task(runtime.logout("codex"))
    try:
        await entered.wait()
        assert runtime.authentication_generation("codex") > before
    finally:
        finish.set()
        await operation


def _signed_in_runtime(tmp_path, monkeypatch):
    runtime = CodingAgentRuntime(tmp_path)
    monkeypatch.setattr(runtime, "command", lambda _agent_id: ["managed-codex"])
    monkeypatch.setattr(runtime, "coding_command", lambda _agent_id: ["managed-codex"])
    monkeypatch.setattr(runtime, "_bridge_command", lambda _spec: ["managed-bridge"])
    return runtime


@pytest.mark.asyncio
async def test_sync_rejects_logout_already_pending_before_discovery(connection, tmp_path, monkeypatch):
    store, _runtime, state = connection
    runtime = _signed_in_runtime(tmp_path, monkeypatch)
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def probe(argv, **_kwargs):
        if argv[-1] == "logout":
            entered.set()
            await finish.wait()
        return 0, "codex-cli test"

    class Transport:
        def __init__(self, selected_runtime):
            assert selected_runtime is runtime

        async def discover_models(self):
            state.calls += 1
            return state.catalog

    monkeypatch.setattr(runtime, "_run_probe", probe)
    monkeypatch.setattr("backend.codex_llm.NativeCodexTransport", Transport)
    operation = asyncio.create_task(runtime.logout("codex"))
    try:
        await entered.wait()
        with pytest.raises(LLMConfigError) as failure:
            await discovery.sync_codex_connection(store, runtime)
        assert failure.value.code == "llm_auth_failed"
        assert state.calls == 0
        assert store.list_providers() == []
        status = await runtime.status("codex")
        assert status["authenticated"] is False
        assert status["auth_state"] == "signed_out"
    finally:
        finish.set()
        await operation


@pytest.mark.asyncio
async def test_runtime_concurrent_logout_keeps_status_signed_out_until_last_operation_finishes(tmp_path, monkeypatch):
    runtime = _signed_in_runtime(tmp_path, monkeypatch)
    entered = [asyncio.Event(), asyncio.Event()]
    finish = asyncio.Event()
    calls = 0

    async def probe(argv, **_kwargs):
        nonlocal calls
        if argv[-1] == "logout":
            index = calls
            calls += 1
            entered[index].set()
            await finish.wait()
        return 0, "codex-cli test"

    monkeypatch.setattr(runtime, "_run_probe", probe)
    first = asyncio.create_task(runtime.logout("codex"))
    second = asyncio.create_task(runtime.logout("codex"))
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        status = await runtime.status("codex")
        assert status["authenticated"] is False
        assert status["auth_state"] == "signed_out"
    finally:
        finish.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert (await runtime.status("codex"))["authenticated"] is True


@pytest.mark.asyncio
async def test_runtime_failed_logout_clears_in_progress_state(tmp_path, monkeypatch):
    from backend.coding_agent_runtime import CodingAgentRuntimeError

    runtime = _signed_in_runtime(tmp_path, monkeypatch)

    async def probe(argv, **_kwargs):
        return (1, "failed") if argv[-1] == "logout" else (0, "codex-cli test")

    monkeypatch.setattr(runtime, "_run_probe", probe)
    with pytest.raises(CodingAgentRuntimeError) as failure:
        await runtime.logout("codex")
    assert failure.value.code == "auth_logout_failed"
    assert (await runtime.status("codex"))["authenticated"] is True


@pytest.mark.asyncio
async def test_sync_discards_logout_completion_while_login_probe_is_pending(connection, tmp_path, monkeypatch):
    store, _runtime, state = connection
    runtime = _signed_in_runtime(tmp_path, monkeypatch)
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def probe(argv, **_kwargs):
        if argv[-1] == "logout":
            entered.set()
            await finish.wait()
        elif argv[-1] == "--version":
            finish.set()
            await operation
        return 0, "codex-cli test"

    monkeypatch.setattr(runtime, "_run_probe", probe)
    operation = asyncio.create_task(runtime.logout("codex"))
    try:
        await entered.wait()
        with pytest.raises(LLMConfigError) as failure:
            await discovery.sync_codex_connection(store, runtime)
        assert failure.value.code == "llm_auth_failed"
        assert state.calls == 0
        assert store.list_providers() == []
    finally:
        finish.set()
        await operation
