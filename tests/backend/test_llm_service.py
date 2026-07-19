import importlib
from types import SimpleNamespace

import pytest

from backend.llm_config import LLMConfigStore, ModelSelection
from backend.llm_runtime import fast_selection, primary_selection, use_model_selections
from backend.llm_service import LLMService, LLMTransportError


def test_litellm_tool_call_runtime_dependencies_are_installed():
    """LiteLLM imports its MCP/tool path only after a request includes tools."""
    importlib.import_module("litellm.responses.mcp.chat_completions_handler")


def configured_store(tmp_path, *, api_mode="chat_completions"):
    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {
            "id": "main",
            "name": "Main",
            "preset": "openai_responses" if api_mode == "responses" else "openai",
            "models": [{"id": "model-a", "api_mode": api_mode}],
            "credential_refs": {"api_key": {"source": "stored"}},
        },
        {"api_key": {"source": "stored", "value": "top-secret"}},
    )
    return store


@pytest.mark.asyncio
async def test_chat_completion_is_normalized_with_tool_calls_and_usage(tmp_path):
    captured = {}

    async def completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=[{"id": "call-1"}]))],
            usage={"total_tokens": 12},
        )

    result = await LLMService(configured_store(tmp_path), completion_fn=completion).generate(
        ModelSelection(provider_id="main", model_id="model-a"),
        [{"role": "user", "content": "hello"}],
        [{"type": "function", "function": {"name": "lookup"}}],
    )

    assert result.text == "done"
    assert result.tool_calls == [{"id": "call-1"}]
    assert result.usage == {"total_tokens": 12}
    assert captured["model"] == "openai/model-a"
    assert captured["api_key"] == "top-secret"
    assert "messages" in captured and "tools" in captured


@pytest.mark.asyncio
async def test_responses_output_is_normalized_to_same_internal_shape(tmp_path):
    captured = {}

    async def responses(**kwargs):
        captured.update(kwargs)
        return {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ready"}]},
                {"type": "function_call", "call_id": "call-2", "name": "write", "arguments": '{"x":1}'},
            ],
            "usage": {"input_tokens": 3},
        }

    result = await LLMService(configured_store(tmp_path, api_mode="responses"), responses_fn=responses).generate(
        ModelSelection(provider_id="main", model_id="model-a"),
        [{"role": "user", "content": "hello"}],
        [{"type": "function", "function": {"name": "write", "description": "Write", "parameters": {"type": "object"}}}],
    )

    assert result.text == "ready"
    assert result.tool_calls == [
        {
            "id": "call-2",
            "type": "function",
            "function": {"name": "write", "arguments": '{"x":1}'},
        }
    ]
    assert "input" in captured and "messages" not in captured
    assert captured["tools"] == [
        {
            "type": "function",
            "name": "write",
            "description": "Write",
            "parameters": {"type": "object"},
        }
    ]


@pytest.mark.asyncio
async def test_transport_errors_are_structured_and_do_not_leak_provider_message(tmp_path):
    class AuthenticationError(Exception):
        pass

    async def completion(**_kwargs):
        raise AuthenticationError("top-secret was rejected by https://private.internal")

    with pytest.raises(LLMTransportError) as caught:
        await LLMService(configured_store(tmp_path), completion_fn=completion).generate(
            ModelSelection(provider_id="main", model_id="model-a"), [{"role": "user", "content": "hello"}]
        )

    assert caught.value.code == "llm_auth_failed"
    assert "top-secret" not in str(caught.value)
    assert "private.internal" not in str(caught.value)


def test_model_snapshot_context_is_nested_and_task_local():
    primary = ModelSelection(provider_id="one", model_id="large")
    fast = ModelSelection(provider_id="one", model_id="small")
    assert primary_selection() is None
    with use_model_selections(primary, fast):
        assert primary_selection() == primary
        assert fast_selection() == fast
        with use_model_selections(ModelSelection(provider_id="two", model_id="other")):
            assert primary_selection().provider_id == "two"
        assert primary_selection() == primary
    assert primary_selection() is None
