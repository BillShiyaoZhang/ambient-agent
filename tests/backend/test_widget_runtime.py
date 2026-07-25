from __future__ import annotations

import hashlib
from typing import Any

import pytest

from backend.widget_runtime import WidgetRuntimeGateway, WidgetRuntimeLimits


class FakeAppManager:
    def __init__(self) -> None:
        self.apps = {
            "notes-app": {
                "id": "notes-app",
                "manifest_revision": "2:1.0.0",
                "grants_digest": "sha256:notes",
                "capabilities": [{"id": "file.read", "scope": {"paths": ["notes/**"]}}],
                "js": "export default function App() { return null; }",
            },
            "other-app": {
                "id": "other-app",
                "manifest_revision": "2:1.0.0",
                "grants_digest": "sha256:other",
                "capabilities": [],
                "js": "export default function Other() { return null; }",
            },
        }

    def get_app_files(self, app_id: str) -> dict[str, Any] | None:
        return self.apps.get(app_id)


class FakeRuntimeConnection:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_open_session_transfers_source_by_value_and_binds_artifact_identity() -> None:
    connection = FakeRuntimeConnection()
    gateway = WidgetRuntimeGateway(
        app_manager=FakeAppManager(),
        connector=lambda: connection,
    )

    binding = await gateway.open_session(
        "notes-app",
        {"width": 640, "height": 480, "device_scale_factor": 1},
        presentation_context={
            "theme": {"preference": "system", "effective": "light"},
            "locale": "zh-CN",
            "reduced_motion": True,
        },
    )

    assert binding.app_id == "notes-app"
    assert binding.manifest_revision == "2:1.0.0"
    assert binding.grants_digest == "sha256:notes"
    assert binding.artifact_digest == hashlib.sha256(
        b"export default function App() { return null; }"
    ).hexdigest()
    assert connection.sent == [
        {
            "type": "start",
            "protocol_version": 1,
            "session_id": binding.session_id,
            "app_id": "notes-app",
            "manifest_revision": "2:1.0.0",
            "grants_digest": "sha256:notes",
            "artifact_digest": binding.artifact_digest,
            "capability_ids": ["file.read"],
            "controller_source": "export default function App() { return null; }",
            "viewport": {"width": 640, "height": 480, "device_scale_factor": 1.0},
            "presentation_context": {
                "theme": {"preference": "system", "effective": "light"},
                "locale": "zh-CN",
                "reduced_motion": True,
            },
        }
    ]


@pytest.mark.asyncio
async def test_runtime_rpc_uses_server_binding_and_ignores_forged_identity() -> None:
    connection = FakeRuntimeConnection()
    calls: list[tuple[Any, str, dict[str, Any]]] = []

    async def rpc_handler(binding, method: str, params: dict[str, Any]) -> Any:
        calls.append((binding, method, params))
        return {"ok": True}

    gateway = WidgetRuntimeGateway(
        app_manager=FakeAppManager(),
        connector=lambda: connection,
        rpc_handler=rpc_handler,
    )
    binding = await gateway.open_session("notes-app", {"width": 320, "height": 240})

    forwarded = await gateway.handle_runtime_message(
        binding.session_id,
        {
            "type": "rpc_request",
            "request_id": "rpc-1",
            "app_id": "other-app",
            "manifest_revision": "forged",
            "grants_digest": "forged",
            "method": "files.read",
            "params": {
                "path": "notes/today.md",
                "app_id": "other-app",
                "manifest_revision": "forged",
                "_runtime_request_id": "forged",
            },
        },
    )

    assert forwarded is None
    assert len(calls) == 1
    called_binding, method, params = calls[0]
    assert called_binding.app_id == "notes-app"
    assert called_binding.manifest_revision == "2:1.0.0"
    assert method == "files.read"
    assert params == {
        "path": "notes/today.md",
        "_runtime_request_id": "rpc-1",
    }
    assert connection.sent[-1] == {
        "type": "rpc_response",
        "session_id": binding.session_id,
        "request_id": "rpc-1",
        "result": {"ok": True},
    }


@pytest.mark.asyncio
async def test_gateway_enforces_session_viewport_and_message_budgets() -> None:
    connection = FakeRuntimeConnection()
    limits = WidgetRuntimeLimits(
        max_sessions=1,
        max_viewport_width=800,
        max_viewport_height=600,
        max_message_bytes=128,
    )
    gateway = WidgetRuntimeGateway(
        app_manager=FakeAppManager(),
        connector=lambda: connection,
        limits=limits,
    )

    with pytest.raises(ValueError, match="viewport"):
        await gateway.open_session("notes-app", {"width": 801, "height": 600})

    first = await gateway.open_session("notes-app", {"width": 800, "height": 600})
    with pytest.raises(RuntimeError, match="session limit"):
        await gateway.open_session("other-app", {"width": 320, "height": 240})

    with pytest.raises(ValueError, match="message"):
        await gateway.forward_input(
            first.session_id,
            {"type": "text", "text": "x" * 512, "app_id": "other-app"},
        )

    await gateway.close_session(first.session_id)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_forward_input_allowlists_fields_and_never_relays_identity() -> None:
    connection = FakeRuntimeConnection()
    gateway = WidgetRuntimeGateway(app_manager=FakeAppManager(), connector=lambda: connection)
    binding = await gateway.open_session("notes-app", {"width": 320, "height": 240})

    await gateway.forward_input(
        binding.session_id,
        {
            "type": "pointer",
            "event": "mousePressed",
            "x": 12,
            "y": 34,
            "button": "left",
            "buttons": 1,
            "click_count": 1,
            "app_id": "other-app",
            "manifest_revision": "forged",
            "controller_source": "malicious",
        },
    )

    assert connection.sent[-1] == {
        "type": "input",
        "session_id": binding.session_id,
        "event": {
            "type": "pointer",
            "event": "mousePressed",
            "x": 12.0,
            "y": 34.0,
            "button": "left",
            "buttons": 1,
            "click_count": 1,
        },
    }

    await gateway.forward_input(
        binding.session_id,
        {
            "type": "presentation_context",
            "theme": {"preference": "light", "effective": "light"},
            "locale": "zh-CN",
            "reduced_motion": True,
            "app_id": "other-app",
            "grants_digest": "forged",
        },
    )

    assert connection.sent[-1] == {
        "type": "input",
        "session_id": binding.session_id,
        "event": {
            "type": "presentation_context",
            "theme": {"preference": "light", "effective": "light"},
            "locale": "zh-CN",
            "reduced_motion": True,
        },
    }


@pytest.mark.asyncio
async def test_presentation_context_is_normalized_at_every_frontend_boundary() -> None:
    connection = FakeRuntimeConnection()
    gateway = WidgetRuntimeGateway(app_manager=FakeAppManager(), connector=lambda: connection)

    binding = await gateway.open_session(
        "notes-app",
        {"width": 320, "height": 240},
        presentation_context={
            "theme": {"preference": "unsupported", "effective": "invalid"},
            "locale": "../../etc/passwd",
            "reduced_motion": "yes",
        },
    )

    assert connection.sent[0]["presentation_context"] == {
        "theme": {"preference": "system", "effective": "dark"},
        "locale": "en-US",
        "reduced_motion": False,
    }

    await gateway.forward_input(
        binding.session_id,
        {
            "type": "presentation_context",
            "theme": {"preference": "dark", "effective": "dark"},
            "locale": "fr-FR",
            "reduced_motion": False,
        },
    )
    assert connection.sent[-1]["event"] == {
        "type": "presentation_context",
        "theme": {"preference": "dark", "effective": "dark"},
        "locale": "fr-FR",
        "reduced_motion": False,
    }


def test_compose_runtime_has_no_network_or_host_workspace_mount() -> None:
    import yaml
    from pathlib import Path

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    )
    runtime = compose["services"]["widget-runtime"]
    backend = compose["services"]["backend"]

    assert runtime["network_mode"] == "none"
    assert runtime["read_only"] is True
    assert runtime["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in runtime["security_opt"]
    assert "seccomp=unconfined" in runtime["security_opt"]
    assert runtime["pids_limit"] <= 256
    assert set(runtime["tmpfs"]) == {
        "/tmp:size=256m,mode=1777",
        "/home/widget:size=64m,uid=10001,gid=10001,mode=0700",
    }
    runtime_mounts = "\n".join(runtime.get("volumes", []))
    assert "./" not in runtime_mounts
    assert "workspace" not in runtime_mounts
    assert "docker.sock" not in runtime_mounts
    assert runtime_mounts == "widget_runtime_socket:/run/ambient-widget-runtime"
    assert "widget_runtime_socket:/run/ambient-widget-runtime" in backend["volumes"]
    assert runtime["healthcheck"]["test"][:3] == ["CMD", "node", "-e"]
    assert backend["depends_on"]["widget-runtime"]["condition"] == "service_healthy"
