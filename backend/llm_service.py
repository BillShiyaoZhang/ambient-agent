"""Unified LiteLLM-backed model transport for Ambient Agent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.llm_config import LLMConfigStore, LLMConfigError, ModelSelection, ResolvedModel
from backend.models import LLMAuditLog


SYSTEM_PROMPT = """You are Ambient Agent, an agentic personal coding and productivity assistant.
Respond helpfully and use only the supplied tools when needed. Interactive Apps are produced only by
the isolated Widget workflow; never emit executable App source or inline UI artifacts in conversation."""


class LLMResult(BaseModel):
    text: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class LLMTransportError(LLMConfigError):
    code = "llm_provider_error"


_default_store: LLMConfigStore | None = None


def set_default_llm_store(store: LLMConfigStore) -> None:
    global _default_store
    _default_store = store


def get_default_llm_store() -> LLMConfigStore:
    if _default_store is None:
        raise LLMConfigError("LLM configuration store is not initialized")
    return _default_store


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if hasattr(value, "dict"):
        return _plain(value.dict())
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    return str(value)


def _normalize_response(response: Any) -> LLMResult:
    data = _plain(response)
    if not isinstance(data, dict):
        return LLMResult(text=str(data or ""))
    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or choices[0].get("delta") or {}
        return LLMResult(
            text=message.get("content") or "",
            tool_calls=message.get("tool_calls") or None,
            usage=data.get("usage") or {},
        )
    output_text = data.get("output_text") or ""
    tool_calls: list[dict[str, Any]] = []
    text_parts: list[str] = [output_text] if output_text else []
    for item in data.get("output") or []:
        item_type = item.get("type")
        if item_type in {"function_call", "tool_call"}:
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {"name": item.get("name"), "arguments": item.get("arguments", "{}")},
                }
            )
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                text_parts.append(content["text"])
    return LLMResult(text="".join(text_parts), tool_calls=tool_calls or None, usage=data.get("usage") or {})


def _transport_error(exc: Exception) -> LLMTransportError:
    name = type(exc).__name__.lower()
    if "authentication" in name or "permission" in name:
        return LLMTransportError("Provider authentication failed", code="llm_auth_failed")
    if "ratelimit" in name or "rate_limit" in name:
        return LLMTransportError("Provider rate limit exceeded", code="llm_rate_limited")
    if "timeout" in name:
        return LLMTransportError("Provider request timed out", code="llm_timeout")
    if "notfound" in name or "not_found" in name:
        return LLMTransportError("Provider model was not found", code="llm_model_not_found")
    if "badrequest" in name and "tool" in str(exc).lower():
        return LLMTransportError("This model does not support agent tool calls", code="llm_capability_unsupported")
    return LLMTransportError("The LLM provider request failed", code="llm_provider_error")


class LLMService:
    def __init__(
        self,
        store: LLMConfigStore,
        completion_fn: Callable[..., Awaitable[Any]] | None = None,
        responses_fn: Callable[..., Awaitable[Any]] | None = None,
    ):
        self.store = store
        self._completion_fn = completion_fn
        self._responses_fn = responses_fn

    async def generate(
        self,
        selection: ModelSelection | ResolvedModel,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResult:
        resolved = selection if isinstance(selection, ResolvedModel) else self.store.resolve(selection)
        kwargs = self._request_kwargs(resolved, messages, tools)
        try:
            if resolved.api_mode == "responses":
                fn = self._responses_fn
                if fn is None:
                    import litellm

                    fn = litellm.aresponses
                response = await fn(**kwargs)
            else:
                fn = self._completion_fn
                if fn is None:
                    import litellm

                    fn = litellm.acompletion
                response = await fn(**kwargs)
            return _normalize_response(response)
        except LLMConfigError:
            raise
        except Exception as exc:
            raise _transport_error(exc) from exc

    @staticmethod
    def _request_kwargs(
        resolved: ResolvedModel, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": resolved.litellm_model}
        if resolved.api_mode == "responses":
            kwargs["input"] = messages
        else:
            kwargs["messages"] = messages
        if tools:
            if resolved.api_mode == "responses":
                kwargs["tools"] = [
                    {"type": "function", **item["function"]}
                    if item.get("type") == "function" and isinstance(item.get("function"), dict)
                    else item
                    for item in tools
                ]
            else:
                kwargs["tools"] = tools
        connection = resolved.connection
        if connection.get("base_url"):
            kwargs["api_base"] = connection["base_url"]
        for key in ("api_version", "region", "project", "profile", "timeout", "max_retries"):
            if connection.get(key) is not None:
                kwargs[key] = connection[key]
        if connection.get("headers"):
            kwargs["extra_headers"] = connection["headers"]
        if connection.get("query_parameters"):
            kwargs["extra_query"] = connection["query_parameters"]
        if resolved.credentials.get("secret_headers"):
            try:
                secret_headers = json.loads(resolved.credentials["secret_headers"])
                if isinstance(secret_headers, dict):
                    kwargs["extra_headers"] = {**(kwargs.get("extra_headers") or {}), **secret_headers}
            except (TypeError, json.JSONDecodeError):
                raise LLMConfigError("Secret headers must be a JSON object", code="llm_invalid_configuration")
        if resolved.credentials.get("api_key"):
            kwargs["api_key"] = resolved.credentials["api_key"]
        for key in (
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_session_token",
            "service_account_json",
        ):
            if resolved.credentials.get(key):
                kwargs[key] = resolved.credentials[key]
        return kwargs


async def call_llm_api(
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = await LLMService(get_default_llm_store()).generate(
        ModelSelection(provider_id=provider, model_id=model), messages, tools
    )
    return {"content": result.text, "tool_calls": result.tool_calls, "usage": result.usage}


async def generate_agent_response(
    messages: list[dict[str, Any]] | None = None,
    provider: str = "",
    model: str = "",
    session: Session = None,
    user_message: str | None = None,
) -> str:
    if messages is None:
        messages = (
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_message}]
            if user_message is not None
            else []
        )
    response_data = await call_llm_api(provider, model, messages)
    response_text = response_data if isinstance(response_data, str) else response_data.get("content", "")
    prompt_str = json.dumps(messages, ensure_ascii=False, default=str)
    audit_log = LLMAuditLog(provider=provider, model=model, prompt=prompt_str, response=response_text)
    session.add(audit_log)
    session.commit()
    session.refresh(audit_log)
    return response_text
