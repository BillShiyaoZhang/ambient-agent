import asyncio
import pytest
from backend.backend_manager import BackendManager, StdioJsonRpcClient

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))
    return workspace

@pytest.mark.asyncio
async def test_permission_management(tmp_workspace):
    manager = BackendManager()
    app_id = "test-app"
    command = ["python"]
    args = ["-m", "echo"]

    # Initially not approved
    assert not manager.is_mcp_approved(app_id, command, args)

    # Approve and check
    manager.approve_mcp(app_id, command, args)
    assert manager.is_mcp_approved(app_id, command, args)

    # Persistence check
    manager2 = BackendManager()
    assert manager2.is_mcp_approved(app_id, command, args)

    # Agent permission check
    url = "http://localhost:8080/agent"
    assert not manager.is_agent_approved(app_id, url)
    manager.approve_agent(app_id, url)
    assert manager.is_agent_approved(app_id, url)

    manager3 = BackendManager()
    assert manager3.is_agent_approved(app_id, url)

@pytest.mark.asyncio
async def test_permission_resolution(tmp_workspace):
    manager = BackendManager()
    app_id = "test-app"
    sent_msgs = []

    async def mock_send(msg):
        sent_msgs.append(msg)
        # Simulate approval response in a background task
        asyncio.create_task(asyncio.sleep(0.01)).add_done_callback(
            lambda _: manager.resolve_permission(msg["request_id"], True)
        )

    approved = await manager.request_permission(app_id, "mcp_spawn", "some-cmd", mock_send)
    assert approved
    assert len(sent_msgs) == 1
    assert sent_msgs[0]["type"] == "backend_permission_request"
    assert sent_msgs[0]["permission_type"] == "mcp_spawn"
    assert sent_msgs[0]["value"] == "some-cmd"

@pytest.mark.asyncio
async def test_stdio_jsonrpc_client(tmp_path):
    # Create a simple mock MCP server python script that replies to JSON-RPC over stdin/stdout
    server_script = tmp_path / "mock_mcp.py"
    server_script.write_text("""
import sys
import json

for line in sys.stdin:
    try:
        req = json.loads(line.strip())
        res = {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "result": {"echo": req.get("params", {})}
        }
        print(json.dumps(res))
        sys.stdout.flush()
    except Exception as e:
        print(json.dumps({"error": {"message": str(e)}}))
        sys.stdout.flush()
""", encoding="utf-8")

    client = StdioJsonRpcClient(["python", str(server_script)], [])
    await client.start()

    try:
        res = await client.call("echo_method", {"key": "value"})
        assert res == {"echo": {"key": "value"}}
    finally:
        await client.stop()
