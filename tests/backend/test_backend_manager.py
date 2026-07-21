import asyncio
import json
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

from backend.app_manifest import AppManifest
from backend.backend_manager import (
    BackendManager,
    MCP_PROTOCOL_VERSION,
    MCPProtocolError,
    MCPTransportError,
    StdioJsonRpcClient,
)


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
async def test_unapproved_mcp_spawn_fails_closed(tmp_workspace):
    manager = BackendManager()
    manifest = _mcp_manifest(["mcp-server"])
    sent_msgs = []

    async def mock_send(msg):
        sent_msgs.append(msg)

    with pytest.raises(PermissionError, match="durably approved"):
        await manager.get_or_start_mcp_client(manifest.id, manifest, mock_send)

    # Approval interactions are created and resumed by RunCoordinator/RunStore;
    # BackendManager must never fall back to a process-local Future.
    assert sent_msgs == []
    assert manifest.id not in manager.mcp_clients


@pytest.mark.asyncio
async def test_stdio_jsonrpc_client(tmp_path):
    # The mock enforces the MCP handshake order before accepting tools/call.
    server_script = tmp_path / "mock_mcp.py"
    server_script.write_text(
        """
import sys
import json

initialized = False
for line in sys.stdin:
    try:
        req = json.loads(line.strip())
        method = req.get("method")
        if method == "initialize":
            result = {"protocolVersion": req["params"]["protocolVersion"], "capabilities": {"tools": {}}}
        elif method == "notifications/initialized":
            initialized = True
            continue
        elif method == "tools/call":
            result = {"initialized": initialized}
        else:
            result = {"echo": req.get("params", {})}
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
    except Exception as e:
        print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"), "error": {"message": str(e)}}), flush=True)
""",
        encoding="utf-8",
    )

    client = StdioJsonRpcClient([sys.executable, str(server_script)], [])
    await client.start()

    try:
        assert client.protocol_version == MCP_PROTOCOL_VERSION
        assert client.server_capabilities == {"tools": {}}
        res = await client.call("echo_method", {"key": "value"})
        assert res == {"echo": {"key": "value"}}
        assert await client.call("tools/call", {"name": "echo", "arguments": {}}) == {"initialized": True}
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_capability_negotiation_rejects_unadvertised_method_before_write(tmp_path):
    server_script = tmp_path / "capability_mcp.py"
    method_log = tmp_path / "capability_methods.log"
    server_script.write_text(
        """
import json
import sys

log_path = sys.argv[1]
for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method", "")
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(method + "\\n")
    if method == "initialize":
        result = {
            "protocolVersion": req["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
        }
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
    elif method == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"tools": []}}), flush=True)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient([sys.executable, str(server_script)], [str(method_log)])
    await client.start()
    try:
        assert await client.call("tools/list", {}) == {"tools": []}
        with pytest.raises(MCPProtocolError, match=r"resources.*capability"):
            await client.call("resources/read", {"uri": "doc://one"})
        with pytest.raises(MCPProtocolError, match=r"prompts.*capability"):
            await client.call("prompts/list", {})

        methods = method_log.read_text(encoding="utf-8").splitlines()
        assert methods == ["initialize", "notifications/initialized", "tools/list"]
        assert not client.pending_requests
    finally:
        await client.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initialize_result", "message"),
    [
        ({"capabilities": {}}, "protocolVersion"),
        ({"protocolVersion": "unsupported", "capabilities": {}}, "Unsupported MCP protocolVersion"),
        ({"protocolVersion": MCP_PROTOCOL_VERSION}, "capabilities"),
        ({"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": []}, "capabilities"),
        ({"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {"tools": True}}, "tools.*capability"),
    ],
)
async def test_initialize_rejects_invalid_protocol_or_capabilities(tmp_path, initialize_result, message):
    server_script = tmp_path / "invalid_initialize_mcp.py"
    server_script.write_text(
        """
import json
import sys

result = json.loads(sys.argv[1])
for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient(
        [sys.executable, str(server_script)],
        [json.dumps(initialize_result)],
    )

    with pytest.raises(MCPProtocolError, match=message):
        await client.start()
    assert client.protocol_version is None
    assert client.server_capabilities == {}
    assert not client.is_healthy


@pytest.mark.asyncio
async def test_resource_subscription_requires_subscribe_capability(tmp_path):
    server_script = tmp_path / "resource_capability_mcp.py"
    server_script.write_text(
        """
import json
import sys

for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        result = {
            "protocolVersion": req["params"]["protocolVersion"],
            "capabilities": {"resources": {}},
        }
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient([sys.executable, str(server_script)], [])
    await client.start()
    try:
        with pytest.raises(MCPProtocolError, match=r"resources\.subscribe"):
            await client.call("resources/subscribe", {"uri": "doc://one"})
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_server_ping_is_answered_and_unknown_client_method_is_rejected(tmp_path):
    server_script = tmp_path / "server_requests_mcp.py"
    response_log = tmp_path / "server_responses.log"
    server_script.write_text(
        """
import json
import sys

log_path = sys.argv[1]
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        result = {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {}}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
    elif method == "notifications/initialized":
        print(json.dumps({"jsonrpc": "2.0", "id": "server-ping", "method": "ping", "params": {}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": "server-unknown", "method": "sampling/createMessage", "params": {}}), flush=True)
    elif message.get("id") in {"server-ping", "server-unknown"}:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(json.dumps(message, sort_keys=True) + "\\n")
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient([sys.executable, str(server_script)], [str(response_log)])
    await client.start()
    try:
        for _ in range(50):
            if response_log.exists() and len(response_log.read_text(encoding="utf-8").splitlines()) == 2:
                break
            await asyncio.sleep(0.01)
        responses = [json.loads(line) for line in response_log.read_text(encoding="utf-8").splitlines()]
        by_id = {response["id"]: response for response in responses}
        assert by_id["server-ping"]["result"] == {}
        assert by_id["server-unknown"]["error"]["code"] == -32601
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_call_deadline_and_caller_cancellation_notify_server(tmp_path):
    server_script = tmp_path / "timeout_mcp.py"
    method_log = tmp_path / "methods.log"
    server_script.write_text(
        """
import json
import sys

log_path = sys.argv[1]
for line in sys.stdin:
    req = json.loads(line)
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(req.get("method", "") + "\\n")
        log.flush()
    if req.get("method") == "initialize":
        result = {"protocolVersion": req["params"]["protocolVersion"], "capabilities": {"tools": {}, "resources": {}}}
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient([sys.executable, str(server_script)], [str(method_log)])
    await client.start()
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            await client.call("tools/call", {"name": "hang"}, timeout_seconds=0.05)
        for _ in range(20):
            methods = method_log.read_text(encoding="utf-8").splitlines()
            if "notifications/cancelled" in methods:
                break
            await asyncio.sleep(0.01)
        assert methods[:3] == ["initialize", "notifications/initialized", "tools/call"]
        assert "notifications/cancelled" in methods
        assert not client.pending_requests

        cancelled_call = asyncio.create_task(client.call("resources/read", {"uri": "slow"}))
        for _ in range(20):
            methods = method_log.read_text(encoding="utf-8").splitlines()
            if "resources/read" in methods:
                break
            await asyncio.sleep(0.01)
        cancelled_call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_call
        for _ in range(20):
            methods = method_log.read_text(encoding="utf-8").splitlines()
            if methods.count("notifications/cancelled") == 2:
                break
            await asyncio.sleep(0.01)
        assert methods.count("notifications/cancelled") == 2
        assert not client.pending_requests
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_eof_rejects_every_pending_request(tmp_path):
    server_script = tmp_path / "eof_mcp.py"
    server_script.write_text(
        """
import json
import sys

for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        result = {"protocolVersion": req["params"]["protocolVersion"], "capabilities": {"tools": {}, "resources": {}}}
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
    elif req.get("id") is not None:
        sys.exit(0)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient([sys.executable, str(server_script)], [])
    await client.start()
    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                client.call("tools/call", {"name": "first"}),
                client.call("resources/read", {"uri": "second"}),
                return_exceptions=True,
            ),
            timeout=1,
        )
        assert all(isinstance(outcome, MCPTransportError) for outcome in outcomes)
        assert not client.pending_requests
    finally:
        await client.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_response", ["not-json", '{"jsonrpc":"2.0","id":2,"result":"' + "x" * 512 + '"}'])
async def test_invalid_or_oversized_response_fails_closed(tmp_path, bad_response):
    server_script = tmp_path / "bad_response_mcp.py"
    server_script.write_text(
        """
import json
import sys

bad_response = sys.argv[1]
for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        result = {"protocolVersion": req["params"]["protocolVersion"], "capabilities": {"tools": {}, "resources": {}}}
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
    elif req.get("id") is not None:
        print(bad_response, flush=True)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient(
        [sys.executable, str(server_script)],
        [bad_response],
        max_response_bytes=256,
    )
    await client.start()
    try:
        outcomes = await asyncio.gather(
            client.call("tools/call", {"name": "bad"}),
            client.call("resources/read", {"uri": "also-pending"}),
            return_exceptions=True,
        )
        assert all(isinstance(outcome, MCPProtocolError) for outcome in outcomes)
        assert not client.pending_requests
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_subprocess_environment_is_allowlisted_and_stderr_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_TEST_SECRET", "must-not-leak")
    server_script = tmp_path / "environment_mcp.py"
    server_script.write_text(
        """
import json
import os
import sys

sys.stderr.write("x" * 1024)
sys.stderr.flush()
for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        result = {"protocolVersion": req["params"]["protocolVersion"], "capabilities": {}}
    elif req.get("method") == "environment":
        result = {
            "secret": os.getenv("AMBIENT_TEST_SECRET"),
            "explicit": os.getenv("EXPLICIT_MCP_VALUE"),
            "has_path": bool(os.getenv("PATH")),
        }
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient(
        [sys.executable, str(server_script)],
        [],
        {"EXPLICIT_MCP_VALUE": "present"},
        max_stderr_bytes=64,
    )
    await client.start()
    try:
        result = await client.call("environment", {})
        assert result == {"secret": None, "explicit": "present", "has_path": True}
        for _ in range(20):
            if client.stderr_tail:
                break
            await asyncio.sleep(0.01)
        assert client.stderr_tail == "x" * 64
    finally:
        await client.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="SIGTERM behavior is POSIX-specific")
async def test_stop_kills_process_that_ignores_terminate(tmp_path):
    server_script = tmp_path / "stubborn_mcp.py"
    server_script.write_text(
        """
import json
import signal
import sys

signal.signal(signal.SIGTERM, signal.SIG_IGN)
for line in sys.stdin:
    req = json.loads(line)
    if req.get("method") == "initialize":
        result = {"protocolVersion": req["params"]["protocolVersion"], "capabilities": {}}
        print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}), flush=True)
""",
        encoding="utf-8",
    )
    client = StdioJsonRpcClient(
        [sys.executable, str(server_script)],
        [],
        stop_timeout_seconds=0.05,
    )
    await client.start()
    process = client.process
    assert process is not None
    await client.stop()
    assert process.returncode is not None


def _mcp_manifest(command: list[str], env: dict[str, str] | None = None, app_version: str = "1.0.0"):
    return AppManifest(
        manifest_version=1,
        id="test-app",
        title="Test App",
        description="",
        app_version=app_version,
        intents=(),
        schema_refs=(),
        backend_type="mcp",
        mcp_server={"command": command, "args": [], "env": env or {}},
    )


def test_mcp_permission_identity_binds_env_and_manifest_revision(tmp_workspace):
    manager = BackendManager()
    manifest = _mcp_manifest(["mcp-server"], {"TOKEN": "one"})
    identity = manager.mcp_permission_identity(manifest)
    manager.approve_mcp_identity(manifest.id, identity)

    assert manager.is_mcp_identity_approved(manifest.id, identity)
    assert manager.is_mcp_approved(
        manifest.id,
        manifest.mcp_server["command"],
        [],
        {"TOKEN": "one"},
        "1:1.0.0",
    )
    assert not manager.is_mcp_approved(manifest.id, ["mcp-server"], [], {"TOKEN": "two"}, "1:1.0.0")
    assert not manager.is_mcp_approved(manifest.id, ["mcp-server"], [], {"TOKEN": "one"}, "1:2.0.0")
    assert set(identity) == {"command", "args", "env_digest", "manifest_revision"}
    assert "one" not in manager.permissions_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_get_or_start_serializes_concurrent_spawns(tmp_workspace, monkeypatch):
    created = []

    class FakeMCPClient:
        def __init__(self, command, args, env):
            self.process = SimpleNamespace(returncode=None, pid=123)
            self.is_healthy = False
            created.append(self)

        async def start(self):
            await asyncio.sleep(0.02)
            self.is_healthy = True

        async def stop(self):
            self.is_healthy = False

    monkeypatch.setattr("backend.backend_manager.StdioJsonRpcClient", FakeMCPClient)
    manager = BackendManager()
    manifest = _mcp_manifest(["mcp-server"])
    manager.approve_mcp_identity(manifest.id, manager.mcp_permission_identity(manifest))

    async def unexpected_permission(_message):
        pytest.fail("An exact pre-approval should not request permission")

    first, second = await asyncio.gather(
        manager.get_or_start_mcp_client(manifest.id, manifest, unexpected_permission),
        manager.get_or_start_mcp_client(manifest.id, manifest, unexpected_permission),
    )
    assert first is second
    assert len(created) == 1
    await manager.shutdown()


def _agent_manifest(agent_url: str = "https://agent.example.test/events") -> AppManifest:
    return AppManifest(
        manifest_version=1,
        id="test-agent",
        title="Test Agent",
        description="",
        app_version="1.0.0",
        intents=(),
        schema_refs=(),
        backend_type="agent",
        agent_url=agent_url,
    )


@pytest.mark.asyncio
async def test_http_agent_enforces_response_bound_and_fails_closed(tmp_workspace, monkeypatch):
    manifest = _agent_manifest()
    manager = BackendManager(http_agent_max_response_bytes=48)
    manager.approve_agent(manifest.id, manifest.agent_url)
    actual_client = httpx.AsyncClient

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'data: {"payload":"' + b"x" * 128 + b'"}\n')

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return actual_client(*args, **kwargs)

    monkeypatch.setattr("backend.backend_manager.httpx.AsyncClient", client_factory)
    emitted = []

    async def emit(event):
        emitted.append(event)

    with pytest.raises(RuntimeError, match="response exceeded 48 bytes"):
        await manager.handle_agent_message(manifest.id, manifest, {"message": "hello"}, emit)
    assert emitted == []
    assert manager.agent_runtimes[manifest.id]["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_http_agent_has_total_deadline_and_unknown_cancel_state(tmp_workspace, monkeypatch):
    manifest = _agent_manifest()
    manager = BackendManager(http_agent_timeout_seconds=0.02)
    manager.approve_agent(manifest.id, manifest.agent_url)
    entered_stream = asyncio.Event()

    class SlowResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def aiter_bytes(self, *, chunk_size):
            assert chunk_size > 0
            entered_stream.set()
            await asyncio.sleep(60)
            yield b""

    class SlowClient:
        def __init__(self, *, timeout, trust_env):
            assert timeout is not None
            assert trust_env is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, method, url, *, json):
            assert (method, url, json) == (
                "POST",
                manifest.agent_url,
                {"message": "hello"},
            )
            return SlowResponse()

    monkeypatch.setattr("backend.backend_manager.httpx.AsyncClient", SlowClient)

    async def emit(_event):
        pytest.fail("A timed-out stream must not emit an event")

    with pytest.raises(TimeoutError, match=r"timed out after 0\.02s"):
        await manager.handle_agent_message(manifest.id, manifest, {"message": "hello"}, emit)
    assert manager.agent_runtimes[manifest.id]["status"] == "unhealthy"

    manager.http_agent_timeout_seconds = 60
    entered_stream.clear()
    task = asyncio.create_task(manager.handle_agent_message(manifest.id, manifest, {"message": "hello"}, emit))
    await asyncio.wait_for(entered_stream.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.agent_runtimes[manifest.id]["status"] == "unknown"
