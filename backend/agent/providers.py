import asyncio
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

import backend.llm_service
from backend.agent.errors import BudgetExhaustedError
from backend.models import LLMAuditLog

logger = logging.getLogger("agent.providers")

_AUDIT_PREVIEW_MAX_BYTES = 32 * 1024
_AUDIT_ERROR_MAX_BYTES = 8 * 1024
_TRACE_CONTEXT_KEYS = ("run_id", "session_id", "step_id", "attempt", "trace_id")
_SENSITIVE_SCHEMA_KEYS = ("sensitive", "x-sensitive", "writeOnly")
_REDACTED = "[REDACTED]"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _bounded_text(value: Any, max_bytes: int = _AUDIT_PREVIEW_MAX_BYTES) -> str:
    text = value if isinstance(value, str) else str(value or "")
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    marker = f"\n...[truncated; original_bytes={len(encoded)}]"
    prefix_limit = max(0, max_bytes - len(marker.encode("utf-8")))
    return encoded[:prefix_limit].decode("utf-8", errors="ignore") + marker


def _is_sensitive_schema(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    return any(schema.get(key) is True for key in _SENSITIVE_SCHEMA_KEYS) or schema.get("format") == "password"


def _schema_contains_sensitive(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    if _is_sensitive_schema(schema):
        return True
    properties = schema.get("properties")
    if isinstance(properties, dict) and any(_schema_contains_sensitive(item) for item in properties.values()):
        return True
    if _schema_contains_sensitive(schema.get("items")):
        return True
    return any(
        isinstance(schema.get(keyword), list)
        and any(_schema_contains_sensitive(item) for item in schema[keyword])
        for keyword in ("allOf", "anyOf", "oneOf")
    )


def _tool_audit_policies(tools: list[dict[str, Any]] | None, tool_registry: Any) -> dict[str, dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {}
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        parameters = function.get("parameters") if isinstance(function.get("parameters"), dict) else {}
        explicit = set()
        for source in (item, function, parameters):
            marked = source.get("x-sensitive-fields") if isinstance(source, dict) else None
            if isinstance(marked, (list, tuple, set, frozenset)):
                explicit.update(str(field) for field in marked)
        try:
            registered_spec = getattr(tool_registry, "specs", {}).get(name)
            if registered_spec is None and hasattr(tool_registry, "gateway"):
                registered_spec = tool_registry.gateway.spec(name)
            if registered_spec is not None:
                explicit.update(registered_spec.sensitive_fields)
        except Exception:
            pass
        policies[name] = {"schema": parameters, "sensitive_fields": frozenset(explicit)}
    return policies


def _redact_schema_value(
    value: Any,
    schema: dict[str, Any] | None,
    *,
    sensitive_fields: frozenset[str] = frozenset(),
    root: bool = True,
) -> Any:
    schema = schema if isinstance(schema, dict) else {}
    if _is_sensitive_schema(schema):
        return _REDACTED
    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        return {
            str(key): (
                _REDACTED
                if root and str(key) in sensitive_fields
                else _redact_schema_value(item, properties.get(str(key)), root=False)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        return [_redact_schema_value(item, item_schema, root=False) for item in value]
    return value


def _collect_sensitive_values(
    value: Any,
    schema: dict[str, Any] | None,
    *,
    sensitive_fields: frozenset[str] = frozenset(),
    root: bool = True,
) -> list[str]:
    schema = schema if isinstance(schema, dict) else {}
    if _is_sensitive_schema(schema):
        return [value] if isinstance(value, str) and value else []
    found: list[str] = []
    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for key, item in value.items():
            if root and str(key) in sensitive_fields:
                if isinstance(item, str) and item:
                    found.append(item)
            else:
                found.extend(_collect_sensitive_values(item, properties.get(str(key)), root=False))
    elif isinstance(value, list):
        item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
        for item in value:
            found.extend(_collect_sensitive_values(item, item_schema, root=False))
    return found


def _decoded_tool_arguments(raw: Any) -> tuple[Any, bool]:
    if isinstance(raw, str):
        try:
            return json.loads(raw), True
        except json.JSONDecodeError:
            return raw, False
    return raw, True


def _redact_tool_calls(
    tool_calls: Any,
    policies: dict[str, dict[str, Any]],
) -> tuple[Any, list[str]]:
    if not isinstance(tool_calls, list):
        return tool_calls, []
    redacted_calls: list[Any] = []
    sensitive_values: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            redacted_calls.append(call)
            continue
        sanitized_call = dict(call)
        function = dict(call.get("function") or {})
        name = function.get("name")
        policy = policies.get(name, {"schema": {}, "sensitive_fields": frozenset()})
        raw_arguments = function.get("arguments", {})
        arguments, parsed = _decoded_tool_arguments(raw_arguments)
        sensitive_fields = policy["sensitive_fields"]
        sensitive_values.extend(
            _collect_sensitive_values(
                arguments,
                policy["schema"],
                sensitive_fields=sensitive_fields,
            )
        )
        if not parsed and (sensitive_fields or _schema_contains_sensitive(policy["schema"])):
            redacted_arguments: Any = "[UNPARSABLE SENSITIVE ARGUMENTS REDACTED]"
        else:
            redacted_arguments = _redact_schema_value(
                arguments,
                policy["schema"],
                sensitive_fields=sensitive_fields,
            )
            if isinstance(raw_arguments, str):
                redacted_arguments = json.dumps(redacted_arguments, ensure_ascii=False, separators=(",", ":"))
        function["arguments"] = redacted_arguments
        sanitized_call["function"] = function
        redacted_calls.append(sanitized_call)
    return redacted_calls, sensitive_values


def _redact_message_tool_calls(value: Any, policies: dict[str, dict[str, Any]]) -> Any:
    if isinstance(value, list):
        return [_redact_message_tool_calls(item, policies) for item in value]
    if not isinstance(value, dict):
        return value
    result = {str(key): _redact_message_tool_calls(item, policies) for key, item in value.items()}
    if "tool_calls" in value:
        result["tool_calls"], _ = _redact_tool_calls(value.get("tool_calls"), policies)
    return result


def _tool_call_sensitive_values(value: Any, policies: dict[str, dict[str, Any]]) -> list[str]:
    found: list[str] = []
    if isinstance(value, list):
        for item in value:
            found.extend(_tool_call_sensitive_values(item, policies))
    elif isinstance(value, dict):
        if "tool_calls" in value:
            _, values = _redact_tool_calls(value.get("tool_calls"), policies)
            found.extend(values)
        for item in value.values():
            found.extend(_tool_call_sensitive_values(item, policies))
    return found


def _audit_preview(value: Any, policies: dict[str, dict[str, Any]], sensitive_values: list[str]) -> str:
    sanitized = _redact_message_tool_calls(value, policies)
    try:
        text = json.dumps(sanitized, ensure_ascii=False, default=str)
    except Exception:
        text = str(sanitized)
    all_sensitive_values = sensitive_values + _tool_call_sensitive_values(value, policies)
    for secret in sorted(set(all_sensitive_values), key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, _REDACTED)
    return _bounded_text(text)


def _redact_plain_text(text: str, sensitive_values: list[str], max_bytes: int = _AUDIT_PREVIEW_MAX_BYTES) -> str:
    for secret in sorted(set(sensitive_values), key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, _REDACTED)
    return _bounded_text(text, max_bytes)


def _safe_usage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        encoded = _json_bytes(value)
    except Exception:
        return {"unserializable": True}
    if len(encoded) <= _AUDIT_ERROR_MAX_BYTES:
        return json.loads(encoded.decode("utf-8"))
    return {"truncated": True, "original_bytes": len(encoded), "hash": hashlib.sha256(encoded).hexdigest()}


def _resolved_audit_context(
    audit_context: dict[str, Any] | None,
    tool_context: dict[str, Any] | None,
) -> dict[str, Any]:
    resolved = {key: (tool_context or {}).get(key) for key in _TRACE_CONTEXT_KEYS}
    for key in _TRACE_CONTEXT_KEYS:
        if audit_context and key in audit_context:
            resolved[key] = audit_context[key]
    for key in ("run_id", "session_id", "step_id", "trace_id"):
        if resolved.get(key) is not None:
            resolved[key] = _bounded_text(str(resolved[key]), 512)
    if resolved.get("attempt") is not None:
        try:
            resolved["attempt"] = int(resolved["attempt"])
        except (TypeError, ValueError):
            resolved["attempt"] = None
    if audit_context and isinstance(audit_context.get("stage"), str):
        resolved["stage"] = _bounded_text(audit_context["stage"], 128)
    raw_hashes = (audit_context or {}).get("artifact_hashes")
    if raw_hashes is None:
        raw_hashes = (tool_context or {}).get("artifact_hashes")
    resolved["artifact_hashes"] = {
        _bounded_text(str(artifact_id), 256): str(digest).lower()
        for artifact_id, digest in (raw_hashes.items() if isinstance(raw_hashes, dict) else ())
        if len(str(digest)) == 64
        and all(character in "0123456789abcdefABCDEF" for character in str(digest))
    }
    return resolved


class ToolLoopExhausted(BudgetExhaustedError):
    """A bounded tool loop stopped before it could produce a final answer."""


@dataclass(frozen=True)
class ToolLoopBudget:
    max_iterations: int = 5
    max_tool_calls: int = 12
    wall_clock_s: float = 120.0
    llm_call_timeout_s: float = 60.0
    max_assistant_output_bytes: int = 256 * 1024
    max_identical_calls: int = 2
    on_model_call: Callable[[], None] | None = field(default=None, repr=False, compare=False)
    on_usage: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False, compare=False)


class BaseLLMProvider(ABC):
    def __init__(self, model: str, provider_name: str = ""):
        self.model = model
        self.provider_name = provider_name

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        db_session: Session | None = None,
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        audit_context: dict[str, Any] | None = None,
    ) -> str:
        """
        Generates a completion response given the list of chat messages.
        Logs the query in the LLMAuditLog if a db_session is provided.
        """
        pass

    def _log_to_db(
        self,
        db_session: Session | None,
        provider: str,
        prompt: Any,
        response: str,
        stage: str = "chat",
        *,
        audit_context: dict[str, Any] | None = None,
        latency_ms: float | None = None,
        usage: dict[str, Any] | None = None,
        finish_reason: str | None = None,
        error: str | None = None,
        prompt_hash: str | None = None,
        tool_schema_hash: str | None = None,
    ) -> None:
        if db_session is None:
            return
        try:
            try:
                prompt_str = prompt if isinstance(prompt, str) else json.dumps(prompt, ensure_ascii=False, default=str)
            except Exception:
                prompt_str = str(prompt)

            audit_log = LLMAuditLog(
                provider=provider,
                model=self.model,
                prompt=_bounded_text(prompt_str),
                response=_bounded_text(response),
                stage=str((audit_context or {}).get("stage") or stage),
                run_id=(audit_context or {}).get("run_id"),
                session_id=(audit_context or {}).get("session_id"),
                step_id=(audit_context or {}).get("step_id"),
                attempt=(audit_context or {}).get("attempt"),
                trace_id=(audit_context or {}).get("trace_id"),
                latency_ms=latency_ms,
                usage=usage,
                finish_reason=finish_reason,
                error=_bounded_text(error, _AUDIT_ERROR_MAX_BYTES) if error is not None else None,
                prompt_hash=prompt_hash,
                tool_schema_hash=tool_schema_hash,
                artifact_hashes=dict((audit_context or {}).get("artifact_hashes") or {}),
            )
            db_session.add(audit_log)
            db_session.commit()
            db_session.refresh(audit_log)
        except Exception as e:
            logger.error(f"Failed to log LLM transaction: {e}")

    async def _run_tool_loop(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        db_session: Session | None = None,
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        audit_context: dict[str, Any] | None = None,
    ) -> str:
        from backend.agent.tools import ToolValidationError, registry as tool_registry

        local_messages = list(messages)
        limits = budget or ToolLoopBudget()
        if limits.max_iterations < 1 or limits.max_tool_calls < 0:
            raise ValueError("Invalid tool-loop budget")
        started = time.monotonic()
        tool_call_count = 0
        repeated_calls: dict[tuple[str, str], int] = {}
        allowed_tool_names = {
            str(item.get("function", {}).get("name"))
            for item in (tools or [])
            if item.get("function", {}).get("name")
        }
        trace_context = _resolved_audit_context(audit_context, tool_context)
        audit_policies = _tool_audit_policies(tools, tool_registry)
        tool_schema_hash = _payload_hash(tools) if tools is not None else None

        for _iteration in range(limits.max_iterations):
            remaining = limits.wall_clock_s - (time.monotonic() - started)
            if remaining <= 0:
                raise ToolLoopExhausted("Agent tool loop exceeded its wall-clock budget")
            func = backend.llm_service.call_llm_api
            import inspect

            try:
                sig = inspect.signature(func)
                accepts_tools = len(sig.parameters) >= 4 or any(
                    p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                    for p in sig.parameters.values()
                )
            except Exception:
                accepts_tools = True

            call_timeout = min(limits.llm_call_timeout_s, remaining)
            prompt_hash = _payload_hash(local_messages)
            call_started = time.monotonic()
            try:
                if limits.on_model_call is not None:
                    limits.on_model_call()
                if accepts_tools:
                    invocation = func(provider_name, self.model, local_messages, tools)
                else:
                    invocation = func(provider_name, self.model, local_messages)
                response_data = await asyncio.wait_for(invocation, timeout=call_timeout)

                if isinstance(response_data, str):
                    response_data = {"content": response_data, "tool_calls": None}
                if not isinstance(response_data, dict):
                    raise RuntimeError("LLM provider returned a non-object response")

                content = response_data.get("content", "") or ""
                if not isinstance(content, str):
                    raise RuntimeError("LLM provider returned non-text content")
                if len(content.encode("utf-8")) > limits.max_assistant_output_bytes:
                    raise ToolLoopExhausted("Assistant output exceeded the configured byte budget")
                tool_calls = response_data.get("tool_calls", None)
            except BaseException as exc:
                latency_ms = (time.monotonic() - call_started) * 1000
                known_secrets = _tool_call_sensitive_values(local_messages, audit_policies)
                error_text = _redact_plain_text(
                    f"{type(exc).__name__}: {exc}",
                    known_secrets,
                    _AUDIT_ERROR_MAX_BYTES,
                )
                self._log_to_db(
                    db_session,
                    provider_name,
                    _audit_preview(local_messages, audit_policies, known_secrets),
                    "",
                    audit_context=trace_context,
                    latency_ms=latency_ms,
                    error=error_text,
                    prompt_hash=prompt_hash,
                    tool_schema_hash=tool_schema_hash,
                )
                raise

            latency_ms = (time.monotonic() - call_started) * 1000
            redacted_tool_calls, response_secrets = _redact_tool_calls(tool_calls, audit_policies)
            historical_secrets = _tool_call_sensitive_values(local_messages, audit_policies)
            all_secrets = historical_secrets + response_secrets
            if tool_calls:
                response_preview = _audit_preview(
                    {"content": content, "tool_calls": redacted_tool_calls},
                    audit_policies,
                    all_secrets,
                )
            else:
                response_preview = _redact_plain_text(content, all_secrets)
            finish_reason = response_data.get("finish_reason") or response_data.get("stop_reason")
            if finish_reason is not None:
                finish_reason = _bounded_text(str(finish_reason), 256)

            usage = _safe_usage(response_data.get("usage"))

            # Log this specific LLM call to DB
            self._log_to_db(
                db_session,
                provider_name,
                _audit_preview(local_messages, audit_policies, all_secrets),
                response_preview,
                audit_context=trace_context,
                latency_ms=latency_ms,
                usage=usage,
                finish_reason=finish_reason,
                prompt_hash=prompt_hash,
                tool_schema_hash=tool_schema_hash,
            )
            if limits.on_usage is not None:
                limits.on_usage(usage or {})

            if not tool_calls:
                return content

            # Append assistant message with tool calls to prompt history
            assistant_msg = {"role": "assistant", "content": content or "", "tool_calls": tool_calls}
            local_messages.append(assistant_msg)

            # Execute each requested tool call
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    raise ToolValidationError("LLM returned a malformed tool call")
                tool_name = tool_call.get("function", {}).get("name")
                tool_args_raw = tool_call.get("function", {}).get("arguments", {})
                tool_id = tool_call.get("id")
                if not isinstance(tool_name, str) or not tool_name:
                    raise ToolValidationError("LLM tool call is missing a tool name")
                if tool_name not in allowed_tool_names:
                    raise ToolValidationError(f"LLM requested tool '{tool_name}' outside the allowed tool set")
                tool_call_count += 1
                if tool_call_count > limits.max_tool_calls:
                    raise ToolLoopExhausted("Agent exceeded its tool-call budget")

                # Parse arguments
                if isinstance(tool_args_raw, str):
                    try:
                        tool_args = json.loads(tool_args_raw)
                    except json.JSONDecodeError as exc:
                        raise ToolValidationError(f"Tool arguments are not valid JSON: {exc}") from exc
                else:
                    tool_args = tool_args_raw
                if not isinstance(tool_args, dict):
                    raise ToolValidationError("Tool arguments must be a JSON object")

                canonical_args = json.dumps(tool_args, ensure_ascii=False, sort_keys=True, default=str)
                call_key = (tool_name, canonical_args)
                repeated_calls[call_key] = repeated_calls.get(call_key, 0) + 1
                if repeated_calls[call_key] > limits.max_identical_calls:
                    raise ToolLoopExhausted(f"Agent repeatedly requested identical tool call '{tool_name}'")

                execution_context = dict(tool_context or {})
                execution_context.setdefault("db_session", db_session)
                result = await tool_registry.execute(tool_name, tool_args, context=execution_context)
                result_str = (
                    result
                    if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
                )

                # Append tool response message to history
                tool_msg = {"role": "tool", "tool_call_id": tool_id, "name": tool_name, "content": result_str}
                local_messages.append(tool_msg)

        raise ToolLoopExhausted(f"Agent tool loop exceeded maximum iterations ({limits.max_iterations})")


class OllamaProvider(BaseLLMProvider):
    def __init__(self, model: str, provider_name: str = "ollama"):
        super().__init__(model, provider_name)

    async def generate(
        self,
        messages: list[dict[str, str]],
        db_session: Session | None = None,
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        audit_context: dict[str, Any] | None = None,
    ) -> str:
        return await self._run_tool_loop(
            self.provider_name,
            messages,
            db_session,
            tools,
            tool_context=tool_context,
            budget=budget,
            audit_context=audit_context,
        )


class CloudLLMProvider(BaseLLMProvider):
    async def generate(
        self,
        messages: list[dict[str, str]],
        db_session: Session | None = None,
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_context: dict[str, Any] | None = None,
        budget: ToolLoopBudget | None = None,
        audit_context: dict[str, Any] | None = None,
    ) -> str:
        return await self._run_tool_loop(
            self.provider_name,
            messages,
            db_session,
            tools,
            tool_context=tool_context,
            budget=budget,
            audit_context=audit_context,
        )


def get_llm_provider(provider_name: str, model_name: str) -> BaseLLMProvider:
    if provider_name.lower() == "ollama":
        return OllamaProvider(model=model_name, provider_name=provider_name)
    return CloudLLMProvider(model=model_name, provider_name=provider_name)
