
import pytest

from backend.agent.providers import OllamaProvider
from backend.agent.tools import registry as global_registry
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
                        "function": {
                            "name": "test_delete_app",
                            "arguments": '{"app_id": "test-widget"}'
                        }
                    }
                ]
            }
        else:
            return {
                "content": "Successfully deleted the app.",
                "tool_calls": None
            }

    monkeypatch.setattr("backend.llm_service.call_llm_api", mock_call_llm_api)

    # 3. Instantiate OllamaProvider and run generate
    provider = OllamaProvider(model="test-model")
    messages = [{"role": "user", "content": "Delete test-widget"}]

    # We pass the tools schema list
    tools_schema = global_registry.get_tool_schemas()

    response = await provider.generate(
        messages=messages,
        db_session=test_session,
        tools=tools_schema
    )

    # 4. Verify tool was executed
    assert tool_executed is True
    assert received_app_id == "test-widget"
    assert response == "Successfully deleted the app."

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
    tool_req_log = next((l for l in logs if "test_delete_app" in l.response or "call_1" in l.response or "I need to delete the app." in l.response), None)
    final_resp_log = next((l for l in logs if l.response == "Successfully deleted the app."), None)
    assert tool_req_log is not None
    assert final_resp_log is not None

    # Clean up registry to avoid polluting other tests
    global_registry.tools.pop("test_delete_app", None)
    global_registry.schemas.pop("test_delete_app", None)
