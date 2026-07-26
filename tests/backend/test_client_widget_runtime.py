from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import main
from backend.client_widget_runtime import (
    CLIENT_WIDGET_RUNTIME_POLICY_VIOLATION,
    CLIENT_WIDGET_RUNTIME_PROTOCOL,
    CLIENT_WIDGET_RUNTIME_SESSION_LIMIT,
    ClientWidgetRuntimeBinding,
    ClientWidgetRuntimeSessionLimitError,
    ClientWidgetRuntimeSessionStore,
    ClientWidgetRuntimeTicketError,
    ClientWidgetRuntimeTicketStore,
    LockedClientWidgetRuntimeConnection,
    allowed_client_runtime_origins,
    client_runtime_frame_url,
    client_runtime_origin,
)
from backend.graph_db import GraphDatabase


class FakeAppManager:
    def __init__(self) -> None:
        self.apps = {
            "notes-app": {
                "id": "notes-app",
                "manifest_revision": "2:1.0.0",
                "grants_digest": "sha256:notes",
                "capabilities": [
                    {"id": "file.read", "scope": {"paths": ["notes/**"]}}
                ],
                "js": "export default function App() { return null; }",
            }
        }

    def get_app_files(self, app_id: str) -> dict[str, Any] | None:
        return self.apps.get(app_id)


class FakeConnection:
    async def send_json(self, _message: dict[str, Any]) -> None:
        return None


def test_client_runtime_origin_policy_is_explicit_and_frame_url_isolated(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "AMBIENT_FRONTEND_ORIGINS",
        "https://ui.example,http://localhost:5173,*",
    )
    monkeypatch.delenv("WIDGET_FRAME_URL", raising=False)

    assert allowed_client_runtime_origins() == {
        "https://ui.example",
        "http://localhost:5173",
    }
    assert (
        client_runtime_origin({"origin": "HTTPS://UI.EXAMPLE:443"})
        == "https://ui.example"
    )
    assert (
        client_runtime_frame_url("https://ui.example")
        == "https://ui.example:8001/frame.html"
    )
    with pytest.raises(ClientWidgetRuntimeTicketError):
        client_runtime_origin({"origin": "null"})
    with pytest.raises(ClientWidgetRuntimeTicketError):
        client_runtime_origin({"origin": "https://attacker.example"})

    monkeypatch.setenv(
        "WIDGET_FRAME_URL",
        "https://widgets.example/frame.html",
    )
    assert (
        client_runtime_frame_url("https://ui.example")
        == "https://widgets.example/frame.html"
    )


def test_ticket_is_256_bit_single_use_and_bound_to_origin_and_snapshot() -> None:
    manager = FakeAppManager()
    clock = [100.0]
    store = ClientWidgetRuntimeTicketStore(
        manager,
        monotonic=lambda: clock[0],
        utcnow=lambda: datetime(2026, 7, 26, tzinfo=UTC),
    )

    ticket = store.issue("notes-app", "https://ui.example")

    assert len(base64.urlsafe_b64decode(ticket.token + "=")) == 32
    assert ticket.expires_at == datetime(2026, 7, 26, 0, 0, 30, tzinfo=UTC)
    assert ticket.artifact.app_id == "notes-app"
    assert ticket.artifact.capability_ids == ("file.read",)
    with pytest.raises(ClientWidgetRuntimeTicketError, match="binding"):
        store.consume(
            ticket.token,
            app_id="notes-app",
            origin="https://other.example",
        )

    consumed = store.consume(
        ticket.token,
        app_id="notes-app",
        origin="https://ui.example",
    )
    assert consumed == ticket
    with pytest.raises(ClientWidgetRuntimeTicketError, match="invalid"):
        store.consume(
            ticket.token,
            app_id="notes-app",
            origin="https://ui.example",
        )

    changed = store.issue("notes-app", "https://ui.example")
    manager.apps["notes-app"]["js"] = "export default function Changed() {}"
    with pytest.raises(ClientWidgetRuntimeTicketError, match="snapshot changed"):
        store.consume(
            changed.token,
            app_id="notes-app",
            origin="https://ui.example",
        )


def test_expired_ticket_cannot_be_consumed() -> None:
    clock = [5.0]
    store = ClientWidgetRuntimeTicketStore(
        FakeAppManager(),
        ttl_seconds=30,
        monotonic=lambda: clock[0],
    )
    ticket = store.issue("notes-app", "https://ui.example")
    clock[0] = 35.0

    with pytest.raises(ClientWidgetRuntimeTicketError, match="expired"):
        store.consume(
            ticket.token,
            app_id="notes-app",
            origin="https://ui.example",
        )


def test_client_session_store_enforces_active_budget() -> None:
    ticket_store = ClientWidgetRuntimeTicketStore(FakeAppManager())
    first_ticket = ticket_store.issue("notes-app", "https://ui.example")
    second_ticket = ticket_store.issue("notes-app", "https://ui.example")
    sessions = ClientWidgetRuntimeSessionStore(max_sessions=1)

    first = sessions.open(first_ticket, FakeConnection())
    with pytest.raises(ClientWidgetRuntimeSessionLimitError):
        sessions.open(second_ticket, FakeConnection())
    assert sessions.active_count == 1

    sessions.close(first.session_id)
    assert sessions.active_count == 0
    assert CLIENT_WIDGET_RUNTIME_SESSION_LIMIT == 4429


@pytest.mark.asyncio
async def test_locked_client_connection_serializes_subscription_and_rpc_sends() -> None:
    class ConcurrentSendDetector:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.sent: list[dict[str, Any]] = []

        async def send_json(self, message: dict[str, Any]) -> None:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0)
            self.sent.append(message)
            self.active -= 1

        async def close(self, *, code: int) -> None:
            return None

    websocket = ConcurrentSendDetector()
    connection = LockedClientWidgetRuntimeConnection(websocket)
    await asyncio.gather(
        connection.send_json({"type": "rpc_response"}),
        connection.send_json({"type": "subscription_event"}),
    )

    assert websocket.max_active == 1
    assert {message["type"] for message in websocket.sent} == {
        "rpc_response",
        "subscription_event",
    }


def test_ticket_endpoint_and_client_runtime_websocket_use_server_binding(
    monkeypatch,
) -> None:
    source = "export default function Notes() { return null; }"
    main.app_manager.create_or_update_app(
        "browser-notes",
        "Browser Notes",
        js=source,
        capabilities=[
            {"id": "file.read", "scope": {"paths": ["notes/**"]}},
        ],
    )
    monkeypatch.setenv(
        "AMBIENT_FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    monkeypatch.delenv("WIDGET_FRAME_URL", raising=False)
    main.client_widget_runtime_tickets.clear()
    main.client_widget_runtime_sessions.clear()
    calls: list[tuple[Any, str, dict[str, Any]]] = []

    async def rpc_handler(
        binding: ClientWidgetRuntimeBinding,
        method: str,
        params: dict[str, Any],
    ) -> Any:
        calls.append((binding, method, params))
        return {"text": "hello"}

    monkeypatch.setattr(main, "_handle_widget_runtime_rpc", rpc_handler)

    with TestClient(main.app) as client:
        denied = client.post(
            "/api/apps/browser-notes/client-runtime-ticket",
            headers={"origin": "https://attacker.example"},
        )
        assert denied.status_code == 403

        response = client.post(
            "/api/apps/browser-notes/client-runtime-ticket",
            headers={"origin": "http://localhost:5173"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        issued = response.json()
        assert set(issued) == {"ticket", "expires_at", "frame_url", "protocol"}
        assert issued["protocol"] == CLIENT_WIDGET_RUNTIME_PROTOCOL
        assert issued["frame_url"] == "http://localhost:8001/frame.html"
        assert datetime.fromisoformat(issued["expires_at"]).tzinfo is not None

        protocols = [
            CLIENT_WIDGET_RUNTIME_PROTOCOL,
            f"ticket.{issued['ticket']}",
        ]
        with client.websocket_connect(
            "/ws/widgets/browser-notes/client-runtime",
            subprotocols=protocols,
            headers={"origin": "http://localhost:5173"},
        ) as websocket:
            assert websocket.accepted_subprotocol == CLIENT_WIDGET_RUNTIME_PROTOCOL
            assert websocket.receive_json() == {
                "type": "bootstrap",
                "protocol_version": 1,
                "controller_source": source,
                "capability_ids": ["file.read"],
            }
            websocket.send_json(
                {
                    "type": "rpc_request",
                    "request_id": "rpc-1",
                    "method": "files.read",
                    "app_id": "forged-app",
                    "params": {
                        "path": "notes/today.md",
                        "app_id": "forged-app",
                        "manifest_revision": "forged",
                        "grants_digest": "forged",
                        "_runtime_request_id": "forged",
                    },
                }
            )
            assert websocket.receive_json() == {
                "type": "rpc_response",
                "request_id": "rpc-1",
                "result": {"text": "hello"},
            }

        assert main.client_widget_runtime_sessions.active_count == 0
        assert len(calls) == 1
        binding, method, params = calls[0]
        manifest = main.app_manager.get_manifest("browser-notes")
        assert manifest is not None
        assert binding.app_id == "browser-notes"
        assert binding.manifest_revision == manifest.revision
        assert binding.grants_digest == manifest.grants_digest
        assert method == "files.read"
        assert params == {
            "path": "notes/today.md",
            "_runtime_request_id": "rpc-1",
        }

        with pytest.raises(WebSocketDisconnect) as replay:
            with client.websocket_connect(
                "/ws/widgets/browser-notes/client-runtime",
                subprotocols=protocols,
                headers={"origin": "http://localhost:5173"},
            ):
                pass
        assert replay.value.code == CLIENT_WIDGET_RUNTIME_POLICY_VIOLATION


@pytest.mark.asyncio
async def test_graph_updates_are_projected_to_client_runtime_without_identity() -> None:
    class RecordingConnection:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []

        async def send_json(self, message: dict[str, Any]) -> None:
            self.sent.append(message)

    connection = RecordingConnection()
    binding = ClientWidgetRuntimeBinding(
        session_id="server-session",
        app_id="notes-app",
        manifest_revision="2:1.0.0",
        grants_digest="sha256:notes",
        artifact_digest="artifact",
        connection=connection,
    )

    await main._send_graph_subscription_payload(
        binding,
        {
            "type": "graph_query_update",
            "subscription_id": "tasks",
            "data": [{"id": "task-1"}],
        },
    )

    assert connection.sent == [
        {
            "type": "subscription_event",
            "subscription_id": "tasks",
            "data": [{"id": "task-1"}],
        }
    ]


def test_client_runtime_graph_subscription_pushes_and_is_cleaned_up(
    tmp_path,
    monkeypatch,
) -> None:
    from backend.graph_subscription import subscription_manager

    graph = GraphDatabase(str(tmp_path / "graph"))
    monkeypatch.setattr(main, "graph_db", graph)
    monkeypatch.setenv("AMBIENT_FRONTEND_ORIGINS", "http://localhost:5173")
    main.app_manager.create_or_update_app(
        "browser-tasks",
        "Browser Tasks",
        js="export default function Tasks() { return null; }",
        capabilities=[
            {"id": "graph.query", "scope": {"entities": ["Task"]}},
        ],
    )
    main.client_widget_runtime_tickets.clear()
    main.client_widget_runtime_sessions.clear()

    with TestClient(main.app) as client:
        issued = client.post(
            "/api/apps/browser-tasks/client-runtime-ticket",
            headers={"origin": "http://localhost:5173"},
        ).json()
        protocols = [
            CLIENT_WIDGET_RUNTIME_PROTOCOL,
            f"ticket.{issued['ticket']}",
        ]
        with client.websocket_connect(
            "/ws/widgets/browser-tasks/client-runtime",
            subprotocols=protocols,
            headers={"origin": "http://localhost:5173"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "bootstrap"
            websocket.send_json(
                {
                    "type": "rpc_request",
                    "request_id": "subscribe-1",
                    "method": "graph.subscribe",
                    "params": {
                        "subscription_id": "tasks",
                        "query": {"type": "Task"},
                    },
                }
            )
            assert websocket.receive_json() == {
                "type": "rpc_response",
                "request_id": "subscribe-1",
                "result": [],
            }
            bindings = [
                target
                for target in subscription_manager.active_subscriptions
                if isinstance(target, ClientWidgetRuntimeBinding)
                and target.app_id == "browser-tasks"
            ]
            assert len(bindings) == 1
            binding = bindings[0]

            graph.create_node(
                node_id="task-client-runtime",
                node_type="Task",
                properties={"title": "From browser Runtime"},
            )

            async def broadcast() -> None:
                await subscription_manager.broadcast_updates(
                    graph,
                    main._send_graph_subscription_payload,
                    authorizer=main.capability_authorizer,
                )

            client.portal.call(broadcast)
            assert websocket.receive_json() == {
                "type": "subscription_event",
                "subscription_id": "tasks",
                "data": [
                    {
                        "id": "task-client-runtime",
                        "type": "Task",
                        "properties": {"title": "From browser Runtime"},
                        "relations": [],
                    }
                ],
            }

        assert binding not in subscription_manager.active_subscriptions
        assert main.client_widget_runtime_sessions.active_count == 0
