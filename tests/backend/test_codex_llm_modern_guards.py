"""Source-driven .159 guard coverage; no real CLI, network, or authentication."""

import asyncio
import copy
import json
import os
from pathlib import Path

import pytest

from backend.codex_llm import NativeCodexTransport
from backend.llm_service import LLMTransportError
from test_codex_llm import HISTORY, TOOLS, ProbeProcess, adapter, selection
from test_codex_llm_modern import FIXTURE, ModernProcess, ModernRuntime, modern_adapter


class _TransformedReader(asyncio.StreamReader):
    """Mutate a decoded fake server response after the official fixture projection."""

    def __init__(self, process, transform):
        super().__init__()
        self.process = process
        self.transform = transform

    def feed_data(self, data):
        message = self.transform(self.process, json.loads(data))
        super().feed_data(json.dumps(message).encode() + b"\n")


class _GuardProcess(ModernProcess):
    def __init__(self, transform, scenario="ok"):
        super().__init__(scenario)
        self.stdout = _TransformedReader(self, transform)


def _guard_adapter(tmp_path, transform, *, scenario="ok", version="0.159.3"):
    runtime = ModernRuntime(tmp_path / "guard-state")
    processes = []
    spawns = []

    async def spawn(*args, **kwargs):
        spawns.append((args, kwargs))
        process = ProbeProcess(version) if "--version" in args else _GuardProcess(transform, scenario)
        processes.append(process)
        return process

    async def stop(process):
        assert Path(spawns[-1][1]["cwd"]).is_dir()
        process.stop()

    return NativeCodexTransport(runtime, process_factory=spawn, stop_process=stop), runtime, processes, spawns


def _request_method(process, message):
    request = next((r for r in process.requests if r.get("id") == message.get("id")), None)
    return request["method"] if request else None


def _methods(processes):
    return [request["method"] for process in processes for request in process.requests]


def _final_item(message):
    return message["params"]["turn"]["items"][-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"experimental_supported_tools": ["clock", "test_sync_tool"]},
        {"experimental_supported_tools": "clock"},
        {"experimental_supported_tools": None},
        {"experimental_supported_tools": ["clock", 1]},
        {"experimental_supported_tools": ["clock", "clock"]},
        {"multi_agent_version": "v3"},
        {"multi_agent_version": []},
        {"tool_mode": "direct"},
    ],
)
async def test_modern_unknown_or_malformed_metadata_rejects_before_starting_a_thread(tmp_path, change):
    transport, runtime, processes, _ = modern_adapter(tmp_path)
    runtime.metadata(**change)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.code == "llm_capability_unsupported"
    assert caught.value.details == {"native_reason": "model_metadata"}
    assert "account/read" in _methods(processes)
    assert "thread/start" not in _methods(processes)
    assert all(process.stopped for process in processes)


@pytest.mark.asyncio
async def test_modern_duplicate_model_metadata_rejects_before_starting_a_thread(tmp_path):
    transport, runtime, processes, _ = modern_adapter(tmp_path)
    models = copy.deepcopy(FIXTURE["models"])
    models.append(copy.deepcopy(models[-1]))
    (runtime.root / "models_cache.json").write_text(json.dumps({"models": models}))
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "model_metadata"}
    assert "account/read" in _methods(processes)
    assert "thread/start" not in _methods(processes)


@pytest.mark.asyncio
async def test_modern_public_cache_symlink_is_rejected_without_reading_its_target(tmp_path, monkeypatch):
    transport, runtime, processes, _ = modern_adapter(tmp_path)
    cache = runtime.root / "models_cache.json"
    target = tmp_path / "outside-catalog.json"
    cache.replace(target)
    cache.symlink_to(target)
    reads = []
    original_fdopen = os.fdopen
    target_identity = (target.stat().st_dev, target.stat().st_ino)

    class ReadProbe:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def fileno(self):
            return self.source.fileno()

        def read(self, *args):
            reads.append(target)
            return self.source.read(*args)

    def open_descriptor(descriptor, *args, **kwargs):
        source = original_fdopen(descriptor, *args, **kwargs)
        info = os.fstat(descriptor)
        return ReadProbe(source) if (info.st_dev, info.st_ino) == target_identity else source

    monkeypatch.setattr(os, "fdopen", open_descriptor)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "model_metadata"}
    assert reads == []
    assert "thread/start" not in _methods(processes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"delivery": "external"},
        {"delivery": []},
        {"phase": "finalAnswer"},
        {"phase": 1},
        {"phase": []},
        {"questions": "question"},
        {"questions": [{"title": 1, "options": None}]},
        {"questions": [{"title": "Which?", "options": [1]}]},
        {"questions": [{"title": "Which?", "options": None, "callback": "external"}]},
    ],
)
async def test_modern_async_messages_validate_public_delivery_phase_and_question_shape(tmp_path, change):
    def transform(_process, message):
        if message.get("method") == "turn/completed":
            message["params"]["turn"]["items"][0].update(change)
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform, scenario="async")
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "native_item"}
    assert _methods(processes).count("turn/start") == 1
    assert all(process.stopped for process in processes)


@pytest.mark.asyncio
async def test_modern_async_questions_cannot_be_reclassified_as_an_ordinary_final_answer(tmp_path):
    def transform(_process, message):
        if message.get("method") == "turn/completed":
            item = message["params"]["turn"]["items"][0]
            item["delivery"] = None
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform, scenario="async")
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "native_item"}
    assert _methods(processes).count("turn/start") == 1


@pytest.mark.asyncio
async def test_modern_async_json_tool_choices_never_escape_as_ambient_tool_calls(tmp_path):
    def transform(_process, message):
        if message.get("method") == "turn/completed":
            item = message["params"]["turn"]["items"][0]
            item["text"] = '{"text":"async only","tool_calls":[{"name":"lookup","arguments":"{\\"value\\":0}"}]}'
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform, scenario="async")
    result = await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert result.text == "done"
    assert [call["function"]["arguments"] for call in result.tool_calls] == ['{"value":1}']
    assert _methods(processes).count("turn/start") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item_type",
    [
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabAgentToolCall",
        "functionCallOutput",
        "toolExecution",
        "codeMode",
    ],
)
async def test_modern_known_native_effect_items_do_not_expand_the_allowed_inference_surface(tmp_path, item_type):
    def transform(_process, message):
        if message.get("method") == "turn/completed":
            message["params"]["turn"]["items"].insert(0, {"id": "native-1", "type": item_type})
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "native_item"}
    assert _methods(processes).count("turn/start") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "item/tool/requestUserInput",
        "item/commandExecution/requestApproval",
        "item/tool/call",
        "account/chatgptAuthTokens/refresh",
    ],
)
async def test_modern_native_callbacks_are_rejected_and_never_answered(tmp_path, method):
    def transform(_process, message):
        if message.get("method") == "turn/completed":
            return {"id": "callback-1", "method": method, "params": {"threadId": "thread-1", "turnId": "turn-1"}}
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "callback"}
    assert not any(request.get("id") == "callback-1" for process in processes for request in process.requests)
    assert all(process.stopped for process in processes)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"threadId": "foreign-thread"}, {"turnId": "foreign-turn"}])
async def test_modern_foreign_thread_or_turn_never_overrides_the_owned_inference(tmp_path, change):
    def transform(_process, message):
        if message.get("method") == "turn/completed":
            message["params"].update(change)
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "identity"}
    assert _methods(processes).count("turn/start") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    [
        {"id": ":workspace", "extends": None},
        {"id": ":read-only", "extends": "custom"},
        {"id": ":read-only", "extends": []},
    ],
)
async def test_modern_permission_provenance_cannot_weaken_the_fixed_readonly_profile(tmp_path, profile):
    def transform(process, message):
        if _request_method(process, message) == "thread/start":
            message["result"]["activePermissionProfile"] = profile
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "thread_policy"}
    assert "thread/start" in _methods(processes)
    assert "turn/start" not in _methods(processes)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["0.159.2", "0.159.4", "0.159.3-alpha.1"])
async def test_modern_unsupported_patch_never_falls_back_to_legacy_execution(tmp_path, monkeypatch, version):
    transport, runtime, processes, spawns = modern_adapter(tmp_path, version=version)
    legacy_calls = []

    def legacy_command(_agent):
        legacy_calls.append("command")
        return ["legacy-codex"]

    monkeypatch.setattr(runtime, "command", legacy_command)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.code == "llm_capability_unsupported"
    assert caught.value.details == {"native_reason": "configuration"}
    assert legacy_calls == []
    assert len(spawns) == 1 and "--version" in spawns[0][0]
    assert _methods(processes) == []


@pytest.mark.asyncio
async def test_modern_initialize_version_mismatch_rejects_before_configuration_or_account_reads(tmp_path):
    def transform(process, message):
        if _request_method(process, message) == "initialize":
            message["result"]["userAgent"] = "codex_cli_rs/0.159.2"
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.code == "llm_capability_unsupported"
    assert _methods(processes) == ["initialize"]
    assert all(process.stopped for process in processes)


@pytest.mark.asyncio
async def test_modern_environment_home_mismatch_never_retries_with_the_login_or_coding_home(tmp_path, monkeypatch):
    transport, runtime, processes, spawns = modern_adapter(tmp_path)
    legacy_calls = []

    def legacy_environment(_agent):
        legacy_calls.append("environment")
        return {"CODEX_HOME": str(runtime.root)}

    monkeypatch.setattr(runtime, "process_environment", legacy_environment)
    monkeypatch.setattr(runtime, "inference_environment", lambda _agent: {"CODEX_HOME": str(tmp_path / "wrong-home")})
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.code == "llm_invalid_configuration"
    assert legacy_calls == []
    assert processes == spawns == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "info,code",
    [
        ("rateLimitExceeded", "llm_rate_limited"),
        ("flexUnavailable", "llm_provider_error"),
        ("misalignmentPolicyViolation", "llm_provider_error"),
        ("tooManyDenials", "llm_provider_error"),
    ],
)
async def test_modern_public_upstream_enum_maps_codes_without_echoing_private_details(tmp_path, info, code):
    def transform(_process, message):
        if message.get("method") == "turn/completed":
            return {
                "method": "error",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "willRetry": False,
                    "error": {
                        "message": "private-error-content",
                        "additionalDetails": "private-error-details",
                        "codexErrorInfo": info,
                    },
                },
            }
        return message

    transport, _, processes, _ = _guard_adapter(tmp_path, transform)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(model_id="gpt-6-luna"), HISTORY, TOOLS)
    assert caught.value.code == code
    assert caught.value.details == {"native_reason": "known_upstream_error"}
    assert "private-error" not in str(caught.value)
    assert _methods(processes).count("turn/start") == 1
    assert all(process.stopped for process in processes)


@pytest.mark.asyncio
async def test_modern_enum_extensions_are_not_backported_to_the_legacy_profile(tmp_path):
    transport, _, processes, _ = adapter(tmp_path, "error_unknown_info")
    process_factory = transport._spawn

    async def spawn(*args, **kwargs):
        process = await process_factory(*args, **kwargs)
        if "--version" not in args:
            emit = process.emit

            def emit_error(message):
                if message.get("method") == "error":
                    message["params"]["error"]["codexErrorInfo"] = "rateLimitExceeded"
                emit(message)

            process.emit = emit_error
        return process

    transport._spawn = spawn
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.code == "llm_capability_unsupported"
    assert caught.value.details == {"native_reason": "known_upstream_error"}
    assert processes[-1].stopped
