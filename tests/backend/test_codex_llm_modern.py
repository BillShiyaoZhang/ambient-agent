"""Pinned .159 protocol fixtures are public metadata, never account data."""

import copy
import json
from pathlib import Path

import pytest

from backend.codex_llm import NativeCodexTransport, _Connection
from backend.llm_service import LLMTransportError
from test_codex_llm import HISTORY, TOOLS, NativeProcess, ProbeProcess, Runtime, selection

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "codex_native_0_159_3.json").read_text())


class ModernRuntime(Runtime):
    def metadata(self, **changes):
        models = copy.deepcopy(FIXTURE["models"])
        for model in models:
            model.update(changes)
        (self.root / "models_cache.json").write_text(json.dumps({"models": models}))

    inference_command = Runtime.command
    inference_environment = Runtime.process_environment
    inference_state_dir = Runtime.state_dir


class ModernProcess(NativeProcess):
    def emit(self, value):
        value = copy.deepcopy(value)
        request = (
            next((r for r in self.requests if r.get("id") == value.get("id")), None) if "result" in value else None
        )
        if request:
            method = request["method"]
            if method == "initialize":
                value["result"]["userAgent"] = "ambient-native-inference/0.159.3 (Debian 12.0.0; aarch64)"
            elif method == "config/read":
                value["result"] = copy.deepcopy(FIXTURE["config_read"])
                if self.scenario == "cloud_enabled":
                    value["result"]["config"]["cloud"]["skills"]["enabled"] = True
                if self.scenario == "sleep_enabled":
                    value["result"]["config"]["features"]["sleep_tool"] = True
            elif method == "model/list":
                super().emit({"method": "account/updated", "params": {"authMode": "chatgpt", "planType": "plus"}})
                value["result"]["data"] = [
                    {"id": m["slug"], "displayName": m["slug"], "hidden": False} for m in FIXTURE["models"]
                ]
            elif method == "thread/start":
                value["result"]["model"] = request["params"]["model"]
                value["result"]["activePermissionProfile"] = {"id": ":read-only", "extends": None}
        if self.scenario.startswith("async") and value.get("method") == "turn/completed":
            item = {
                "type": "agentMessage",
                "id": "async-1",
                "text": '{"text":"unwanted","tool_calls":[]}',
                "phase": "final_answer",
                "delivery": "async",
                "questions": [{"title": "Which?", "options": None}],
                "memoryCitation": None,
            }
            if self.scenario == "async_unknown":
                item["delivery"] = "external"
            if self.scenario == "async_invalid":
                item["questions"][0]["title"] = 10
            value["params"]["turn"]["items"].insert(0, item)
        super().emit(value)


def modern_adapter(tmp_path, scenario="ok", version="0.159.3"):
    runtime = ModernRuntime(tmp_path / "modern")
    processes = []
    spawns = []

    async def spawn(*args, **kwargs):
        spawns.append((args, kwargs))
        process = ProbeProcess(version) if "--version" in args else ModernProcess(scenario)
        processes.append(process)
        return process

    async def stop(process):
        process.stop()

    return NativeCodexTransport(runtime, process_factory=spawn, stop_process=stop), runtime, processes, spawns


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [m["slug"] for m in FIXTURE["models"]])
async def test_modern_gpt6_generates_strict_ambient_tool_choices(tmp_path, model):
    transport, _, processes, spawns = modern_adapter(tmp_path)
    result = await transport.generate(selection(model_id=model, litellm_model=model), HISTORY, TOOLS)
    assert result.text == "done"
    assert result.tool_calls[0]["function"] == {"name": "lookup", "arguments": '{"value":1}'}
    thread = next(r["params"] for r in processes[-1].requests if r["method"] == "thread/start")
    assert thread["config"] == FIXTURE["safe_config"]
    assert thread["model"] == model
    assert thread["ephemeral"] is True
    assert thread["sandbox"] == "read-only"
    assert thread["environments"] == thread["dynamicTools"] == thread["runtimeWorkspaceRoots"] == []
    assert "--strict-config" in spawns[-1][0]
    assert all(p.stopped for p in processes)


@pytest.mark.parametrize("params", [{"authMode": "chatgpt", "planType": "pro"}, {"authMode": None, "planType": None}])
def test_modern_account_update_accepts_only_public_status(params):
    connection = _Connection(None, "0.159.3")
    connection.notification({"method": "account/updated", "params": params})
    assert connection.thread_id is None
    assert connection.items == {}


@pytest.mark.parametrize(
    "params",
    [
        {"authMode": "apikey", "planType": "plus"},
        {"authMode": "chatgptAuthTokens", "planType": "plus"},
        {"authMode": "chatgpt", "planType": "future"},
        {"authMode": ["chatgpt"], "planType": "plus"},
        {"authMode": "chatgpt", "planType": []},
        {"authMode": "chatgpt"},
        {"authMode": "chatgpt", "planType": "plus", "token": "private"},
    ],
)
def test_modern_account_update_rejects_unknown_or_external_auth(params):
    connection = _Connection(None, "0.159.3")
    with pytest.raises(LLMTransportError) as caught:
        connection.notification({"method": "account/updated", "params": params})
    assert caught.value.details["native_reason"] == "native_notification"
    assert "private" not in str(caught.value)


def test_legacy_account_update_stays_unsupported():
    connection = _Connection(None)
    with pytest.raises(LLMTransportError) as caught:
        connection.notification({"method": "account/updated", "params": {"authMode": "chatgpt", "planType": "plus"}})
    assert caught.value.details["native_reason"] == "native_notification"


def test_modern_async_item_cannot_rebind_type_to_become_final_answer():
    connection = _Connection(None, "0.159.3")
    connection._item(
        {"type": "agentMessage", "id": "a", "text": "private", "delivery": "async", "questions": [{"title": "Which?"}]}
    )
    with pytest.raises(LLMTransportError) as caught:
        connection._item({"type": "userMessage", "id": "a", "content": []})
        connection._item(
            {"type": "agentMessage", "id": "a", "text": '{"text":"private","tool_calls":[]}', "phase": "final_answer"}
        )
    assert caught.value.details["native_reason"] == "native_item"


def _startup_warning(home):
    return f"Under-development features enabled: skip_host_skill_discovery. Under-development features are incomplete and may behave unpredictably. To suppress this warning, set `suppress_unstable_features_warning = true` in {home}/config.toml."


_HOST_DISABLED_WARNING = "Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`."


@pytest.mark.parametrize("warning", ["startup", "host"])
def test_modern_exact_owned_warning_is_safe_to_observe(tmp_path, warning):
    connection = _Connection(None, "0.159.3")
    connection.home = tmp_path
    connection.thread_id = "owned"
    message = _startup_warning(tmp_path) if warning == "startup" else _HOST_DISABLED_WARNING
    connection.notification({"method": "warning", "params": {"threadId": "owned", "message": message}})
    assert connection.items == {}


@pytest.mark.parametrize("change", ["unknown", "path", "fallback", "fields", "thread", "legacy"])
def test_modern_warning_never_ignores_other_profile_or_warning(tmp_path, change):
    connection = _Connection(None, "0.145.0" if change == "legacy" else "0.159.3")
    connection.home = tmp_path
    connection.thread_id = "owned"
    params = {"threadId": "owned", "message": _startup_warning(tmp_path)}
    if change == "unknown":
        params["message"] = "private unknown warning"
    if change == "path":
        params["message"] = _startup_warning(tmp_path / "other")
    if change == "fallback":
        params["message"] = _HOST_DISABLED_WARNING.replace("Code mode will fail closed", "Falling back to direct tools")
    if change == "fields":
        params["token"] = "private"
    if change == "thread":
        params["threadId"] = None
    with pytest.raises(LLMTransportError) as caught:
        connection.notification({"method": "warning", "params": params})
    assert caught.value.details["native_reason"] == "native_notification"
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_modern_async_message_is_buffered_but_never_a_final_answer(tmp_path):
    transport, _, _, _ = modern_adapter(tmp_path, "async")
    result = await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert result.text == "done"
    assert len(result.tool_calls) == 1


@pytest.mark.asyncio
async def test_modern_public_metadata_compatibility_lists_all_gpt6_models(tmp_path):
    transport, _, processes, _ = modern_adapter(tmp_path)
    models = await transport.model_availability()
    assert {m["id"] for m in models} == {m["slug"] for m in FIXTURE["models"]}
    assert all(m["native_inference"] for m in models)
    assert not any(r["method"] in {"account/read", "thread/start", "turn/start"} for r in processes[-1].requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("cloud_enabled", "configuration"),
        ("sleep_enabled", "configuration"),
        ("async_unknown", "native_item"),
        ("async_invalid", "native_item"),
    ],
)
async def test_modern_rejects_unsafe_config_or_unknown_async_shape(tmp_path, scenario, reason):
    transport, _, processes, _ = modern_adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details["native_reason"] == reason
    assert all(p.stopped for p in processes)
