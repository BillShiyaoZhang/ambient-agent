import os
import pytest
import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from acp.schema import (
    PermissionOption,
    RequestPermissionResponse,
    AllowedOutcome,
    DeniedOutcome,
    ReadTextFileResponse,
    WriteTextFileResponse,
    CreateTerminalResponse,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    AgentMessageChunk,
    ToolCallStart,
    ToolCallProgress,
)
from acp import text_block

# Since FastAPIACPClient and run_opencode_agent_acp don't exist yet,
# we import them inside the tests or mock them, but for strict TDD we
# import them directly so that the tests fail until we write the implementation.
from backend.opencode_service import FastAPIACPClient, run_opencode_agent_acp

@pytest.mark.asyncio
async def test_client_fs_operations(tmp_path):
    # 1. Setup target workspace dir
    workspace_root = tmp_path / "app_workspace"
    workspace_root.mkdir()
    
    # Write a test file
    test_file_path = workspace_root / "index.html"
    test_file_path.write_text("hello html", encoding="utf-8")
    
    # 2. Instantiate client
    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=lambda x: None)
    
    # Test read_text_file
    read_resp = await client.read_text_file(session_id="sess", path="index.html")
    assert isinstance(read_resp, ReadTextFileResponse)
    assert read_resp.content == "hello html"
    
    # Test write_text_file
    write_resp = await client.write_text_file(session_id="sess", path="style.css", content="body {color: red;}")
    assert isinstance(write_resp, WriteTextFileResponse)
    assert (workspace_root / "style.css").exists()
    assert (workspace_root / "style.css").read_text(encoding="utf-8") == "body {color: red;}"

@pytest.mark.asyncio
async def test_client_request_permission():
    client = FastAPIACPClient(workspace_root=Path("."), on_update_callback=lambda x: None)
    
    options = [
        PermissionOption(option_id="opt-allow", name="Allow", kind="allow_always"),
        PermissionOption(option_id="opt-deny", name="Deny", kind="reject_always")
    ]
    
    resp = await client.request_permission(session_id="sess", tool_call=MagicMock(), options=options)
    assert isinstance(resp, RequestPermissionResponse)
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.option_id == "opt-allow"
    assert resp.outcome.outcome == "selected"

@pytest.mark.asyncio
async def test_client_terminal_operations(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    
    # Run a simple echo command in subprocess shell
    import sys
    cmd = "cmd.exe /c echo hello_terminal" if sys.platform == "win32" else "echo hello_terminal"
    create_resp = await client.create_terminal(session_id="sess", command=cmd)
    assert isinstance(create_resp, CreateTerminalResponse)
    terminal_id = create_resp.terminal_id
    assert terminal_id is not None
    
    # Wait for execution and read output
    await asyncio.sleep(0.5)
    
    output_resp = await client.terminal_output(session_id="sess", terminal_id=terminal_id)
    assert isinstance(output_resp, TerminalOutputResponse)
    assert "hello_terminal" in output_resp.output
    
    exit_resp = await client.wait_for_terminal_exit(session_id="sess", terminal_id=terminal_id)
    assert isinstance(exit_resp, WaitForTerminalExitResponse)
    assert exit_resp.exit_code == 0
    
    # Release terminal
    release_resp = await client.release_terminal(session_id="sess", terminal_id=terminal_id)
    assert release_resp is not None

@pytest.mark.asyncio
async def test_client_session_update_callbacks():
    callback_outputs = []
    def on_update(text):
        callback_outputs.append(text)
        
    client = FastAPIACPClient(workspace_root=Path("."), on_update_callback=on_update)
    
    # Create chunks
    msg_chunk = AgentMessageChunk(
        session_update="agent_message_chunk",
        content=text_block("hello world"),
        message_id="msg-1"
    )
    
    await client.session_update(session_id="sess", update=msg_chunk)
    assert len(callback_outputs) == 1
    assert callback_outputs[0] == "hello world"
    
    tool_chunk = ToolCallStart(
        session_update="tool_call",
        tool_call_id="tc-1",
        title="write_text_file",
        kind="edit",
        status="pending"
    )
    await client.session_update(session_id="sess", update=tool_chunk)
    assert len(callback_outputs) == 2
    assert "write_text_file" in callback_outputs[1]

@pytest.mark.asyncio
async def test_run_opencode_agent_acp(monkeypatch, tmp_path):
    # Mock spawn_agent_process
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))
    mock_conn.prompt = AsyncMock(return_value=PromptResponse(stop_reason="end_turn"))
    
    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        # Trigger on_connect to wire connection
        to_client.on_connect(mock_conn)
        
        # Simulate some agent updates to test streaming
        msg_chunk = AgentMessageChunk(
            session_update="agent_message_chunk",
            content=text_block("OpenCode response text"),
            message_id="msg-1"
        )
        await to_client.session_update(session_id="sess-xyz", update=msg_chunk)
        
        yield mock_conn, AsyncMock()
        
    monkeypatch.setattr("backend.opencode_service.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))
    
    updates = []
    def on_update(text):
        updates.append(text)
        
    result_log = await run_opencode_agent_acp(app_id="weather-card", instruction="Make it glassmorphic", on_update=on_update)
    
    assert "OpenCode response text" in result_log
    assert len(updates) > 0
    assert "OpenCode response text" in updates[-1]
    
    # Assert handshake & prompt called
    mock_conn.initialize.assert_called_once()
    mock_conn.new_session.assert_called_once()
    mock_conn.prompt.assert_called_once()

@pytest.mark.asyncio
async def test_run_opencode_agent_acp_timeout(monkeypatch, tmp_path):
    # Mock spawn_agent_process
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))
    
    async def hanging_prompt(*args, **kwargs):
        await asyncio.sleep(5.0)
        return PromptResponse(stop_reason="end_turn")
    mock_conn.prompt = hanging_prompt
    
    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, AsyncMock()
        
    monkeypatch.setattr("backend.opencode_service.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))
    
    monkeypatch.setenv("OPENCODE_TIMEOUT", "180.0")
    
    # Mock asyncio.wait_for to raise TimeoutError when timeout is 180.0
    original_wait_for = asyncio.wait_for
    async def mock_wait_for(fut, timeout, **kwargs):
        if timeout == 180.0:
            raise asyncio.TimeoutError()
        return await original_wait_for(fut, timeout, **kwargs)
        
    monkeypatch.setattr("asyncio.wait_for", mock_wait_for)
    
    result_log = await run_opencode_agent_acp(app_id="weather-card", instruction="Make it glassmorphic", on_update=lambda x: None)
    assert "timed out after 3 minutes" in result_log


@pytest.mark.asyncio
async def test_client_directory_traversal(tmp_path):
    # Setup target workspace dir
    workspace_root = tmp_path / "app_workspace"
    workspace_root.mkdir()
    
    # 2. Instantiate client
    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=lambda x: None)
    
    # Test read_text_file outside workspace
    from acp.exceptions import RequestError
    with pytest.raises(RequestError) as excinfo:
        await client.read_text_file(session_id="sess", path="../conftest.py")
    assert "Directory traversal attempt blocked" in getattr(excinfo.value, "data", "")
    
    # Test write_text_file outside workspace
    with pytest.raises(RequestError) as excinfo:
        await client.write_text_file(session_id="sess", path="../unsafe.txt", content="unsafe")
    assert "Directory traversal attempt blocked" in getattr(excinfo.value, "data", "")


def test_permission_policy_manager(tmp_path):
    from backend.opencode_service import PermissionPolicyManager
    
    # Write custom configuration file
    config_file = tmp_path / "custom_policy.json"
    import json
    policy_data = {
      "policy_mode": "interactive",
      "files": {
        "allowed_extensions": [".html", ".css", ".js"],
        "allowed_filenames": ["data.json"]
      },
      "commands": {
        "allowed_commands": ["npm test", "npm run build"],
        "allowed_prefixes": ["npm install "],
        "blocklist": ["rm -rf", "curl"]
      }
    }
    config_file.write_text(json.dumps(policy_data), encoding="utf-8")
    
    mgr = PermissionPolicyManager(config_path=str(config_file))
    
    # Test file paths
    assert mgr.validate_file_path("index.html", tmp_path) is True
    assert mgr.validate_file_path("data.json", tmp_path) is True
    assert mgr.validate_file_path("unsafe.exe", tmp_path) is False
    assert mgr.validate_file_path("../conftest.py", tmp_path) is False  # Directory traversal blocked
    
    # Test commands
    assert mgr.validate_command("npm run build") is True
    assert mgr.validate_command("npm install lodash") is True
    assert mgr.validate_command("rm -rf /") is False
    assert mgr.validate_command("curl http://unsafe.site") is False
    assert mgr.validate_command("python script.py") is False  # Not whitelisted


@pytest.mark.asyncio
async def test_interactive_permission_flow(tmp_path, monkeypatch):
    # Mock PermissionPolicyManager to deny execution by default
    from backend.opencode_service import PermissionPolicyManager
    original_validate = PermissionPolicyManager.validate_command
    monkeypatch.setattr(PermissionPolicyManager, "validate_command", lambda self, cmd: False)
    
    workspace_root = tmp_path / "app_workspace"
    workspace_root.mkdir()
    
    callback_payloads = []
    async def on_update(payload):
        if isinstance(payload, dict):
            callback_payloads.append(payload)
            
    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=on_update)
    
    # Mock a tool call for create_terminal
    class MockToolCall:
        kind = "execute"
        raw_input = {"command": "npm install lodash", "args": []}
        title = "run command"
        content = None
        
    options = [
        PermissionOption(option_id="opt-allow", name="Allow", kind="allow_always")
    ]
    
    # Trigger permission request task
    req_task = asyncio.create_task(client.request_permission(session_id="sess", tool_call=MockToolCall(), options=options))
    
    # Allow task to run and emit the websocket request
    await asyncio.sleep(0.1)
    
    assert len(callback_payloads) == 1
    req_payload = callback_payloads[0]
    assert req_payload["type"] == "permission_request"
    assert req_payload["tool_call"] == "execute"
    assert "npm install lodash" in req_payload["details"]
    
    # Simulate user approval from frontend websocket
    request_id = req_payload["request_id"]
    client.resolve_permission(request_id, approved=True)
    
    resp = await req_task
    assert isinstance(resp, RequestPermissionResponse)
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.option_id == "opt-allow"
    
    # Restore mock
    monkeypatch.setattr(PermissionPolicyManager, "validate_command", original_validate)
