"""Native protocol tests use injected processes and never call a model or read auth."""

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

from backend.codex_llm import (
    NativeCodexTransport,
    _BYTE_LIMIT,
    _ITEM_LIMIT,
    _MESSAGE_LIMIT,
    _SAFE_CONFIG,
    _STREAM_MESSAGE_LIMIT,
    _Connection,
    _closed_schema,
    _error,
    _stop_process,
)
from backend.llm_config import ResolvedModel
from backend.llm_service import LLMTransportError


class Runtime:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True)
        self.metadata()

    def metadata(self, **changes):
        model = {
            "slug": "gpt-5.6-luna",
            "tool_mode": "code_mode_only",
            "experimental_supported_tools": [],
            "multi_agent_version": "v1",
        }
        model.update(changes)
        (self.root / "models_cache.json").write_text(json.dumps({"models": [model]}), encoding="utf-8")

    def command(self, agent):
        assert agent == "codex"
        return ["trusted-codex"]

    def process_environment(self, agent):
        assert agent == "codex"
        return {"CODEX_HOME": str(self.root), "HOME": str(self.root)}

    def state_dir(self, agent):
        assert agent == "codex"
        return self.root


def selection(**changes):
    values = {
        "provider_id": "native",
        "provider_name": "Native",
        "provider_preset": "codex_native",
        "model_id": "gpt-5.6-luna",
        "litellm_model": "gpt-5.6-luna",
        "api_mode": "codex_native",
        "connection": {},
        "credentials": {},
        "capabilities": {},
    }
    values.update(changes)
    return ResolvedModel.model_construct(**values)


class NativeProcess:
    def __init__(self, scenario):
        self.scenario = scenario
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.stdin = self
        self.returncode = None
        self.pid = 12345
        self.done = asyncio.Event()
        self.requests = []
        self.stopped = False

    def emit(self, value):
        self.stdout.feed_data(json.dumps(value).encode() + b"\n")

    def write(self, raw):
        request = json.loads(raw)
        self.requests.append(request)
        method = request["method"]
        if "id" not in request:
            return
        result = {
            "initialize": {
                "userAgent": "codex_cli_rs/0.145.0",
                "codexHome": "/managed",
                "platformFamily": "unix",
                "platformOs": "linux",
            },
            "config/read": {
                "config": dict(_SAFE_CONFIG),
                "origins": {},
                "layers": [{"name": {"type": "sessionFlags"}, "config": dict(_SAFE_CONFIG), "disabledReason": None}],
            },
            "account/read": {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True},
            "model/list": {
                "data": [{"id": "gpt-5.6-luna", "model": "gpt-5.6-luna", "displayName": "Luna", "hidden": False}],
                "nextCursor": None,
            },
            "mcpServerStatus/list": {"data": [], "nextCursor": None},
            "thread/start": {
                "thread": {"id": "thread-1"},
                "model": "gpt-5.6-luna",
                "modelProvider": "openai",
                "cwd": request["params"].get("cwd"),
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "sandbox": {"type": "readOnly"},
            },
            "turn/start": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}},
        }.get(method, {})
        if method == "account/read" and self.scenario == "no_auth":
            result["account"] = None
        if method == "mcpServerStatus/list" and self.scenario == "mcp":
            result["data"] = [{"name": "private-secret-server"}]
        if method == "config/read" and self.scenario == "unsafe_config":
            result["config"]["notify"] = ["unsafe-command"]
        if method == "config/read" and self.scenario.startswith("endpoint_"):
            lower = {"name": {"type": "system"}, "config": {}, "disabledReason": None}
            if self.scenario in {
                "endpoint_null",
                "endpoint_null_config",
                "endpoint_list_config",
                "endpoint_list_layer",
            }:
                if self.scenario == "endpoint_null":
                    lower["config"] = {"openai_base_url": None, "chatgpt_base_url": None}
                elif self.scenario == "endpoint_null_config":
                    lower["config"] = None
                elif self.scenario == "endpoint_list_config":
                    lower["config"] = [{"openai_base_url": "private-secret.invalid"}]
                else:
                    lower = []
            else:
                key = "chatgpt_base_url" if "chatgpt" in self.scenario else "openai_base_url"
                lower["config"][key] = (
                    {}
                    if "map" in self.scenario
                    else ""
                    if "empty" in self.scenario
                    else "https://private-secret.invalid"
                )
                if "disabled" in self.scenario:
                    lower["disabledReason"] = "disabled"
            if "session" in self.scenario:
                result["layers"][0]["config"] = {**_SAFE_CONFIG, **lower["config"]}
            else:
                result["layers"].append(lower)
        if method == "config/read" and self.scenario in {
            "typed_tools",
            "raw_tools_enabled",
            "raw_tools_disabled",
            "raw_tools_missing",
        }:
            result["config"].pop("tools")
            if self.scenario == "raw_tools_enabled":
                result["layers"][0]["config"] = {"tools": {"experimental_request_user_input": {"enabled": True}}}
            elif self.scenario == "raw_tools_disabled":
                result["layers"][0]["disabledReason"] = "ignored"
            elif self.scenario == "raw_tools_missing":
                result["layers"] = []
        if method == "thread/start" and self.scenario.startswith("thread_"):
            changes = {
                "thread_write": {"sandbox": {"type": "workspaceWrite"}},
                "thread_network": {"sandbox": {"type": "readOnly", "networkAccess": True}},
                "thread_approval": {"approvalPolicy": "on-request"},
                "thread_reviewer": {"approvalsReviewer": "guardian_subagent"},
                "thread_cwd": {"cwd": "/user/workspace"},
                "thread_provider": {"modelProvider": "proxy"},
                "thread_roots": {"runtimeWorkspaceRoots": ["/user/workspace"]},
                "thread_instructions": {"instructionSources": [{"type": "project"}]},
            }
            result.update(changes[self.scenario])
        if method == "config/read" and self.scenario in {
            "bwrap_warning",
            "unsafe_warning",
            "generic_warning",
            "remote_disabled",
            "remote_connected",
        }:
            if self.scenario in {"remote_disabled", "remote_connected"}:
                self.emit(
                    {
                        "method": "remoteControl/status/changed",
                        "params": {
                            "status": "disabled" if self.scenario == "remote_disabled" else "connected",
                            "installationId": "hidden",
                            "serverName": "hidden",
                            "environmentId": None,
                        },
                    }
                )
            elif self.scenario == "generic_warning":
                self.emit({"method": "warning", "params": {"message": "unsafe sandbox downgrade"}})
            else:
                summary = (
                    "Codex could not find bubblewrap on PATH. Install bubblewrap with your OS package manager. See the sandbox prerequisites: https://developers.openai.com/codex/concepts/sandboxing#prerequisites. Codex will use the bundled bubblewrap in the meantime."
                    if self.scenario == "bwrap_warning"
                    else "Invalid configuration: secret"
                )
                self.emit({"method": "configWarning", "params": {"summary": summary}})
        if method == "turn/start" and self.scenario in {
            "early_turn",
            "wrong_thread",
            "rebind_turn",
            "conflict_reply",
            "unbound_item",
        }:
            if self.scenario == "unbound_item":
                self.emit(
                    {
                        "method": "item/started",
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {"id": "early", "type": "reasoning"},
                        },
                    }
                )
            else:
                self.emit(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": "wrong" if self.scenario == "wrong_thread" else "thread-1",
                            "turn": {"id": "turn-1", "status": "inProgress", "items": []},
                        },
                    }
                )
                if self.scenario == "rebind_turn":
                    self.emit(
                        {
                            "method": "turn/started",
                            "params": {
                                "threadId": "thread-1",
                                "turn": {"id": "turn-2", "status": "inProgress", "items": []},
                            },
                        }
                    )
                if self.scenario == "conflict_reply":
                    result["turn"]["id"] = "turn-2"
        self.emit({"id": request["id"], "result": result})
        if method != "turn/start":
            return
        if self.scenario == "hang":
            return
        if self.scenario in {"stream_deltas", "stream_overflow"}:
            self.emit(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {"id": "final", "type": "agentMessage", "text": ""},
                    },
                }
            )
            delta_count = 1100 if self.scenario == "stream_deltas" else _STREAM_MESSAGE_LIMIT + 1
            for _ in range(delta_count):
                self.emit(
                    {
                        "method": "item/agentMessage/delta",
                        "emittedAtMs": 1_797_000_000_000,
                        "params": {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "itemId": "final",
                            "delta": "x",
                        },
                    }
                )
        if self.scenario == "approval":
            self.emit(
                {
                    "id": "approval-1",
                    "method": "item/commandExecution/requestApproval",
                    "params": {"command": "secret command"},
                }
            )
            return
        if self.scenario in {"upstream_error", "unknown_notification"}:
            self.emit(
                {
                    "method": "error" if self.scenario == "upstream_error" else "private-secret/notification",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "error": {"message": "private-secret", "codexErrorInfo": "other"},
                        "willRetry": False,
                    },
                }
            )
            return
        if self.scenario.startswith("error_"):
            infos = {
                "error_rate": "usageLimitExceeded",
                "error_session_budget": "sessionBudgetExceeded",
                "error_auth": "unauthorized",
                "error_provider": "serverOverloaded",
                "error_http_rate": {"httpConnectionFailed": {"httpStatusCode": 429}},
                "error_http_timeout": {"responseStreamConnectionFailed": {"httpStatusCode": 504}},
                "error_http_auth": {"responseTooManyFailedAttempts": {"httpStatusCode": 403}},
                "error_stream": {"responseStreamDisconnected": {}},
                "error_active": {"activeTurnNotSteerable": {"turnKind": "review"}},
                "error_null_info": None,
                "error_unknown_info": "private-secret",
                "error_unknown_variant": {"private-secret": {}},
                "error_bad_status": {"httpConnectionFailed": {"httpStatusCode": "private-secret"}},
                "error_bad_kind": {"activeTurnNotSteerable": {"turnKind": "private-secret"}},
                "error_extra_inner": {
                    "httpConnectionFailed": {"httpStatusCode": 429, "private-secret": "private-secret"}
                },
            }
            params = {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "error": {
                    "message": "private-secret",
                    "additionalDetails": "private-secret",
                    "codexErrorInfo": infos.get(self.scenario, "other"),
                },
                "willRetry": self.scenario in {"error_retry", "error_retry_hang", "error_retry_budget"},
            }
            if self.scenario == "error_wrong_thread":
                params["threadId"] = "wrong"
            if self.scenario == "error_wrong_turn":
                params["turnId"] = "wrong"
            if self.scenario == "error_bad_retry":
                params["willRetry"] = 1
            if self.scenario == "error_bad_message":
                params["error"]["message"] = {}
            if self.scenario == "error_bad_details":
                params["error"]["additionalDetails"] = []
            if self.scenario == "error_missing_turn":
                params.pop("turnId")
            if self.scenario == "error_extra":
                params["private-secret"] = "private-secret"
            if self.scenario == "error_omitted_info":
                params["error"].pop("codexErrorInfo")
            for _ in range(1030 if self.scenario == "error_retry_budget" else 1):
                self.emit({"method": "error", "params": params})
            if self.scenario != "error_retry":
                return
        if self.scenario == "oversize":
            self.stdout.feed_data(b"x" * (2 * 1024 * 1024) + b"\n")
            return
        if self.scenario == "native_tool":
            self.emit(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {"id": "bad", "type": "commandExecution"},
                    },
                }
            )
            return
        arguments = {"value": 1}
        if self.scenario == "wrong_type":
            arguments["value"] = "private-secret"
        if self.scenario == "constraint":
            arguments["value"] = -1
        if self.scenario == "missing_required":
            arguments = {}
        if self.scenario == "unknown_argument":
            arguments["secret"] = 1
        value = {"text": "done", "tool_calls": [{"name": "lookup", "arguments": json.dumps(arguments)}]}
        if self.scenario == "unknown_tool":
            value["tool_calls"][0]["name"] = "exec_command"
        if self.scenario == "bad_args":
            value["tool_calls"][0]["arguments"] = "secret argument"
        if self.scenario == "object_args":
            value["tool_calls"][0]["arguments"] = arguments
        if self.scenario == "double_encoded":
            value["tool_calls"][0]["arguments"] = json.dumps(json.dumps(arguments))
        if self.scenario == "array_args":
            value["tool_calls"][0]["arguments"] = "[]"
        if self.scenario == "partial_calls":
            value["tool_calls"].append({"name": "lookup", "arguments": '{"value":"private-secret"}'})
        if self.scenario in {"no_tools", "no_usage", "plan"}:
            value["tool_calls"] = []
        if self.scenario == "extra_key":
            value["secret"] = "raw-secret"
        text = "raw-secret invalid json" if self.scenario == "invalid_json" else json.dumps(value)
        items = [{"id": "final", "type": "agentMessage", "phase": "final_answer", "text": text}]
        if self.scenario == "plan":
            items.insert(0, {"id": "memory-plan", "type": "plan", "text": "Internal plan"})
        if self.scenario == "missing_final":
            items = []
        if self.scenario != "no_usage":
            self.emit(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "tokenUsage": {"total": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15}},
                    },
                }
            )
        self.emit(
            {
                "method": "turn/completed",
                "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed", "items": items}},
            }
        )

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.stop()

    def stop(self):
        self.stopped = True
        self.returncode = 0
        self.done.set()

    async def wait(self):
        await self.done.wait()
        return self.returncode


class ProbeProcess(NativeProcess):
    def __init__(self, version):
        super().__init__("")
        self.stdout.feed_data(f"codex-cli {version}\n".encode())
        self.stdout.feed_eof()
        self.stop()


def adapter(tmp_path, scenario="ok", *, timeout=1, version="0.145.0"):
    runtime = Runtime(tmp_path / "native-state")
    processes = []
    spawns = []

    async def spawn(*args, **kwargs):
        spawns.append((args, kwargs))
        process = ProbeProcess(version) if "--version" in args else NativeProcess(scenario)
        processes.append(process)
        return process

    async def stop(process):
        assert Path(spawns[-1][1]["cwd"]).is_dir(), "Process must stop before temporary cwd removal"
        process.stop()

    transport = NativeCodexTransport(runtime, process_factory=spawn, stop_process=stop, timeout=timeout)
    return transport, runtime, processes, spawns


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Lookup",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "integer", "minimum": 0, "enum": [0, 1]}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    }
]
HISTORY = [
    {"role": "system", "content": "System"},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "tool_calls": [{"id": "previous", "function": {"name": "lookup", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "previous", "content": "result"},
]


@pytest.mark.asyncio
async def test_native_final_preserves_history_tool_choice_and_upstream_usage(tmp_path):
    transport, runtime, processes, spawns = adapter(tmp_path)
    result = await transport.generate(selection(), HISTORY, TOOLS)
    assert result.text == "done"
    assert result.tool_calls[0]["function"] == {"name": "lookup", "arguments": '{"value":1}'}
    assert result.usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    server = processes[-1]
    thread = next(x["params"] for x in server.requests if x["method"] == "thread/start")
    turn = next(x["params"] for x in server.requests if x["method"] == "turn/start")
    assert thread["environments"] == turn["environments"] == []
    assert thread["dynamicTools"] == thread["selectedCapabilityRoots"] == []
    assert thread["ephemeral"] is True
    assert thread["sandbox"] == "read-only"
    assert thread["approvalPolicy"] == "never"
    assert thread["allowProviderModelFallback"] is False
    envelope = json.loads(turn["input"][0]["text"])
    assert envelope["messages"] == HISTORY
    assert envelope["tools"] == TOOLS
    assert turn["outputSchema"]["additionalProperties"] is False
    call_schema = turn["outputSchema"]["properties"]["tool_calls"]["items"]
    assert call_schema["additionalProperties"] is False
    assert call_schema["properties"]["arguments"] == {"type": "string"}
    assert "--strict-config" in spawns[-1][0] and "--stdio" in spawns[-1][0]
    assert spawns[-1][1]["env"]["CODEX_HOME"] == str(runtime.root)
    assert server.stopped
    assert not Path(spawns[-1][1]["cwd"]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "mcp",
        "unsafe_config",
        "approval",
        "native_tool",
        "oversize",
        "invalid_json",
        "unknown_tool",
        "bad_args",
        "extra_key",
        "missing_final",
    ],
)
async def test_unsafe_protocol_fails_closed_with_bounded_sanitized_error_and_cleanup(tmp_path, scenario):
    transport, _, processes, spawns = adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert "secret" not in str(caught.value)
    assert len(str(caught.value)) < 180
    assert processes[-1].stopped
    assert not Path(spawns[-1][1]["cwd"]).exists()
    if scenario in {"mcp", "unsafe_config"}:
        assert not any(x["method"] == "thread/start" for x in processes[-1].requests)


@pytest.mark.asyncio
async def test_native_auth_required_and_discovery_does_not_require_auth(tmp_path):
    transport, _, processes, _ = adapter(tmp_path, "no_auth")
    models = await transport.discover_models()
    assert models[0]["id"] == "gpt-5.6-luna"
    assert not any(x["method"] in {"account/read", "thread/start", "turn/start"} for x in processes[-1].requests)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.code == "llm_auth_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata,supported",
    [
        ({}, True),
        ({"multi_agent_version": "v2"}, False),
        ({"tool_mode": "direct"}, False),
        ({"experimental_supported_tools": ["send_user_message_async", "clock"]}, False),
    ],
)
async def test_native_model_availability_prechecks_metadata_without_account_or_inference(tmp_path, metadata, supported):
    transport, runtime, processes, _ = adapter(tmp_path, "no_auth")
    runtime.metadata(**metadata)

    models = await transport.model_availability()

    assert models[0]["id"] == "gpt-5.6-luna"
    assert models[0]["native_inference"] is supported
    assert not any(x["method"] in {"account/read", "thread/start", "turn/start"} for x in processes[-1].requests)


@pytest.mark.asyncio
async def test_native_model_availability_marks_missing_public_metadata_incompatible(tmp_path):
    transport, runtime, processes, _ = adapter(tmp_path)
    (runtime.root / "models_cache.json").unlink()
    models = await transport.model_availability()
    assert models[0]["native_inference"] is False
    assert not any(x["method"] in {"account/read", "thread/start", "turn/start"} for x in processes[-1].requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [{"experimental_supported_tools": ["test_sync_tool"]}, {"multi_agent_version": "v2"}, {"tool_mode": "unknown"}],
)
async def test_unsafe_model_metadata_prevents_turn(tmp_path, metadata):
    transport, runtime, processes, _ = adapter(tmp_path)
    runtime.metadata(**metadata)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.code == "llm_capability_unsupported"
    assert not any(x["method"] == "turn/start" for p in processes for x in p.requests)


@pytest.mark.asyncio
async def test_existing_managed_user_config_is_not_read_or_overwritten(tmp_path):
    transport, runtime, processes, _ = adapter(tmp_path)
    config = runtime.root / "config.toml"
    config.write_text('notify=["private-secret"]', encoding="utf-8")
    with pytest.raises(LLMTransportError):
        await transport.generate(selection(), HISTORY, TOOLS)
    assert config.read_text(encoding="utf-8") == 'notify=["private-secret"]'
    assert processes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["generate", "discover_models", "model_availability"])
async def test_unsupported_cli_version_fails_before_app_server(tmp_path, operation):
    transport, _, processes, _ = adapter(tmp_path, version="0.159.2")
    with pytest.raises(LLMTransportError) as caught:
        if operation == "generate":
            await transport.generate(selection(), HISTORY, TOOLS)
        else:
            await getattr(transport, operation)()
    assert caught.value.code == "llm_capability_unsupported"
    assert len(processes) == 1


@pytest.mark.asyncio
async def test_native_timeout_and_cancellation_stop_process_and_remove_cwd(tmp_path):
    transport, _, processes, spawns = adapter(tmp_path, "hang", timeout=0.02)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.code == "llm_timeout"
    assert processes[-1].stopped
    assert not Path(spawns[-1][1]["cwd"]).exists()
    transport.timeout = 5
    task = asyncio.create_task(transport.generate(selection(), HISTORY, TOOLS))
    while not any(x["method"] == "turn/start" for p in processes[2:] for x in p.requests):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[-1].stopped
    assert not Path(spawns[-1][1]["cwd"]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "object_args",
        "double_encoded",
        "array_args",
        "wrong_type",
        "constraint",
        "missing_required",
        "unknown_argument",
        "partial_calls",
    ],
)
async def test_wire_arguments_decode_once_and_all_tool_schemas_validate_before_return(tmp_path, scenario):
    transport, _, processes, _ = adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert "private-secret" not in str(caught.value)
    assert processes[-1].stopped


@pytest.mark.asyncio
async def test_internal_refs_validate_without_network_and_external_refs_fail_before_spawn(tmp_path):
    transport, _, _, _ = adapter(tmp_path)
    internal = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "parameters": {
                    "type": "object",
                    "$defs": {"number": {"type": "integer", "const": 1}},
                    "properties": {"value": {"$ref": "#/$defs/number"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    result = await transport.generate(selection(), HISTORY, internal)
    assert result.tool_calls[0]["function"]["name"] == "lookup"
    transport2, _, processes2, _ = adapter(tmp_path / "other")
    external = [
        {
            "type": "function",
            "function": {"name": "lookup", "parameters": {"$ref": "https://private-secret.invalid/schema"}},
        }
    ]
    with pytest.raises(LLMTransportError) as caught:
        await transport2.generate(selection(), HISTORY, external)
    assert "private-secret" not in str(caught.value)
    assert processes2 == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["no_tools", "no_usage", "plan"])
async def test_text_only_and_internal_plan_never_become_ambient_calls(tmp_path, scenario):
    transport, _, _, _ = adapter(tmp_path, scenario)
    result = await transport.generate(selection(), HISTORY)
    assert result.text == "done"
    assert result.tool_calls is None
    if scenario == "no_usage":
        assert result.usage == {}


@pytest.mark.asyncio
async def test_call_without_supplied_tool_fails_closed(tmp_path):
    transport, _, _, _ = adapter(tmp_path)
    with pytest.raises(LLMTransportError):
        await transport.generate(selection(), HISTORY)


@pytest.mark.skipif(sys.platform != "linux", reason="The pinned native profile is supported on Linux")
@pytest.mark.asyncio
async def test_cleanup_kills_owned_group_after_leader_exit_with_term_ignoring_child():
    child = "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(os.getpid(),flush=True); time.sleep(30)"
    parent = (
        "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',"
        + repr(child)
        + "],stdout=subprocess.PIPE); print(child.stdout.readline().decode().strip(),flush=True)"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    child_pid = int(await asyncio.wait_for(proc.stdout.readline(), 3))
    await asyncio.wait_for(proc.wait(), 3)

    def active():
        try:
            return Path(f"/proc/{child_pid}/stat").read_text().split()[2] != "Z"
        except FileNotFoundError:
            return False

    try:
        assert active()
        await _stop_process(proc)
        for _ in range(30):
            if not active():
                break
            await asyncio.sleep(0.02)
        assert not active(), "Owned descendant survived cleanup after its group leader exited"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["typed_tools", "bwrap_warning", "remote_disabled", "early_turn"])
async def test_verified_0145_projection_and_notification_order_are_supported(tmp_path, scenario):
    transport, _, _, _ = adapter(tmp_path, scenario)
    result = await transport.generate(selection(), HISTORY, TOOLS)
    assert result.text == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "raw_tools_enabled",
        "raw_tools_disabled",
        "raw_tools_missing",
        "unsafe_warning",
        "generic_warning",
        "remote_connected",
        "thread_write",
        "thread_network",
        "thread_approval",
        "thread_reviewer",
        "thread_cwd",
        "thread_provider",
        "thread_roots",
        "thread_instructions",
        "wrong_thread",
        "rebind_turn",
        "conflict_reply",
        "unbound_item",
    ],
)
async def test_effective_configuration_thread_and_pending_turn_fail_closed(tmp_path, scenario):
    transport, _, processes, _ = adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError):
        await transport.generate(selection(), HISTORY, TOOLS)
    assert processes[-1].stopped
    if scenario.startswith(("raw_", "thread_")) or scenario in {
        "unsafe_warning",
        "generic_warning",
        "remote_connected",
    }:
        assert not any(x["method"] == "turn/start" for x in processes[-1].requests)


def test_schema_business_keys_and_literals_are_not_interpreted_as_schema_keywords():
    schema = {
        "type": "object",
        "properties": {"$id": {"type": "string"}, "literal": {"const": {"$ref": "https://literal.invalid"}}},
    }
    validator = _closed_schema(schema)
    validator.validate({"$id": "name", "literal": {"$ref": "https://literal.invalid"}})


@pytest.mark.asyncio
async def test_unverified_platform_fails_before_any_subprocess(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / "native-state")
    monkeypatch.setattr("backend.codex_llm.sys.platform", "win32")
    with pytest.raises(LLMTransportError) as caught:
        await NativeCodexTransport(runtime).discover_models()
    assert caught.value.code == "llm_capability_unsupported"


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_owned_cleanup_before_removing_cwd(tmp_path):
    transport, _, processes, spawns = adapter(tmp_path, "hang", timeout=5)
    began = asyncio.Event()
    finish = asyncio.Event()
    original_stop = transport._stop

    async def stop(process):
        if process is processes[-1] and len(processes) > 1:
            began.set()
            await finish.wait()
        await original_stop(process)

    transport._stop = stop
    task = asyncio.create_task(transport.generate(selection(), HISTORY, TOOLS))
    while not any(x["method"] == "turn/start" for p in processes for x in p.requests):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait_for(began.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert Path(spawns[-1][1]["cwd"]).is_dir()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processes[-1].stopped
    assert not Path(spawns[-1][1]["cwd"]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "endpoint_openai_session",
        "endpoint_chatgpt_session",
        "endpoint_openai_lower",
        "endpoint_chatgpt_lower",
        "endpoint_openai_disabled",
        "endpoint_chatgpt_disabled",
        "endpoint_openai_empty",
        "endpoint_chatgpt_map",
        "endpoint_null_config",
        "endpoint_list_config",
        "endpoint_list_layer",
    ],
)
async def test_any_raw_layer_endpoint_override_or_malformed_layer_rejects_before_account_and_inference(
    tmp_path, scenario
):
    transport, _, processes, _ = adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert "private-secret" not in str(caught.value)
    assert processes[-1].stopped
    assert not any(
        request["method"] in {"account/read", "thread/start", "turn/start"} for request in processes[-1].requests
    )


@pytest.mark.asyncio
async def test_absent_and_explicit_null_endpoints_preserve_native_defaults(tmp_path):
    transport, _, _, _ = adapter(tmp_path, "endpoint_null")
    assert (await transport.generate(selection(), HISTORY, TOOLS)).text == "done"


@pytest.mark.asyncio
async def test_many_bound_agent_message_deltas_use_separate_bounded_stream_budget(tmp_path):
    transport, _, processes, _ = adapter(tmp_path, "stream_deltas")
    result = await transport.generate(selection(), HISTORY, TOOLS)
    assert result.text == "done"
    assert processes[-1].stopped


@pytest.mark.asyncio
async def test_agent_message_delta_stream_budget_remains_bounded(caplog, tmp_path):
    transport, _, processes, _ = adapter(tmp_path, "stream_overflow")
    with caplog.at_level(logging.WARNING, logger="backend.codex_llm"), pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": "output_limit"}
    record = next(record for record in caplog.records if record.limit_kind == "stream_messages")
    assert (record.observed, record.hard_limit) == (_STREAM_MESSAGE_LIMIT + 1, _STREAM_MESSAGE_LIMIT)
    assert "private-secret" not in caplog.text
    assert processes[-1].stopped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expected_reason",
    [
        ({"threadId": "foreign", "turnId": "turn-1", "itemId": "item-1", "delta": "x"}, "identity"),
        (
            {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "x", "extra": 1},
            "native_notification",
        ),
        ({"threadId": "thread-1", "turnId": "turn-1", "itemId": "unknown", "delta": "x"}, "native_notification"),
    ],
)
async def test_unbound_malformed_or_foreign_delta_never_uses_stream_credit(params, expected_reason):
    class Process:
        def __init__(self):
            self.stdout = asyncio.StreamReader()

    process = Process()
    message = {"method": "item/agentMessage/delta", "params": params}
    process.stdout.feed_data(json.dumps(message).encode() + b"\n")
    process.stdout.feed_eof()
    connection = _Connection(process)
    connection.thread_id = "thread-1"
    connection.turn_id = "turn-1"
    connection.pending_turn = True
    connection.items["item-1"] = {"id": "item-1", "type": "agentMessage"}

    received = await connection.receive()
    assert connection.control_messages == 1
    assert connection.stream_messages == 0
    with pytest.raises(LLMTransportError) as caught:
        connection.notification(received)
    assert caught.value.details["native_reason"] == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("emitted_at_ms", ["missing", -(2**63), -1, 0, 2**63 - 1])
async def test_valid_optional_delta_timestamp_uses_stream_credit(emitted_at_ms):
    class Process:
        def __init__(self):
            self.stdout = asyncio.StreamReader()

    process = Process()
    message = {
        "method": "item/agentMessage/delta",
        "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "x"},
    }
    if emitted_at_ms != "missing":
        message["emittedAtMs"] = emitted_at_ms
    process.stdout.feed_data(json.dumps(message).encode() + b"\n")
    process.stdout.feed_eof()
    connection = _Connection(process)
    connection.thread_id = "thread-1"
    connection.turn_id = "turn-1"
    connection.pending_turn = True
    connection.items["item-1"] = {"id": "item-1", "type": "agentMessage"}

    received = await connection.receive()
    assert connection.control_messages == 0
    assert connection.stream_messages == 1
    connection.notification(received)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("emittedAtMs", True),
        ("emittedAtMs", False),
        ("emittedAtMs", None),
        ("emittedAtMs", "1797000000000"),
        ("emittedAtMs", 1.5),
        ("emittedAtMs", 2**63),
        ("emittedAtMs", -(2**63) - 1),
        ("unexpected", 1),
    ],
)
async def test_malformed_delta_envelope_is_rejected_without_stream_credit(field, value):
    class Process:
        def __init__(self):
            self.stdout = asyncio.StreamReader()

    process = Process()
    message = {
        "method": "item/agentMessage/delta",
        "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "delta": "x"},
        field: value,
    }
    process.stdout.feed_data(json.dumps(message).encode() + b"\n")
    process.stdout.feed_eof()
    connection = _Connection(process)
    connection.thread_id = "thread-1"
    connection.turn_id = "turn-1"
    connection.pending_turn = True
    connection.items["item-1"] = {"id": "item-1", "type": "agentMessage"}

    received = await connection.receive()
    assert connection.control_messages == 1
    assert connection.stream_messages == 0
    with pytest.raises(LLMTransportError) as caught:
        connection.notification(received)
    assert caught.value.details["native_reason"] == "native_notification"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("native_tool", "native_item"),
        ("approval", "callback"),
        ("generic_warning", "native_notification"),
        ("unknown_notification", "native_notification"),
        ("upstream_error", "known_upstream_error"),
        ("mcp", "configuration"),
        ("thread_network", "thread_policy"),
        ("wrong_thread", "identity"),
        ("rebind_turn", "identity"),
        ("conflict_reply", "identity"),
        ("unbound_item", "identity"),
        ("invalid_json", "final_output"),
        ("wrong_type", "final_output"),
        ("oversize", "output_limit"),
    ],
)
async def test_fixed_rejection_reason_survives_existing_audit_string_without_private_protocol(
    tmp_path, scenario, reason
):
    transport, _, processes, _ = adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.details == {"native_reason": reason}
    assert f"[native:{reason}]" in str(caught.value)
    assert "private-secret" not in str(caught.value)
    assert "private-secret" not in json.dumps(caught.value.details)
    assert processes[-1].stopped
    assert len([request for request in processes[-1].requests if request["method"] == "turn/start"]) <= 1


@pytest.mark.asyncio
async def test_metadata_and_input_limits_have_fixed_reasons_before_any_turn(tmp_path):
    transport, runtime, processes, _ = adapter(tmp_path)
    runtime.metadata(multi_agent_version="v2")
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY)
    assert caught.value.details == {"native_reason": "model_metadata"}
    assert not any(request["method"] == "turn/start" for request in processes[-1].requests)
    transport, _, processes, _ = adapter(tmp_path / "oversized")
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), [{"role": "user", "content": "x" * (512 * 1024)}])
    assert caught.value.details == {"native_reason": "input_limit"}
    assert processes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit_kind,observed,hard_limit",
    [
        ("line_bytes", _BYTE_LIMIT + 1, _BYTE_LIMIT),
        ("total_bytes", _BYTE_LIMIT + 1, _BYTE_LIMIT),
        ("control_messages", _MESSAGE_LIMIT + 1, _MESSAGE_LIMIT),
        ("items", _ITEM_LIMIT + 1, _ITEM_LIMIT),
    ],
)
async def test_native_output_limits_log_only_fixed_scalar_diagnostics(caplog, limit_kind, observed, hard_limit):
    class Process:
        def __init__(self, line_limit=_BYTE_LIMIT):
            self.stdout = asyncio.StreamReader(limit=line_limit)

    process = Process()
    connection = _Connection(process)
    small_line = json.dumps({"id": 1, "result": {}}).encode() + b"\n"
    if limit_kind == "line_bytes":
        process.stdout.feed_data(b"x" * (_BYTE_LIMIT + 1) + b"\n")
        process.stdout.feed_eof()
    elif limit_kind == "total_bytes":
        connection.bytes = _BYTE_LIMIT - len(small_line) + 1
        process.stdout.feed_data(small_line)
        process.stdout.feed_eof()
    elif limit_kind == "control_messages":
        connection.control_messages = _MESSAGE_LIMIT
        process.stdout.feed_data(small_line)
        process.stdout.feed_eof()
    else:
        with caplog.at_level(logging.WARNING, logger="backend.codex_llm"), pytest.raises(LLMTransportError) as caught:
            for index in range(_ITEM_LIMIT + 1):
                connection._item({"id": f"item-{index}", "type": "reasoning"})
        assert caught.value.details == {"native_reason": "output_limit"}
        assert any(record.limit_kind == limit_kind for record in caplog.records)
        record = next(record for record in caplog.records if record.limit_kind == limit_kind)
        assert (record.observed, record.hard_limit) == (observed, hard_limit)
        assert record.getMessage() == "Native Codex output bound reached"
        assert "item-" not in record.getMessage()
        return

    with caplog.at_level(logging.WARNING, logger="backend.codex_llm"), pytest.raises(LLMTransportError) as caught:
        await connection.receive()
    assert caught.value.details == {"native_reason": "output_limit"}
    record = next(record for record in caplog.records if record.limit_kind == limit_kind)
    assert (record.observed, record.hard_limit) == (observed, hard_limit)
    assert record.getMessage() == "Native Codex output bound reached"
    assert "private-secret" not in caplog.text


def test_unrecognized_diagnostic_reason_cannot_enter_message_or_audit():
    error = _error(reason="private-secret/raw-method")
    assert error.details == {"native_reason": "protocol"}
    assert "private-secret" not in str(error)


@pytest.mark.asyncio
async def test_official_retry_notice_waits_for_same_turn_without_new_rpc(tmp_path):
    transport, _, processes, _ = adapter(tmp_path, "error_retry")
    assert (await transport.generate(selection(), HISTORY, TOOLS)).text == "done"
    assert sum(request["method"] == "turn/start" for request in processes[-1].requests) == 1
    assert sum(request["method"] == "thread/start" for request in processes[-1].requests) == 1
    assert processes[-1].stopped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,code",
    [
        ("error_rate", "llm_rate_limited"),
        ("error_session_budget", "llm_rate_limited"),
        ("error_auth", "llm_auth_failed"),
        ("error_provider", "llm_provider_error"),
        ("error_http_rate", "llm_rate_limited"),
        ("error_http_timeout", "llm_timeout"),
        ("error_http_auth", "llm_auth_failed"),
        ("error_stream", "llm_provider_error"),
        ("error_active", "llm_provider_error"),
        ("error_null_info", "llm_provider_error"),
        ("error_omitted_info", "llm_provider_error"),
    ],
)
async def test_terminal_typed_upstream_error_uses_fixed_codes_without_content(tmp_path, scenario, code):
    transport, _, processes, _ = adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.code == code
    assert caught.value.details == {"native_reason": "known_upstream_error"}
    assert "private-secret" not in str(caught.value)
    assert processes[-1].stopped
    assert sum(request["method"] == "turn/start" for request in processes[-1].requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "error_unknown_info",
        "error_unknown_variant",
        "error_bad_status",
        "error_bad_kind",
        "error_extra_inner",
        "error_bad_retry",
        "error_bad_message",
        "error_bad_details",
        "error_missing_turn",
        "error_extra",
        "error_wrong_thread",
        "error_wrong_turn",
    ],
)
async def test_unknown_upstream_shape_or_foreign_turn_remains_fail_closed(tmp_path, scenario):
    transport, _, processes, _ = adapter(tmp_path, scenario)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.details["native_reason"] in {"identity", "known_upstream_error"}
    assert "private-secret" not in str(caught.value)
    assert processes[-1].stopped


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario,code,reason",
    [("error_retry_hang", "llm_timeout", "protocol"), ("error_retry_budget", "llm_provider_error", "output_limit")],
)
async def test_retry_notices_obey_existing_deadline_and_message_budget(tmp_path, scenario, code, reason):
    transport, _, processes, _ = adapter(tmp_path, scenario, timeout=0.03 if scenario == "error_retry_hang" else 1)
    with pytest.raises(LLMTransportError) as caught:
        await transport.generate(selection(), HISTORY, TOOLS)
    assert caught.value.code == code
    assert caught.value.details == {"native_reason": reason}
    assert processes[-1].stopped
    assert sum(request["method"] == "turn/start" for request in processes[-1].requests) == 1
