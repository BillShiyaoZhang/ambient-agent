"""Bounded, isolated official Codex app-server transport for Ambient inference.

Ambient tools are JSON choices in the final answer, never native dynamic tools.
Pinned profiles isolate internal computation, read-only clock and bounded async messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import NoSuchResource

from backend.coding_agent_runtime import CodingAgentRuntime
from backend.llm_config import ResolvedModel
from backend.llm_service import LLMResult, LLMTransportError

_VERSION = "0.145.0"
_MODERN_VERSION = "0.159.3"
_MODERN_PLAN_TYPES = (
    None,
    "free",
    "go",
    "plus",
    "pro",
    "prolite",
    "promax",
    "team",
    "self_serve_business_prolite",
    "self_serve_business_usage_based",
    "business",
    "ent26",
    "enterprise_cbp_automation",
    "enterprise_cbp_usage_based",
    "enterprise",
    "edu",
    "edu_plus",
    "edu_pro",
    "unknown",
)
_HOST_DISABLED_WARNING = (
    "Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; "
    "enable `features.code_mode_host` and install `codex-code-mode-host`."
)
_BYTE_LIMIT = 2 * 1024 * 1024
_INPUT_LIMIT = 512 * 1024
_MESSAGE_LIMIT = 1024
_STREAM_MESSAGE_LIMIT = 8192
_ITEM_LIMIT = 64
_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1
_logger = logging.getLogger(__name__)
_REJECTION_REASONS = frozenset(
    {
        "configuration",
        "model_metadata",
        "input_limit",
        "output_limit",
        "native_item",
        "native_notification",
        "known_upstream_error",
        "callback",
        "protocol",
        "thread_policy",
        "identity",
        "final_output",
    }
)
_BUNDLED_BWRAP_WARNING = (
    "Codex could not find bubblewrap on PATH. Install bubblewrap with your OS package manager. "
    "See the sandbox prerequisites: https://developers.openai.com/codex/concepts/sandboxing#prerequisites. "
    "Codex will use the bundled bubblewrap in the meantime."
)
_DISABLED_FEATURES = (
    "shell_tool",
    "multi_agent",
    "multi_agent_v2",
    "apps",
    "enable_mcp_apps",
    "plugins",
    "remote_plugin",
    "tool_suggest",
    "hooks",
    "memories",
    "goals",
    "image_generation",
    "standalone_web_search",
    "skill_mcp_dependency_install",
    "skill_search",
    "deferred_executor",
    "request_permissions_tool",
    "token_budget",
    "current_time_reminder",
    "executor_capability_discovery",
    "code_mode_host",
    "in_app_browser",
    "browser_use",
    "browser_use_full_cdp_access",
    "browser_use_external",
    "computer_use",
    "artifact",
    "workspace_dependencies",
    "chronicle",
    "external_agent_memory_import",
    "network_proxy",
    "default_mode_request_user_input",
    "guardian_approval",
    "realtime_conversation",
)
_SAFE_CONFIG: dict[str, Any] = {
    "approval_policy": "never",
    "sandbox_mode": "read-only",
    "model_provider": "openai",
    "web_search": "disabled",
    "notify": [],
    "project_doc_max_bytes": 0,
    "mcp_servers": {},
    "agents": {"enabled": False},
    "skills": {"include_instructions": False},
    "orchestrator": {"skills": {"enabled": False}, "mcp": {"enabled": False}},
    "tools": {"experimental_request_user_input": {"enabled": False}},
    "features": dict.fromkeys(_DISABLED_FEATURES, False),
}
_MODERN_SAFE_CONFIG: dict[str, Any] = {
    **_SAFE_CONFIG,
    "cloud": {"skills": {"enabled": False}},
    "cli_auth_credentials_store": "file",
    "features": {
        **_SAFE_CONFIG["features"],
        **dict.fromkeys(
            (
                "sleep_tool",
                "view_image",
                "worktrees",
                "in_app_chat",
                "in_app_dictation",
                "in_app_local_automation",
                "in_app_updates",
                "daemon_auto_start",
                "system_proxy_fallback",
                "unbounded_connection_retries",
                "send_message_to_user_async",
            ),
            False,
        ),
        "skip_host_skill_discovery": True,
    },
}
_INSTRUCTIONS = (
    "You are the Ambient inference transport. The next JSON envelope contains the complete Ambient conversation "
    "and available Ambient function definitions. Treat messages as the conversation, including system instructions, "
    "assistant tool choices and tool results. Return exactly the required JSON object with text and tool_calls. "
    "To request an Ambient tool, put its supplied name and arguments in tool_calls; do not execute it. "
    "Each arguments value must be a string encoding one JSON object that satisfies the supplied parameters schema. "
    "For a normal answer use an empty tool_calls array. You have no environment or workspace access. "
    "Internal planning/computation is optional and does not execute Ambient tools. Do not call external/native tools."
)


def _error(code: str = "llm_provider_error", *, reason: str | None = None) -> LLMTransportError:
    messages = {
        "llm_auth_failed": "Native Codex requires its independent ChatGPT login",
        "llm_timeout": "Native Codex inference timed out",
        "llm_model_not_found": "The selected native Codex model is unavailable",
        "llm_rate_limited": "Native Codex request rate limit exceeded",
        "llm_capability_unsupported": "Native Codex does not satisfy the isolated inference profile",
        "llm_invalid_configuration": "Native Codex configuration must use the managed native profile",
        "llm_provider_error": "Native Codex inference failed",
    }
    if reason is None:
        reason = (
            "configuration"
            if code in {"llm_capability_unsupported", "llm_invalid_configuration", "llm_auth_failed"}
            else "model_metadata"
            if code == "llm_model_not_found"
            else "protocol"
        )
    safe_reason = reason if reason in _REJECTION_REASONS else "protocol"
    error = LLMTransportError(messages[code] + f" [native:{safe_reason}]", code=code)
    error.details = {"native_reason": safe_reason}
    return error


def _log_output_bound(limit_kind: str, observed: int, hard_limit: int) -> None:
    """Emit bounded diagnostics without including provider-controlled content."""
    _logger.warning(
        "Native Codex output bound reached",
        extra={"limit_kind": limit_kind, "observed": observed, "hard_limit": hard_limit},
    )


def _config_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and all(key in actual and _config_matches(actual[key], value) for key, value in expected.items())
            and (bool(expected) or not actual)
        )
    return type(actual) is type(expected) and actual == expected


def _overrides(config: dict[str, Any], prefix: str = "") -> list[str]:
    args: list[str] = []
    for key, value in config.items():
        name = prefix + key
        if isinstance(value, dict) and value:
            args.extend(_overrides(value, name + "."))
        else:
            args.extend(["-c", name + "=" + json.dumps(value, separators=(",", ":"))])
    return args


def _deny_resource(uri: str):
    raise NoSuchResource(ref=uri)


def _closed_schema(schema: dict[str, Any]) -> Any:
    # Only subschema positions are schemas. Property names, const and enum are data.
    mappings = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    singles = {
        "additionalProperties",
        "additionalItems",
        "contains",
        "propertyNames",
        "not",
        "if",
        "then",
        "else",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contentSchema",
    }
    sequences = {"allOf", "anyOf", "oneOf", "prefixItems"}

    def check(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise _error("llm_invalid_configuration")
                if key == "$id":
                    raise _error("llm_invalid_configuration")
                if key in mappings and isinstance(item, dict):
                    for child in item.values():
                        check(child)
                elif key in singles:
                    check(item)
                elif key in sequences and isinstance(item, list):
                    for child in item:
                        check(child)
                elif key == "items":
                    for child in item if isinstance(item, list) else [item]:
                        check(child)
                elif key == "dependencies" and isinstance(item, dict):
                    for child in item.values():
                        if isinstance(child, dict):
                            check(child)

    check(schema)
    validator = validator_for(schema, default=Draft202012Validator)
    if "$schema" in schema and validator_for(schema, default=None) is None:
        raise _error("llm_invalid_configuration")
    validator.check_schema(schema)
    return validator(schema, registry=Registry(retrieve=_deny_resource), format_checker=validator.FORMAT_CHECKER)


def _safe_effective_config(response: dict[str, Any], expected: dict[str, Any] = _SAFE_CONFIG) -> bool:
    # Both pinned typed ToolsV2 schemas omit this legacy knob. Effective sessionFlags
    # layer is returned first and must explicitly disable the actual core knob.
    projected = {key: value for key, value in expected.items() if key != "tools"}
    if not _config_matches(response.get("config"), projected):
        return False
    layers = response.get("layers")
    if not isinstance(layers, list) or not layers or len(layers) > 100:
        return False
    for candidate in layers:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("config"), dict):
            return False
        # A lower-precedence or disabled custom route is still outside the native
        # profile. Pinned .145 gives this URL priority even for ChatGPT auth.
        if any(candidate["config"].get(key) is not None for key in ("openai_base_url", "chatgpt_base_url")):
            return False
    layer = layers[0]
    return (
        isinstance(layer, dict)
        and (layer.get("name") or {}).get("type") == "sessionFlags"
        and layer.get("disabledReason") is None
        and _config_matches((layer.get("config") or {}).get("tools"), expected["tools"])
    )


async def _finish_owned(task: asyncio.Task, *, propagate_cancel: bool = True):
    """Repeated cancellation cannot abandon an owned spawn or cleanup task."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError
    return result


def _json_object(raw: str) -> dict[str, Any]:
    def reject_constant(_value: str):
        raise _error(reason="final_output")

    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except (ValueError, TypeError):
        raise _error(reason="final_output") from None
    if not isinstance(value, dict):
        raise _error(reason="final_output")
    return value


def _upstream_error_code(info: Any, version: str = _VERSION) -> str:
    """Validate only the pinned public enum; never inspect error text."""
    if info is None:
        return "llm_provider_error"
    if isinstance(info, str):
        known = {
            "contextWindowExceeded",
            "sessionBudgetExceeded",
            "usageLimitExceeded",
            "serverOverloaded",
            "cyberPolicy",
            "internalServerError",
            "unauthorized",
            "badRequest",
            "threadRollbackFailed",
            "sandboxError",
            "other",
        }
        if version == _MODERN_VERSION:
            known |= {"rateLimitExceeded", "flexUnavailable", "misalignmentPolicyViolation", "tooManyDenials"}
        if info not in known:
            raise _error("llm_capability_unsupported", reason="known_upstream_error")
        if info in {"sessionBudgetExceeded", "usageLimitExceeded", "rateLimitExceeded"}:
            return "llm_rate_limited"
        return "llm_auth_failed" if info == "unauthorized" else "llm_provider_error"
    if not isinstance(info, dict) or len(info) != 1:
        raise _error("llm_capability_unsupported", reason="known_upstream_error")
    variant, value = next(iter(info.items()))
    if not isinstance(value, dict):
        raise _error("llm_capability_unsupported", reason="known_upstream_error")
    if variant == "activeTurnNotSteerable":
        if set(value) != {"turnKind"} or value["turnKind"] not in {"review", "compact"}:
            raise _error("llm_capability_unsupported", reason="known_upstream_error")
        return "llm_provider_error"
    if variant not in {
        "httpConnectionFailed",
        "responseStreamConnectionFailed",
        "responseStreamDisconnected",
        "responseTooManyFailedAttempts",
    } or set(value) - {"httpStatusCode"}:
        raise _error("llm_capability_unsupported", reason="known_upstream_error")
    status = value.get("httpStatusCode")
    if status is not None and (type(status) is not int or not 0 <= status <= 65535):
        raise _error("llm_capability_unsupported", reason="known_upstream_error")
    if status == 429:
        return "llm_rate_limited"
    if status in {408, 504}:
        return "llm_timeout"
    if status in {401, 403}:
        return "llm_auth_failed"
    return "llm_provider_error"


async def _stop_process(proc: asyncio.subprocess.Process) -> None:
    """Clean up only the process/group created for this inference."""
    if proc.stdin is not None:
        with contextlib.suppress(OSError, RuntimeError):
            proc.stdin.close()
    if os.name == "nt":
        # Production native inference fails closed on Windows until job-object
        # process-tree ownership is implemented. This fallback is not a guarantee.
        if proc.returncode is not None:
            return
        # A Windows process group alone does not kill descendants.
        with contextlib.suppress(OSError):
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), 2)
            except TimeoutError:
                killer.kill()
                await killer.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        deadline = asyncio.get_running_loop().time() + 2
        while asyncio.get_running_loop().time() < deadline:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.02)
        # The leader can exit while a descendant ignores TERM. Always address
        # the group we created with start_new_session, even after leader exit.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), 2)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        await asyncio.wait_for(proc.wait(), 2)


class _Connection:
    def __init__(self, process: asyncio.subprocess.Process, version: str = _VERSION, home: Path | None = None):
        self.process = process
        self.version = version
        self.home = home
        self.sequence = 0
        self.bytes = 0
        self.messages = 0
        self.control_messages = 0
        self.stream_messages = 0
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.items: dict[str, dict[str, Any]] = {}
        self.usage: dict[str, int] = {}
        self.completed: dict[str, Any] | None = None
        self.pending_turn = False

    async def send(self, method: str, params: dict[str, Any], request_id: int | None = None) -> None:
        if self.process.stdin is None:
            raise _error()
        value: dict[str, Any] = {"method": method, "params": params}
        if request_id is not None:
            value["id"] = request_id
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > _INPUT_LIMIT:
            raise _error("llm_capability_unsupported", reason="input_limit")
        self.process.stdin.write(raw + b"\n")
        await self.process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise _error()
        try:
            raw = await self.process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError):
            _log_output_bound("line_bytes", _BYTE_LIMIT + 1, _BYTE_LIMIT)
            raise _error(reason="output_limit") from None
        self.bytes += len(raw)
        self.messages += 1
        if self.bytes > _BYTE_LIMIT:
            _log_output_bound("total_bytes", self.bytes, _BYTE_LIMIT)
            raise _error(reason="output_limit")
        if not raw:
            raise _error()
        self.control_messages += 1
        try:
            message = json.loads(raw)
        except (ValueError, TypeError, UnicodeDecodeError):
            if self.control_messages > _MESSAGE_LIMIT:
                _log_output_bound("control_messages", self.control_messages, _MESSAGE_LIMIT)
                raise _error(reason="output_limit") from None
            raise
        if self._is_stream_delta(message):
            self.control_messages -= 1
            self.stream_messages += 1
            if self.stream_messages > _STREAM_MESSAGE_LIMIT:
                _log_output_bound("stream_messages", self.stream_messages, _STREAM_MESSAGE_LIMIT)
                raise _error(reason="output_limit")
        elif self.control_messages > _MESSAGE_LIMIT:
            _log_output_bound("control_messages", self.control_messages, _MESSAGE_LIMIT)
            raise _error(reason="output_limit")
        if not isinstance(message, dict) or ("method" in message and "id" in message):
            # Approval, dynamic tool and auth refresh callbacks are never answered.
            raise _error("llm_capability_unsupported", reason="callback")
        return message

    def _is_stream_delta(self, message: Any) -> bool:
        emitted_at_ms = message.get("emittedAtMs") if isinstance(message, dict) else None
        valid_envelope = (
            isinstance(message, dict)
            and set(message) in ({"method", "params"}, {"method", "params", "emittedAtMs"})
            and (
                "emittedAtMs" not in message
                or (type(emitted_at_ms) is int and _INT64_MIN <= emitted_at_ms <= _INT64_MAX)
            )
        )
        if (
            not valid_envelope
            or message.get("method") != "item/agentMessage/delta"
            or not self.pending_turn
            or self.completed is not None
            or not isinstance(self.thread_id, str)
            or not self.thread_id
            or not isinstance(self.turn_id, str)
            or not self.turn_id
        ):
            return False
        params = message.get("params")
        if (
            not isinstance(params, dict)
            or set(params) != {"threadId", "turnId", "itemId", "delta"}
            or params.get("threadId") != self.thread_id
            or params.get("turnId") != self.turn_id
            or not isinstance(params.get("itemId"), str)
            or not isinstance(params.get("delta"), str)
        ):
            return False
        item = self.items.get(params["itemId"])
        return isinstance(item, dict) and item.get("type") == "agentMessage"

    def _item(self, item: Any) -> None:
        if not isinstance(item, dict) or item.get("type") not in {"userMessage", "agentMessage", "reasoning", "plan"}:
            raise _error("llm_capability_unsupported", reason="native_item")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
            raise _error()
        previous = self.items.get(identifier)
        if self.version == _MODERN_VERSION and previous and previous["type"] != item["type"]:
            raise _error("llm_capability_unsupported", reason="native_item")
        if item["type"] == "agentMessage":
            delivery = item.get("delivery")
            if delivery is not None and (self.version != _MODERN_VERSION or delivery != "async"):
                raise _error("llm_capability_unsupported", reason="native_item")
            if self.version == _MODERN_VERSION:
                if (
                    set(item) - {"type", "id", "text", "phase", "memoryCitation", "delivery", "questions"}
                    or not isinstance(item.get("text"), str)
                    or item.get("phase") not in (None, "commentary", "final_answer")
                    or item.get("memoryCitation") is not None
                ):
                    raise _error("llm_capability_unsupported", reason="native_item")
                questions = item.get("questions")
                if delivery == "async":
                    if not isinstance(questions, list) or not 1 <= len(questions) <= _ITEM_LIMIT:
                        raise _error("llm_capability_unsupported", reason="native_item")
                    for question in questions:
                        if (
                            not isinstance(question, dict)
                            or set(question) - {"title", "options"}
                            or not isinstance(question.get("title"), str)
                            or not question["title"].strip()
                            or (
                                question.get("options") is not None
                                and (
                                    not isinstance(question["options"], list)
                                    or not 1 <= len(question["options"]) <= _ITEM_LIMIT
                                    or any(
                                        not isinstance(option, str) or not option.strip()
                                        for option in question["options"]
                                    )
                                )
                            )
                        ):
                            raise _error("llm_capability_unsupported", reason="native_item")
                elif questions is not None:
                    raise _error("llm_capability_unsupported", reason="native_item")
                if previous and previous.get("delivery") != delivery:
                    raise _error("llm_capability_unsupported", reason="native_item")
        self.items[identifier] = item
        if len(self.items) > _ITEM_LIMIT:
            _log_output_bound("items", len(self.items), _ITEM_LIMIT)
            raise _error(reason="output_limit")

    def notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            raise _error()
        if params.get("threadId") is not None and params["threadId"] != self.thread_id:
            raise _error(reason="identity")
        if params.get("turnId") is not None and params["turnId"] != self.turn_id:
            raise _error(reason="identity")
        if method in {"item/started", "item/completed"}:
            self._item(params.get("item"))
        elif method == "thread/tokenUsage/updated":
            total = (params.get("tokenUsage") or {}).get("total")
            if not isinstance(total, dict):
                raise _error()
            for upstream, local in (
                ("inputTokens", "input_tokens"),
                ("outputTokens", "output_tokens"),
                ("totalTokens", "total_tokens"),
            ):
                value = total.get(upstream)
                if type(value) is not int or value < 0:
                    raise _error()
                self.usage[local] = value
        elif method == "turn/completed":
            turn = params.get("turn")
            if not isinstance(turn, dict) or turn.get("id") != self.turn_id or turn.get("status") != "completed":
                raise _error()
            for item in turn.get("items") or []:
                self._item(item)
            self.completed = turn
        elif method == "thread/started":
            thread = params.get("thread") or {}
            if self.thread_id is not None and thread.get("id") != self.thread_id:
                raise _error(reason="identity")
        elif method == "turn/started":
            turn = params.get("turn") or {}
            identifier = turn.get("id")
            if (
                not self.pending_turn
                or params.get("threadId") != self.thread_id
                or not isinstance(identifier, str)
                or not identifier
                or len(identifier) > 256
            ):
                raise _error(reason="identity")
            if self.turn_id is not None and identifier != self.turn_id:
                raise _error(reason="identity")
            self.turn_id = identifier
        elif method == "error":
            if (
                self.thread_id is None
                or self.turn_id is None
                or params.get("threadId") != self.thread_id
                or params.get("turnId") != self.turn_id
            ):
                raise _error(reason="identity")
            error = params.get("error")
            if (
                set(params) != {"error", "threadId", "turnId", "willRetry"}
                or type(params["willRetry"]) is not bool
                or not isinstance(error, dict)
                or set(error) - {"message", "additionalDetails", "codexErrorInfo"}
                or not isinstance(error.get("message"), str)
                or (error.get("additionalDetails") is not None and not isinstance(error["additionalDetails"], str))
            ):
                raise _error("llm_capability_unsupported", reason="known_upstream_error")
            code = _upstream_error_code(error.get("codexErrorInfo"), self.version)
            if not params["willRetry"]:
                raise _error(code, reason="known_upstream_error")
            # The official server is retrying the existing turn. No new request
            # is sent; receive byte/message bounds and the original deadline hold.
        elif method == "configWarning":
            if params.get("summary") != _BUNDLED_BWRAP_WARNING or any(
                params.get(key) is not None for key in ("details", "path", "range")
            ):
                raise _error("llm_capability_unsupported", reason="native_notification")
        elif method == "remoteControl/status/changed":
            if params.get("status") != "disabled" or params.get("environmentId") is not None:
                raise _error("llm_capability_unsupported", reason="native_notification")
        elif method == "account/updated":
            if (
                self.version != _MODERN_VERSION
                or set(params) != {"authMode", "planType"}
                or params["authMode"] not in (None, "chatgpt")
                or params["planType"] not in _MODERN_PLAN_TYPES
            ):
                raise _error("llm_capability_unsupported", reason="native_notification")
        elif method == "warning":
            startup_warning = (
                "Under-development features enabled: skip_host_skill_discovery. "
                "Under-development features are incomplete and may behave unpredictably. "
                "To suppress this warning, set `suppress_unstable_features_warning = true` in "
                f"{self.home}/config.toml."
            )
            if (
                self.version != _MODERN_VERSION
                or self.home is None
                or self.thread_id is None
                or set(params) != {"threadId", "message"}
                or params["threadId"] != self.thread_id
                or params["message"] not in (startup_warning, _HOST_DISABLED_WARNING)
            ):
                raise _error("llm_capability_unsupported", reason="native_notification")
        elif method == "item/agentMessage/delta":
            if not self._is_stream_delta(message):
                raise _error("llm_capability_unsupported", reason="native_notification")
        elif method in {
            "item/reasoning/summaryTextDelta",
            "item/reasoning/textDelta",
            "item/reasoning/summaryPartAdded",
            "item/plan/delta",
            "turn/plan/updated",
            "thread/status/changed",
            "account/rateLimits/updated",
            "model/verification",
            "model/safetyBuffering/updated",
            "turn/moderationMetadata",
        }:
            pass
        else:
            # This includes hooks, environment access, MCP startup and model rerouting.
            raise _error("llm_capability_unsupported", reason="native_notification")

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.sequence += 1
        identifier = self.sequence
        await self.send(method, params, identifier)
        while True:
            message = await self.receive()
            if "method" in message:
                self.notification(message)
                continue
            if message.get("id") != identifier or "error" in message or not isinstance(message.get("result"), dict):
                raise _error()
            return message["result"]


class NativeCodexTransport:
    def __init__(
        self,
        runtime: CodingAgentRuntime,
        *,
        timeout: float = 90,
        process_factory: Callable[..., Awaitable[Any]] | None = None,
        stop_process: Callable[[Any], Awaitable[None]] | None = None,
    ):
        self.runtime = runtime
        self.timeout = timeout
        self._spawn = process_factory or asyncio.create_subprocess_exec
        self._stop = stop_process or _stop_process
        self._injected_process = process_factory is not None

    def _guard_home(self, home: Path | None = None) -> Path:
        home = home or getattr(self.runtime, "inference_state_dir", self.runtime.state_dir)("codex")
        try:
            (home / "config.toml").lstat()
        except FileNotFoundError:
            return home
        raise _error("llm_capability_unsupported")

    def _model_metadata(self, home: Path, model: str, version: str = _VERSION) -> None:
        # Only public catalog metadata is read; authentication remains CLI-owned.
        try:
            descriptor = os.open(home / "models_cache.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise _error("llm_capability_unsupported", reason="model_metadata")
                raw = source.read(_BYTE_LIMIT + 1)
            if len(raw) > _BYTE_LIMIT:
                raise _error("llm_capability_unsupported", reason="model_metadata")
            catalog = json.loads(raw)
        except (OSError, ValueError, TypeError):
            raise _error("llm_capability_unsupported", reason="model_metadata") from None
        models = catalog.get("models") if isinstance(catalog, dict) else None
        if not isinstance(models, list) or len(models) > 1000:
            raise _error("llm_capability_unsupported", reason="model_metadata")
        matches = [item for item in models if isinstance(item, dict) and item.get("slug") == model]
        if len(matches) != 1:
            raise _error("llm_capability_unsupported", reason="model_metadata")
        item = matches[0]
        supported_tools = item.get("experimental_supported_tools")
        allowed_tools = (
            {"clock", "send_user_message_async", "request_user_input_async"} if version == _MODERN_VERSION else set()
        )
        versions = {"v1", "v2"} if version == _MODERN_VERSION else {"v1"}
        if (
            item.get("tool_mode") != "code_mode_only"
            or not isinstance(item.get("multi_agent_version"), str)
            or item["multi_agent_version"] not in versions
            or not isinstance(supported_tools, list)
            or len(supported_tools) > len(allowed_tools)
            or any(not isinstance(tool, str) or tool not in allowed_tools for tool in supported_tools)
            or len(set(supported_tools)) != len(supported_tools)
        ):
            raise _error("llm_capability_unsupported", reason="model_metadata")

    async def _owned_process(self, argv: list[str], cwd: str, environment: dict[str, str]):
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.DEVNULL,
            "cwd": cwd,
            "env": environment,
            "limit": _BYTE_LIMIT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        task = asyncio.create_task(self._spawn(*argv, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancellation during process creation still takes ownership of cleanup.
            proc = await _finish_owned(task, propagate_cancel=False)
            await _finish_owned(asyncio.create_task(self._stop(proc)), propagate_cancel=False)
            raise

    async def _with_connection(self, operation: Callable[[_Connection, Path, str], Awaitable[Any]]) -> Any:
        proc = None
        try:
            async with asyncio.timeout(self.timeout):
                if sys.platform != "linux" and not self._injected_process:
                    raise _error("llm_capability_unsupported")
                home = self._guard_home()
                command = getattr(self.runtime, "inference_command", self.runtime.command)("codex")
                if not command:
                    raise _error("llm_capability_unsupported")
                environment = getattr(self.runtime, "inference_environment", self.runtime.process_environment)("codex")
                if Path(environment.get("CODEX_HOME", "")).resolve() != home.resolve():
                    raise _error("llm_invalid_configuration")
                with tempfile.TemporaryDirectory(prefix="ambient-native-inference-") as cwd:
                    # Reject TMPDIR choices inside a repository; never infer in a user project.
                    if any((ancestor / ".git").exists() for ancestor in (Path(cwd), *Path(cwd).parents)):
                        raise _error("llm_capability_unsupported")
                    try:
                        proc = await self._owned_process([*command, "--version"], cwd, environment)
                        if proc.stdout is None:
                            raise _error()
                        version = await proc.stdout.read(256)
                        match = re.fullmatch(rb"codex-cli (0\.145\.0|0\.159\.3)\s*", version)
                        if await proc.wait() != 0 or match is None:
                            raise _error("llm_capability_unsupported")
                        profile_version = match[1].decode("ascii")
                        safe_config = _MODERN_SAFE_CONFIG if profile_version == _MODERN_VERSION else _SAFE_CONFIG
                        await self._stop(proc)
                        proc = None
                        self._guard_home(home)
                        proc = await self._owned_process(
                            [*command, "app-server", "--stdio", "--strict-config", *_overrides(safe_config)],
                            cwd,
                            environment,
                        )
                        connection = _Connection(proc, profile_version, home)
                        initialized = await connection.request(
                            "initialize",
                            {
                                "clientInfo": {"name": "ambient-native-inference", "version": "1"},
                                "capabilities": {"experimentalApi": True},
                            },
                        )
                        if not re.search(
                            rf"\b{re.escape(profile_version)}(?=$|[\s;)])", str(initialized.get("userAgent", ""))
                        ):
                            raise _error("llm_capability_unsupported")
                        await connection.send("initialized", {})
                        configured = await connection.request("config/read", {"cwd": cwd, "includeLayers": True})
                        if not _safe_effective_config(configured, safe_config):
                            raise _error("llm_capability_unsupported")
                        cursor = None
                        seen: set[str] = set()
                        for _ in range(10):
                            mcp = await connection.request("mcpServerStatus/list", {"cursor": cursor, "limit": 100})
                            if mcp.get("data") != []:
                                raise _error("llm_capability_unsupported")
                            cursor = mcp.get("nextCursor")
                            if cursor is None:
                                break
                            if not isinstance(cursor, str) or cursor in seen:
                                raise _error()
                            seen.add(cursor)
                        else:
                            raise _error()
                        self._guard_home(home)
                        return await operation(connection, home, cwd)
                    finally:
                        if proc is not None:
                            cleanup = asyncio.create_task(self._stop(proc))
                            try:
                                await _finish_owned(cleanup)
                            finally:
                                proc = None
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise _error("llm_timeout") from None
        except LLMTransportError:
            raise
        except Exception:
            raise _error() from None
        finally:
            if proc is not None:
                cleanup = asyncio.create_task(self._stop(proc))
                with contextlib.suppress(Exception):
                    await _finish_owned(cleanup)

    async def _catalog(self, connection: _Connection) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        cursor = None
        cursors: set[str] = set()
        for _ in range(10):
            response = await connection.request("model/list", {"cursor": cursor, "limit": 100, "includeHidden": False})
            data = response.get("data")
            if not isinstance(data, list) or len(data) > 100:
                raise _error()
            for item in data:
                if not isinstance(item, dict):
                    raise _error()
                identifier = item.get("id")
                if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
                    raise _error()
                models.append({"id": identifier, "name": str(item.get("displayName") or identifier)[:256]})
            cursor = response.get("nextCursor")
            if cursor is None:
                return models
            if not isinstance(cursor, str) or cursor in cursors:
                raise _error()
            cursors.add(cursor)
        raise _error()

    async def discover_models(self) -> list[dict[str, Any]]:
        async def discover(connection: _Connection, _home: Path, _cwd: str):
            return await self._catalog(connection)

        return await self._with_connection(discover)

    async def model_availability(self) -> list[dict[str, Any]]:
        """Precheck the pinned inference catalog; this does not verify account access."""

        async def discover(connection: _Connection, home: Path, _cwd: str):
            models = await self._catalog(connection)
            for model in models:
                try:
                    self._model_metadata(home, model["id"], connection.version)
                except (LLMTransportError, OSError, ValueError, TypeError):
                    model["native_inference"] = False
                else:
                    model["native_inference"] = True
            return models

        return await self._with_connection(discover)

    async def generate(
        self, selection: ResolvedModel, messages: list[dict], tools: list[dict] | None = None
    ) -> LLMResult:
        if selection.api_mode != "codex_native" or selection.connection or selection.credentials:
            raise _error("llm_invalid_configuration")
        tools = tools or []
        validators: dict[str, Any] = {}
        try:
            for tool in tools:
                function = tool.get("function") if isinstance(tool, dict) and tool.get("type") == "function" else None
                name = function.get("name") if isinstance(function, dict) else None
                parameters = function.get("parameters") if isinstance(function, dict) else None
                if (
                    not isinstance(name, str)
                    or not name
                    or name in validators
                    or len(name) > 128
                    or not isinstance(parameters, dict)
                ):
                    raise _error("llm_invalid_configuration")
                validators[name] = _closed_schema(parameters)
            envelope = json.dumps(
                {"messages": messages, "tools": tools}, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
        except LLMTransportError:
            raise
        except Exception:
            raise _error("llm_invalid_configuration") from None
        if len(envelope.encode()) > _INPUT_LIMIT - 4096:
            raise _error("llm_capability_unsupported", reason="input_limit")
        schema = {
            "type": "object",
            "required": ["text", "tool_calls"],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "tool_calls": {
                    "type": "array",
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "required": ["name", "arguments"],
                        "additionalProperties": False,
                        "properties": {"name": {"type": "string"}, "arguments": {"type": "string"}},
                    },
                },
            },
        }

        async def infer(connection: _Connection, home: Path, cwd: str):
            account = await connection.request("account/read", {"refreshToken": False})
            if (account.get("account") or {}).get("type") != "chatgpt":
                raise _error("llm_auth_failed")
            catalog = await self._catalog(connection)
            if selection.model_id not in {item["id"] for item in catalog}:
                raise _error("llm_model_not_found")
            self._model_metadata(home, selection.model_id, connection.version)
            safe_config = _MODERN_SAFE_CONFIG if connection.version == _MODERN_VERSION else _SAFE_CONFIG
            response = await connection.request(
                "thread/start",
                {
                    "cwd": cwd,
                    "model": selection.model_id,
                    "modelProvider": "openai",
                    "environments": [],
                    "dynamicTools": [],
                    "selectedCapabilityRoots": [],
                    "runtimeWorkspaceRoots": [],
                    "ephemeral": True,
                    "sandbox": "read-only",
                    "approvalPolicy": "never",
                    "approvalsReviewer": "user",
                    "baseInstructions": _INSTRUCTIONS,
                    "developerInstructions": "",
                    "config": safe_config,
                    "allowProviderModelFallback": False,
                    "experimentalRawEvents": False,
                },
            )
            connection.thread_id = (response.get("thread") or {}).get("id")
            sandbox = response.get("sandbox")
            permission_profile = response.get("activePermissionProfile")
            if (
                not isinstance(connection.thread_id, str)
                or not connection.thread_id
                or len(connection.thread_id) > 256
                or response.get("model") != selection.model_id
                or response.get("modelProvider") != "openai"
                or response.get("cwd") != cwd
                or response.get("approvalPolicy") != "never"
                or response.get("approvalsReviewer") != "user"
                or not isinstance(sandbox, dict)
                or sandbox.get("type") != "readOnly"
                or sandbox.get("networkAccess", False) is not False
                or response.get("runtimeWorkspaceRoots")
                or response.get("instructionSources")
                or (
                    permission_profile is not None
                    and (
                        connection.version != _MODERN_VERSION
                        or not isinstance(permission_profile, dict)
                        or set(permission_profile) - {"id", "extends"}
                        or permission_profile.get("id") != ":read-only"
                        or permission_profile.get("extends") is not None
                    )
                )
            ):
                raise _error("llm_capability_unsupported", reason="thread_policy")
            connection.pending_turn = True
            turn = await connection.request(
                "turn/start",
                {
                    "threadId": connection.thread_id,
                    "input": [{"type": "text", "text": envelope, "text_elements": []}],
                    "model": selection.model_id,
                    "environments": [],
                    "runtimeWorkspaceRoots": [],
                    "approvalPolicy": "never",
                    "outputSchema": schema,
                },
            )
            identifier = (turn.get("turn") or {}).get("id")
            if (
                not isinstance(identifier, str)
                or not identifier
                or len(identifier) > 256
                or (connection.turn_id is not None and connection.turn_id != identifier)
            ):
                raise _error(reason="identity")
            connection.turn_id = identifier
            while connection.completed is None:
                connection.notification(await connection.receive())
            finals = [
                item
                for item in connection.items.values()
                if item.get("type") == "agentMessage"
                and item.get("phase") != "commentary"
                and item.get("delivery") != "async"
            ]
            if len(finals) != 1 or not isinstance(finals[0].get("text"), str):
                raise _error(reason="final_output")
            value = _json_object(finals[0]["text"])
            if (
                not isinstance(value, dict)
                or set(value) != {"text", "tool_calls"}
                or not isinstance(value["text"], str)
                or not isinstance(value["tool_calls"], list)
                or len(value["tool_calls"]) > 16
            ):
                raise _error(reason="final_output")
            calls = []
            for call in value["tool_calls"]:
                if (
                    not isinstance(call, dict)
                    or set(call) != {"name", "arguments"}
                    or not isinstance(call["name"], str)
                    or call["name"] not in validators
                    or not isinstance(call["arguments"], str)
                ):
                    raise _error(reason="final_output")
                arguments = _json_object(call["arguments"])
                try:
                    validators[call["name"]].validate(arguments)
                except Exception:
                    raise _error(reason="final_output") from None
                calls.append(
                    {
                        "id": "ambient-native-" + uuid.uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(
                                arguments, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                            ),
                        },
                    }
                )
            return LLMResult(text=value["text"], tool_calls=calls or None, usage=connection.usage)

        return await self._with_connection(infer)
