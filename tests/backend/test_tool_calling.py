import pytest

from backend.agent.providers import OllamaProvider, ToolLoopBudget, ToolLoopExhausted
from backend.agent.tools import (
    ApprovalPolicy,
    ToolEffect,
    ToolPolicyError,
    ToolRegistry,
    registry as global_registry,
)
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture(name="test_session")
def test_session_fixture(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)
    yield storage


@pytest.mark.asyncio
async def test_tool_calling_loop(test_session, monkeypatch):
    # 1. Register a test tool in the global registry
    tool_executed = False
    received_app_id = None

    @global_registry.register
    def test_delete_app(app_id: str) -> str:
        """
        Delete a mock app.
        :param app_id: The ID of the app.
        """
        nonlocal tool_executed, received_app_id
        tool_executed = True
        received_app_id = app_id
        return "success_delete"

    # 2. Mock call_llm_api to simulate a 2-step tool loop:
    mock_calls = []

    async def mock_call_llm_api(provider, model, messages, tools=None):
        mock_calls.append(list(messages))
        if len(mock_calls) == 1:
            return {
                "content": "I need to delete the app.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "test_delete_app", "arguments": '{"app_id": "test-widget"}'},
                    }
                ],
            }
        else:
            return {"content": "Successfully deleted the app.", "tool_calls": None}

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)

    # 3. Instantiate OllamaProvider and run generate
    provider = OllamaProvider(model="test-model")
    messages = [{"role": "user", "content": "Delete test-widget"}]
    tool_events = []

    # We pass the tools schema list
    tools_schema = global_registry.get_tool_schemas()

    response = await provider.generate(
        messages=messages,
        db_session=test_session,
        tools=tools_schema,
        tool_context={
            "run_id": "run-tool",
            "step_id": "converse",
            "attempt": 2,
            "trace_id": "trace-tool",
            "on_event": tool_events.append,
        },
    )

    # 4. Verify tool was executed
    assert tool_executed is True
    assert received_app_id == "test-widget"
    assert response == "Successfully deleted the app."
    assert [event["type"] for event in tool_events] == ["tool_started", "tool_succeeded"]
    assert all(event["run_id"] == "run-tool" for event in tool_events)
    assert all(event["step_id"] == "converse" for event in tool_events)
    assert all(event["attempt"] == 2 for event in tool_events)
    assert all(event["trace_id"] == "trace-tool" for event in tool_events)
    assert tool_events[-1]["duration_ms"] >= 0

    # 5. Verify the messages history was correctly built in the loop:
    assert len(mock_calls[0]) == 1
    assert mock_calls[0][0]["content"] == "Delete test-widget"

    assert len(mock_calls[1]) == 3
    assert mock_calls[1][0]["role"] == "user"
    assert mock_calls[1][1]["role"] == "assistant"
    assert mock_calls[1][1]["tool_calls"][0]["function"]["name"] == "test_delete_app"
    assert mock_calls[1][2]["role"] == "tool"
    assert mock_calls[1][2]["name"] == "test_delete_app"
    assert mock_calls[1][2]["content"] == "success_delete"

    # 6. Verify audit logging
    logs = test_session.get_audit_logs()
    assert len(logs) == 2
    tool_req_log = next(
        (
            l
            for l in logs
            if "test_delete_app" in l.response or "call_1" in l.response or "I need to delete the app." in l.response
        ),
        None,
    )
    final_resp_log = next((l for l in logs if l.response == "Successfully deleted the app."), None)
    assert tool_req_log is not None
    assert final_resp_log is not None

    # Clean up registry to avoid polluting other tests
    global_registry.unregister("test_delete_app")


@pytest.mark.asyncio
async def test_tool_loop_exhaustion_is_typed_budget_failure(test_session, monkeypatch):
    model_calls = 0

    @global_registry.register
    def repeat_read(value: str) -> str:
        """Return a value without side effects."""

        return value

    async def endless_tool_call(_provider, _model, _messages, _tools=None):
        return {
            "content": "again",
            "tool_calls": [
                {
                    "id": "repeat",
                    "type": "function",
                    "function": {"name": "repeat_read", "arguments": '{"value":"x"}'},
                }
            ],
        }

    def count_model_call() -> None:
        nonlocal model_calls
        model_calls += 1

    monkeypatch.setattr("backend.llm_service.call_llm_api", endless_tool_call)
    provider = OllamaProvider(model="test-model")
    try:
        with pytest.raises(ToolLoopExhausted) as exc_info:
            await provider.generate(
                messages=[{"role": "user", "content": "loop"}],
                db_session=test_session,
                tools=global_registry.get_tool_schemas(),
                budget=ToolLoopBudget(max_iterations=1, on_model_call=count_model_call),
            )
    finally:
        global_registry.unregister("repeat_read")

    assert exc_info.value.code == "budget_exhausted"
    assert model_calls == 1


@pytest.mark.asyncio
async def test_effectful_tool_idempotency_replays_same_args_and_rejects_key_reuse():
    registry = ToolRegistry()
    calls = 0

    @registry.register(
        effect=ToolEffect.WRITE,
        scopes={"workspace:write"},
        approval_policy=ApprovalPolicy.ALWAYS,
        requires_idempotency=True,
    )
    async def write_value(value: str, idempotency_key: str | None = None) -> str:
        nonlocal calls
        calls += 1
        return f"{idempotency_key}:{value}"

    context = {
        "scopes": {"workspace:write"},
        "approved_effects": {ToolEffect.WRITE},
        "idempotency_key": "effect-1",
    }
    assert await registry.execute("write_value", {"value": "a"}, context) == "effect-1:a"
    assert await registry.execute("write_value", {"value": "a"}, context) == "effect-1:a"
    assert calls == 1

    with pytest.raises(ToolPolicyError, match="different arguments"):
        await registry.execute("write_value", {"value": "b"}, context)
    assert calls == 1
