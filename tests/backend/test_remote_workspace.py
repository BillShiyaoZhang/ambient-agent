from __future__ import annotations

import asyncio
import base64
import gzip
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from websockets.asyncio.client import connect as connect_local_socket
from websockets.asyncio.server import serve

from backend.remote_workspace import (
    MAX_HTTP_BYTES,
    RemoteWorkspaceConnector,
    RemoteWorkspaceDenied,
    RemoteWorkspaceNodeStore,
    allowed_route,
    normalize_gateway_url,
)
from backend.remote_workspace_api import create_remote_workspace_router


NOW = datetime(2026, 10, 1, tzinfo=UTC)
IDENTITY = {
    "node_id": "node-test",
    "account_id": "account-one",
    "grant_id": "grant-one",
    "scopes": ["workspace.control"],
    "workspace_origin": "http://node-test.localhost:8787",
}


@pytest.fixture(autouse=True)
def isolate_remote_upstream_mode(monkeypatch):
    monkeypatch.delenv("AMBIENT_REMOTE_UPSTREAM_MODE", raising=False)


def paired_store(tmp_path, *, scopes=None):
    store = RemoteWorkspaceNodeStore(tmp_path, now=lambda: NOW)
    store.save(
        {
            **IDENTITY,
            "scopes": scopes or IDENTITY["scopes"],
            "status": "paired",
            "approved": True,
            "connector_token": "device-secret",
            "gateway_url": "http://localhost:8787",
            "portal_url": "http://localhost:3000",
            "name": "My computer",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }
    )
    return store


def request_message(**updates):
    return {
        "type": "http.request",
        "id": "request-one",
        **IDENTITY,
        "service": "backend",
        "method": "GET",
        "path": "/api/sessions",
        "headers": [],
        "body": "",
        **updates,
    }


def test_persistent_state_hides_credential_and_fails_closed_after_expiry(tmp_path):
    store = paired_store(tmp_path)
    assert "connector_token" not in store.status()
    assert "device-secret" not in json.dumps(store.status())
    restored = RemoteWorkspaceNodeStore(tmp_path, now=lambda: NOW)
    assert restored.identity() == IDENTITY
    restored.now = lambda: NOW + timedelta(hours=2)
    with pytest.raises(RemoteWorkspaceDenied, match="expired"):
        restored.identity()
    restored.revoke()
    assert restored.status()["status"] == "revoked"
    assert "device-secret" not in restored.path.read_text(encoding="utf-8")


def test_disconnected_status_has_empty_scopes(tmp_path):
    assert RemoteWorkspaceConnector(RemoteWorkspaceNodeStore(tmp_path)).status()["scopes"] == []


@pytest.mark.parametrize(
    "url",
    [
        "http://gateway.example",
        "file:///tmp",
        "https://user:pass@x",
        "http://127.0.0.1.evil:8080",
        "https://gateway.example:invalid",
    ],
)
def test_gateway_rejects_insecure_or_credential_urls(url):
    with pytest.raises(ValueError):
        normalize_gateway_url(url)


def test_scope_and_route_policy_rejects_remote_management_and_traversal():
    assert allowed_route("backend", "GET", "/api/sessions", ["workspace.control"])
    assert not allowed_route("backend", "PATCH", "/api/llm/settings", ["workspace.control"])
    assert allowed_route("backend", "PATCH", "/api/llm/settings", ["workspace.control", "workspace.manage"])
    for path in (
        "/api/remote-workspace/pair",
        "/api/apps/%2e%2e/remote-workspace",
        "//evil.test",
        "/api/apps/%252e%252e/x",
        "/api/unknown",
        "/api/apps/a%2fb",
    ):
        assert not allowed_route("backend", "POST", path, ["workspace.control", "workspace.manage"])
    assert not allowed_route("private-network", "GET", "/", ["workspace.control"])
    assert allowed_route("backend", "POST", "/api/apps/notes/graph/mutate", ["workspace.control"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [None, "loopback", "docker"])
async def test_upstream_mode_selects_only_fixed_local_service_targets(tmp_path, monkeypatch, mode):
    if mode is not None:
        monkeypatch.setenv("AMBIENT_REMOTE_UPSTREAM_MODE", mode)
    urls = []

    def upstream(request):
        urls.append(str(request.url))
        return httpx.Response(200, content=b"ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        connector = RemoteWorkspaceConnector(paired_store(tmp_path), local_http=client)
        for service, path in (("frontend", "/"), ("backend", "/api/sessions"), ("frame", "/frame.html")):
            assert (await connector.handle_http(request_message(service=service, path=path)))["status"] == 200
    if mode == "docker":
        assert urls == [
            "http://frontend:5173/",
            "http://127.0.0.1:8000/api/sessions",
            "http://widget-frame:8001/frame.html",
        ]
    else:
        assert urls == [
            "http://127.0.0.1:5173/",
            "http://127.0.0.1:8000/api/sessions",
            "http://127.0.0.1:8001/frame.html",
        ]


@pytest.mark.parametrize("mode", ["external", "http://attacker.test", ""])
def test_unknown_upstream_mode_fails_closed_at_process_configuration(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("AMBIENT_REMOTE_UPSTREAM_MODE", mode)
    with pytest.raises(ValueError, match="AMBIENT_REMOTE_UPSTREAM_MODE"):
        RemoteWorkspaceConnector(paired_store(tmp_path))


@pytest.mark.asyncio
async def test_http_bridge_uses_loopback_filters_headers_and_rewrites_frame(tmp_path):
    received = []

    def upstream(request):
        received.append(request)
        return httpx.Response(
            200,
            json={"ticket": "short-ticket", "frame_url": "http://localhost:8001/frame.html"},
            headers={"set-cookie": "secret=bad", "content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        connector = RemoteWorkspaceConnector(paired_store(tmp_path), local_http=client)
        response = await connector.handle_http(
            request_message(
                method="POST",
                path="/api/apps/demo/client-runtime-ticket",
                headers=[
                    ["Origin", "https://attacker.test"],
                    ["Authorization", "Bearer bad"],
                    ["Cookie", "bad=true"],
                    ["X-Forwarded-For", "1.2.3.4"],
                    ["Content-Type", "application/json"],
                ],
                body=base64.b64encode(b"{}").decode(),
            )
        )
        assert response["status"] == 200
        request = received[0]
        assert str(request.url) == "http://127.0.0.1:8000/api/apps/demo/client-runtime-ticket"
        assert request.headers["origin"] == "http://localhost:5173"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        assert "x-forwarded-for" not in request.headers
        assert not any(key.lower() == "set-cookie" for key, _ in response["headers"])
        body = json.loads(base64.b64decode(response["body"]))
        assert body["frame_url"] == IDENTITY["workspace_origin"] + "/_ambient/frame.html"
        assert body["ticket"] == "short-ticket"


@pytest.mark.asyncio
async def test_http_bridge_rechecks_identity_scope_size_and_expiry(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"ok")

    store = paired_store(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        connector = RemoteWorkspaceConnector(store, local_http=client)
        for update in (
            {"account_id": "other"},
            {"grant_id": "other"},
            {"method": "DELETE", "path": "/api/llm/providers/a"},
            {"body": base64.b64encode(b"x" * (MAX_HTTP_BYTES + 1)).decode()},
        ):
            result = await connector.handle_http(request_message(**update))
            assert result["status"] in {403, 413}
        store.now = lambda: NOW + timedelta(hours=2)
        assert (await connector.handle_http(request_message()))["status"] == 403
        assert calls == []


@pytest.mark.asyncio
async def test_oversized_upstream_http_response_is_bounded(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (MAX_HTTP_BYTES + 1)))
    ) as client:
        connector = RemoteWorkspaceConnector(paired_store(tmp_path), local_http=client)
        result = await connector.handle_http(request_message())
        assert result["status"] == 413


class EncodedAssetStream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body


@pytest.mark.asyncio
async def test_fixed_frame_gzip_asset_preserves_wire_encoding_with_bounded_transfer(tmp_path):
    asset = b"large fixed compiler asset\n" * 100_000
    compressed = gzip.compress(asset)
    assert len(asset) > MAX_HTTP_BYTES and len(compressed) < MAX_HTTP_BYTES
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(
            200,
            stream=EncodedAssetStream(compressed),
            headers={"content-type": "text/javascript", "content-encoding": "gzip", "vary": "Accept-Encoding"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        connector = RemoteWorkspaceConnector(paired_store(tmp_path), local_http=client)
        result = await connector.handle_http(
            request_message(
                service="frame",
                path="/vendor/babel.min.js?v=known",
                headers=[["Accept-Encoding", "br, gzip"], ["Accept-Encoding", "identity"]],
            )
        )
        assert result["status"] == 200
        assert requests[0].headers.get_list("accept-encoding") == ["gzip"]
        assert dict(result["headers"])["content-encoding"] == "gzip"
        assert base64.b64decode(result["body"]) == compressed
        assert gzip.decompress(base64.b64decode(result["body"])) == asset


@pytest.mark.asyncio
async def test_compressed_ordinary_http_still_bounds_decoded_content_and_strips_encoding(tmp_path):
    def upstream(request):
        body = b"small" if request.url.path == "/" else b"x" * (MAX_HTTP_BYTES + 1)
        return httpx.Response(200, stream=EncodedAssetStream(gzip.compress(body)), headers={"content-encoding": "gzip"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        connector = RemoteWorkspaceConnector(paired_store(tmp_path), local_http=client)
        assert (await connector.handle_http(request_message()))["status"] == 413
        small = await connector.handle_http(request_message(service="frontend", path="/"))
        assert small["status"] == 200 and base64.b64decode(small["body"]) == b"small"
        assert "content-encoding" not in dict(small["headers"])


@pytest.mark.asyncio
async def test_frame_raw_response_rejects_unknown_encoding_and_oversized_wire_body(tmp_path):
    def upstream(request):
        if request.url.path == "/frame.html":
            return httpx.Response(
                200, stream=EncodedAssetStream(b"unknown encoding"), headers={"content-encoding": "deflate"}
            )
        return httpx.Response(
            200,
            stream=EncodedAssetStream(b"x" * (MAX_HTTP_BYTES + 1)),
            headers={"content-encoding": "gzip"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        connector = RemoteWorkspaceConnector(paired_store(tmp_path), local_http=client)
        assert (await connector.handle_http(request_message(service="frame", path="/frame.html")))["status"] == 502
        assert (await connector.handle_http(request_message(service="frame", path="/vendor/babel.min.js")))[
            "status"
        ] == 413


@pytest.mark.asyncio
async def test_pair_claim_local_approve_and_revoke_with_offline_cloud(tmp_path, monkeypatch):
    store = RemoteWorkspaceNodeStore(tmp_path, now=lambda: NOW)
    current = {
        **IDENTITY,
        "status": "pending",
        "account_id": None,
        "grant_id": None,
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "account_label": "Alice",
    }
    requests = []

    def gateway(request):
        requests.append(request)
        if request.url.path.endswith("pairings"):
            return httpx.Response(
                200,
                json={
                    **current,
                    "connector_token": "device-secret",
                    "pairing_code": "one-time",
                    "pairing_expires_at": (NOW + timedelta(minutes=5)).isoformat(),
                },
            )
        assert request.headers["authorization"] == "Bearer device-secret"
        if request.url.path.endswith("approve"):
            assert json.loads(request.content) == {"account_id": "account-one", "grant_id": "grant-one"}
            current["status"] = "paired"
        if request.url.path.endswith("revoke"):
            assert store.status()["status"] == "revoked"
            assert "connector_token" not in store._state
            raise httpx.ConnectError("network unavailable")
        return httpx.Response(200, json=current)

    async with httpx.AsyncClient(transport=httpx.MockTransport(gateway)) as client:
        connector = RemoteWorkspaceConnector(store, gateway_http=client)

        async def no_background():
            pass

        monkeypatch.setattr(connector, "start", no_background)
        status = await connector.pair(
            {
                "gateway_url": "http://localhost:8787",
                "portal_url": "http://localhost:3000",
                "name": "My computer",
                "scopes": ["workspace.control"],
                "expires_in": 3600,
            }
        )
        assert status["status"] == "pending" and status["pairing_code"] == "one-time"
        assert "device-secret" not in json.dumps(status)
        with pytest.raises(RemoteWorkspaceDenied):
            store.identity()
        current.update({"status": "claimed", "account_id": "account-one", "grant_id": "grant-one"})
        assert (await connector.refresh())["status"] == "claimed"
        with pytest.raises(RemoteWorkspaceDenied):
            await connector.approve("another-account", "grant-one")
        assert (await connector.approve("account-one", "grant-one"))["status"] == "paired"
        assert RemoteWorkspaceNodeStore(tmp_path, now=lambda: NOW).identity() == IDENTITY
        assert (await connector.revoke())["status"] == "revoked"
        assert "device-secret" not in store.path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_pair_gateway_cannot_extend_locally_requested_expiry(tmp_path, monkeypatch):
    state = {
        **IDENTITY,
        "connector_token": "secret",
        "pairing_code": "code",
        "pairing_expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(days=20)).isoformat(),
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=state))
    ) as client:
        connector = RemoteWorkspaceConnector(RemoteWorkspaceNodeStore(tmp_path, now=lambda: NOW), gateway_http=client)

        async def no_background():
            pass

        monkeypatch.setattr(connector, "start", no_background)
        with pytest.raises(RemoteWorkspaceDenied, match="expiry"):
            await connector.pair(
                {
                    "gateway_url": "http://localhost:8787",
                    "portal_url": "http://localhost:3000",
                    "name": "Node",
                    "scopes": ["workspace.control"],
                    "expires_in": 3600,
                }
            )


class LocalSocket:
    subprotocol = "ambient-widget-client-v1"

    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def recv(self):
        return await self.messages.get()

    async def send(self, data):
        self.sent.append(data)

    async def close(self, **kwargs):
        self.closed = True


@pytest.mark.asyncio
async def test_ws_bridge_both_directions_and_revoke_closes_connection(tmp_path):
    socket = LocalSocket()
    sent = []
    connected = []

    async def connect(url, **kwargs):
        connected.append((url, kwargs))
        return socket

    async def send(message):
        sent.append(message)

    store = paired_store(tmp_path)
    connector = RemoteWorkspaceConnector(store, local_ws_connect=connect)
    await connector.handle_message(
        {
            **request_message(),
            "type": "ws.open",
            "path": "/ws/widgets/demo/client-runtime",
            "subprotocols": ["ambient-widget-client-v1", "ticket.short"],
        },
        send,
    )
    assert sent[0]["type"] == "ws.accept"
    assert connected[0][0] == "ws://127.0.0.1:8000/ws/widgets/demo/client-runtime"
    await connector.handle_message(
        {"type": "ws.data", "id": "request-one", "kind": "text", "data": "browser-message"}, send
    )
    assert socket.sent == ["browser-message"]
    await socket.messages.put(b"node-message")
    await asyncio.wait_for(_wait_for(lambda: any(item["type"] == "ws.data" for item in sent)), timeout=1)
    frame = next(item for item in sent if item["type"] == "ws.data")
    assert base64.b64decode(frame["data"]) == b"node-message"
    store.revoke()
    await connector.handle_message({"type": "ws.data", "id": "request-one", "kind": "text", "data": "denied"}, send)
    assert socket.closed
    assert socket.sent == ["browser-message"]
    await connector.stop()


@pytest.mark.asyncio
async def test_actual_loopback_websocket_bridge_echoes_text_bytes_and_rechecks_expiry(tmp_path):
    """Exercise real local WS handshakes and frame IO without any external network."""
    origins = []

    async def echo(socket):
        origins.append(socket.request.headers["origin"])
        async for data in socket:
            await socket.send(data)

    forwarded = []

    async def send(message):
        forwarded.append(message)

    async with serve(echo, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connect(url, **kwargs):
            assert url == "ws://127.0.0.1:8000/ws/chat?session_id=demo"
            return await connect_local_socket(f"ws://127.0.0.1:{port}/ws/chat?session_id=demo", **kwargs)

        store = paired_store(tmp_path)
        connector = RemoteWorkspaceConnector(store, local_ws_connect=connect)
        await connector.handle_message(
            {**request_message(), "type": "ws.open", "path": "/ws/chat?session_id=demo", "subprotocols": []}, send
        )
        assert forwarded[0]["type"] == "ws.accept"
        assert origins == ["http://localhost:5173"]
        await connector.handle_message(
            {"type": "ws.data", "id": "request-one", "kind": "text", "data": "text-payload"}, send
        )
        await asyncio.wait_for(_wait_for(lambda: len(forwarded) >= 2), timeout=1)
        assert forwarded[1] == {"type": "ws.data", "id": "request-one", "kind": "text", "data": "text-payload"}
        encoded = base64.b64encode(b"\x00\x01bytes").decode()
        await connector.handle_message({"type": "ws.data", "id": "request-one", "kind": "bytes", "data": encoded}, send)
        await asyncio.wait_for(_wait_for(lambda: len(forwarded) >= 3), timeout=1)
        assert forwarded[2]["kind"] == "bytes" and forwarded[2]["data"] == encoded
        store.now = lambda: NOW + timedelta(hours=2)
        await connector.handle_message(
            {"type": "ws.data", "id": "request-one", "kind": "text", "data": "expired"}, send
        )
        assert forwarded[-1]["type"] == "ws.close" and forwarded[-1]["code"] == 4403
        await connector.stop()


class TunnelSocket:
    def __init__(self):
        self.receives = 0
        self.queue = asyncio.Queue()
        self.queue.put_nowait(json.dumps({"type": "hello", **IDENTITY}))
        self.entered = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def recv(self):
        self.receives += 1
        self.entered.set()
        try:
            return await self.queue.get()
        finally:
            self.receives -= 1

    async def send(self, message):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_stop_cleans_tunnel_receive_tasks_and_can_restart(tmp_path):
    tunnels = []

    def connect_tunnel(*args, **kwargs):
        tunnel = TunnelSocket()
        tunnels.append(tunnel)
        return tunnel

    remote = {**IDENTITY, "status": "paired", "expires_at": (NOW + timedelta(hours=1)).isoformat()}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=remote))
    ) as client:
        connector = RemoteWorkspaceConnector(paired_store(tmp_path), gateway_http=client, tunnel_connect=connect_tunnel)
        for _ in range(2):
            await connector.start()
            await asyncio.wait_for(_wait_for(lambda: connector.online and tunnels[-1].receives == 1), timeout=1)
            await connector.stop()
            assert not connector.online
            assert tunnels[-1].receives == 0


@pytest.mark.asyncio
async def test_unconfigured_connector_start_never_attempts_network(tmp_path):
    calls = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
        connector = RemoteWorkspaceConnector(RemoteWorkspaceNodeStore(tmp_path), gateway_http=client)
        await connector.start()
        assert not connector.online
        await connector.stop()
    assert calls == []


@pytest.mark.asyncio
async def test_failed_tunnel_task_cannot_prevent_other_resources_from_closing(tmp_path):
    connector = RemoteWorkspaceConnector(paired_store(tmp_path))
    socket = LocalSocket()

    async def failed():
        raise RuntimeError("transport failed with private credential")

    task = asyncio.create_task(asyncio.sleep(60))
    connector._ws["request-one"] = (socket, task, request_message())
    connector._runner = asyncio.create_task(failed())
    await asyncio.sleep(0)
    try:
        await connector.stop()
        assert socket.closed
        assert "private credential" not in json.dumps(connector.status())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _wait_for(predicate):
    while not predicate():
        await asyncio.sleep(0)


def test_local_management_api_rejects_remote_peer_and_never_discloses_secret(tmp_path):
    connector = RemoteWorkspaceConnector(paired_store(tmp_path))
    app = FastAPI()
    app.include_router(create_remote_workspace_router(lambda: connector))
    with TestClient(app, client=("127.0.0.1", 4444)) as client:
        response = client.get("/api/remote-workspace/status")
        assert response.status_code == 200
        assert "device-secret" not in response.text
        assert (
            client.post("/api/remote-workspace/revoke", headers={"origin": "https://attacker.test"}).status_code == 403
        )
        assert (
            client.post(
                "/api/remote-workspace/approve",
                json={"account_id": "other", "grant_id": "other"},
                headers={"origin": "null"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/remote-workspace/pair",
                json={
                    "gateway_url": "http://outside.example",
                    "portal_url": "http://localhost:3000",
                    "name": "Node",
                    "scopes": ["workspace.control"],
                    "expires_in": 3600,
                },
            ).status_code
            == 422
        )
    with TestClient(app, client=("10.10.1.2", 4444)) as client:
        assert client.get("/api/remote-workspace/status").status_code == 403
        assert client.post("/api/remote-workspace/revoke").status_code == 403
