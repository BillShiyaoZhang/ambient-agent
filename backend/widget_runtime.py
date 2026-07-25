"""Backend control plane for isolated Widget Chromium sessions.

Controller code crosses this boundary by value and is evaluated only by the
separate zero-network runtime. Capability identity always comes from the
server-owned session binding, never from a runtime payload.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


WIDGET_RUNTIME_PROTOCOL_VERSION = 1
DEFAULT_WIDGET_RUNTIME_SOCKET = "/run/ambient-widget-runtime/runtime.sock"
_IDENTITY_FIELDS = frozenset(
    {
        "app_id",
        "manifest_revision",
        "grants_digest",
        "artifact_digest",
        "controller_source",
        "session_id",
        "_runtime_request_id",
    }
)
_LOCALE_PATTERN = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


class RuntimeConnection(Protocol):
    async def send_json(self, message: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


class RuntimeReadableConnection(RuntimeConnection, Protocol):
    async def receive_json(self) -> dict[str, Any]: ...


class UnixRuntimeConnection:
    """Bounded newline-delimited JSON connection over a Unix domain socket."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_message_bytes: int,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._max_message_bytes = max_message_bytes
        self._write_lock = asyncio.Lock()

    async def send_json(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > self._max_message_bytes:
            raise ValueError("Widget Runtime message exceeds the configured byte limit")
        async with self._write_lock:
            self._writer.write(payload + b"\n")
            await self._writer.drain()

    async def receive_json(self) -> dict[str, Any]:
        try:
            payload = await self._reader.readuntil(b"\n")
        except asyncio.LimitOverrunError as exc:
            raise ValueError("Widget Runtime message exceeds the configured byte limit") from exc
        if not payload:
            raise EOFError("Widget Runtime connection closed")
        if len(payload) > self._max_message_bytes + 1:
            raise ValueError("Widget Runtime message exceeds the configured byte limit")
        try:
            message = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Widget Runtime sent invalid JSON") from exc
        if not isinstance(message, dict):
            raise ValueError("Widget Runtime messages must be JSON objects")
        return message

    async def close(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, RuntimeError):
            pass


@dataclass(frozen=True, slots=True)
class WidgetRuntimeLimits:
    max_sessions: int = 16
    max_viewport_width: int = 4096
    max_viewport_height: int = 4096
    max_device_scale_factor: float = 2.0
    max_message_bytes: int = 4 * 1024 * 1024
    max_controller_bytes: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class WidgetRuntimeBinding:
    session_id: str
    app_id: str
    manifest_revision: str
    grants_digest: str
    artifact_digest: str
    connection: RuntimeConnection = field(repr=False, compare=False)


RuntimeConnector = Callable[[], RuntimeConnection | Awaitable[RuntimeConnection]]
RuntimeRpcHandler = Callable[
    [WidgetRuntimeBinding, str, dict[str, Any]],
    Any | Awaitable[Any],
]


class WidgetRuntimeGateway:
    """Owns Runtime sessions and binds every RPC to persistent App identity."""

    def __init__(
        self,
        *,
        app_manager: Any,
        connector: RuntimeConnector | None = None,
        rpc_handler: RuntimeRpcHandler | None = None,
        limits: WidgetRuntimeLimits | None = None,
        socket_path: str | None = None,
    ) -> None:
        self.app_manager = app_manager
        self.limits = limits or WidgetRuntimeLimits()
        self.socket_path = socket_path or os.getenv(
            "WIDGET_RUNTIME_SOCKET_PATH",
            DEFAULT_WIDGET_RUNTIME_SOCKET,
        )
        self._connector = connector or self._connect_unix
        self._rpc_handler = rpc_handler
        self._sessions: dict[str, WidgetRuntimeBinding] = {}
        self._session_lock = asyncio.Lock()

    async def _connect_unix(self) -> UnixRuntimeConnection:
        reader, writer = await asyncio.open_unix_connection(
            self.socket_path,
            limit=self.limits.max_message_bytes + 1,
        )
        return UnixRuntimeConnection(
            reader,
            writer,
            max_message_bytes=self.limits.max_message_bytes,
        )

    @staticmethod
    def _require_string(value: Any, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"App {name} is required for a Widget Runtime session")
        return value

    def _normalize_viewport(self, viewport: dict[str, Any]) -> dict[str, int | float]:
        if not isinstance(viewport, dict):
            raise ValueError("Widget Runtime viewport must be an object")
        width = viewport.get("width")
        height = viewport.get("height")
        scale = viewport.get("device_scale_factor", 1)
        if (
            not isinstance(width, (int, float))
            or isinstance(width, bool)
            or not isinstance(height, (int, float))
            or isinstance(height, bool)
            or not isinstance(scale, (int, float))
            or isinstance(scale, bool)
        ):
            raise ValueError("Widget Runtime viewport values must be numeric")
        width = int(width)
        height = int(height)
        scale = float(scale)
        if (
            width < 64
            or height < 64
            or width > self.limits.max_viewport_width
            or height > self.limits.max_viewport_height
            or scale <= 0
            or scale > self.limits.max_device_scale_factor
        ):
            raise ValueError("Widget Runtime viewport exceeds the configured bounds")
        return {"width": width, "height": height, "device_scale_factor": scale}

    @staticmethod
    def _normalize_theme(theme: Any) -> dict[str, str]:
        theme = theme if isinstance(theme, dict) else {}
        preference = theme.get("preference")
        effective = theme.get("effective")
        return {
            "preference": preference if preference in {"light", "dark", "system"} else "system",
            "effective": effective if effective in {"light", "dark"} else "dark",
        }

    @classmethod
    def _normalize_presentation_context(cls, context: Any) -> dict[str, Any]:
        context = context if isinstance(context, dict) else {}
        locale = context.get("locale")
        if (
            not isinstance(locale, str)
            or len(locale) > 35
            or _LOCALE_PATTERN.fullmatch(locale) is None
        ):
            locale = "en-US"
        return {
            "theme": cls._normalize_theme(context.get("theme")),
            "locale": locale,
            "reduced_motion": (
                context.get("reduced_motion")
                if isinstance(context.get("reduced_motion"), bool)
                else False
            ),
        }

    async def _new_connection(self) -> RuntimeConnection:
        connection = self._connector()
        if inspect.isawaitable(connection):
            connection = await connection
        return connection

    async def open_session(
        self,
        app_id: str,
        viewport: dict[str, Any],
        *,
        presentation_context: dict[str, Any] | None = None,
    ) -> WidgetRuntimeBinding:
        normalized_viewport = self._normalize_viewport(viewport)
        app = self.app_manager.get_app_files(app_id)
        if not isinstance(app, dict):
            raise KeyError(f"App '{app_id}' was not found")
        if app.get("id") != app_id:
            raise ValueError("Widget Runtime App identity does not match the requested App")
        source = self._require_string(app.get("js"), "Controller")
        source_bytes = source.encode("utf-8")
        if len(source_bytes) > self.limits.max_controller_bytes:
            raise ValueError("Widget Runtime Controller exceeds the configured byte limit")
        revision = self._require_string(app.get("manifest_revision"), "manifest revision")
        grants_digest = self._require_string(app.get("grants_digest"), "grants digest")
        artifact_digest = hashlib.sha256(source_bytes).hexdigest()
        capabilities = app.get("capabilities") if isinstance(app.get("capabilities"), list) else []
        capability_ids = sorted(
            {
                item["id"]
                for item in capabilities
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        )

        async with self._session_lock:
            if len(self._sessions) >= self.limits.max_sessions:
                raise RuntimeError("Widget Runtime session limit reached")
            connection = await self._new_connection()
            session_id = secrets.token_urlsafe(24)
            binding = WidgetRuntimeBinding(
                session_id=session_id,
                app_id=app_id,
                manifest_revision=revision,
                grants_digest=grants_digest,
                artifact_digest=artifact_digest,
                connection=connection,
            )
            self._sessions[session_id] = binding

        try:
            await connection.send_json(
                {
                    "type": "start",
                    "protocol_version": WIDGET_RUNTIME_PROTOCOL_VERSION,
                    "session_id": session_id,
                    "app_id": app_id,
                    "manifest_revision": revision,
                    "grants_digest": grants_digest,
                    "artifact_digest": artifact_digest,
                    "capability_ids": capability_ids,
                    "controller_source": source,
                    "viewport": normalized_viewport,
                    "presentation_context": self._normalize_presentation_context(
                        presentation_context
                    ),
                }
            )
        except Exception:
            async with self._session_lock:
                self._sessions.pop(session_id, None)
            await connection.close()
            raise
        return binding

    def binding(self, session_id: str) -> WidgetRuntimeBinding:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError("Unknown Widget Runtime session") from exc

    async def close_session(self, session_id: str) -> None:
        async with self._session_lock:
            binding = self._sessions.pop(session_id, None)
        if binding is None:
            return
        try:
            await binding.connection.send_json(
                {"type": "close", "session_id": binding.session_id}
            )
        except Exception:
            pass
        await binding.connection.close()

    def _encoded_size(self, message: dict[str, Any]) -> int:
        try:
            return len(
                json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Widget Runtime message must be JSON serializable") from exc

    @staticmethod
    def _pointer_event(message: dict[str, Any]) -> dict[str, Any]:
        event = message.get("event")
        button = message.get("button", "none")
        if event not in {"mousePressed", "mouseReleased", "mouseMoved"}:
            raise ValueError("Unsupported Widget Runtime pointer event")
        if button not in {"none", "left", "middle", "right", "back", "forward"}:
            raise ValueError("Unsupported Widget Runtime pointer button")
        return {
            "type": "pointer",
            "event": event,
            "x": float(message.get("x", 0)),
            "y": float(message.get("y", 0)),
            "button": button,
            "buttons": int(message.get("buttons", 0)),
            "click_count": max(0, min(int(message.get("click_count", 0)), 3)),
        }

    def _sanitize_input(self, message: dict[str, Any]) -> dict[str, Any]:
        message_type = message.get("type")
        if message_type == "pointer":
            return self._pointer_event(message)
        if message_type == "wheel":
            return {
                "type": "wheel",
                "x": float(message.get("x", 0)),
                "y": float(message.get("y", 0)),
                "delta_x": float(message.get("delta_x", 0)),
                "delta_y": float(message.get("delta_y", 0)),
            }
        if message_type == "key":
            event = message.get("event")
            if event not in {"keyDown", "keyUp", "rawKeyDown"}:
                raise ValueError("Unsupported Widget Runtime key event")
            return {
                "type": "key",
                "event": event,
                "key": str(message.get("key", ""))[:64],
                "code": str(message.get("code", ""))[:64],
                "text": str(message.get("text", ""))[:4096],
                "modifiers": int(message.get("modifiers", 0)),
            }
        if message_type == "text":
            return {"type": "text", "text": str(message.get("text", ""))[:65536]}
        if message_type == "viewport":
            return {"type": "viewport", **self._normalize_viewport(message)}
        if message_type == "visibility":
            return {"type": "visibility", "visible": bool(message.get("visible"))}
        if message_type == "focus":
            return {"type": "focus", "focused": bool(message.get("focused"))}
        if message_type == "presentation_context":
            return {
                "type": "presentation_context",
                **self._normalize_presentation_context(message),
            }
        raise ValueError("Unsupported Widget Runtime input message")

    async def forward_input(self, session_id: str, message: dict[str, Any]) -> None:
        binding = self.binding(session_id)
        if not isinstance(message, dict):
            raise ValueError("Widget Runtime input message must be an object")
        if self._encoded_size(message) > self.limits.max_message_bytes:
            raise ValueError("Widget Runtime message exceeds the configured byte limit")
        event = self._sanitize_input(message)
        await binding.connection.send_json(
            {"type": "input", "session_id": binding.session_id, "event": event}
        )

    @staticmethod
    def _sanitize_rpc_params(params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise ValueError("Widget Runtime RPC params must be an object")
        return {key: value for key, value in params.items() if key not in _IDENTITY_FIELDS}

    async def handle_runtime_message(
        self,
        session_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any] | None:
        binding = self.binding(session_id)
        if not isinstance(message, dict):
            raise ValueError("Widget Runtime message must be an object")
        if self._encoded_size(message) > self.limits.max_message_bytes:
            raise ValueError("Widget Runtime message exceeds the configured byte limit")

        message_type = message.get("type")
        if message_type == "rpc_request":
            request_id = self._require_string(message.get("request_id"), "RPC request ID")
            method = self._require_string(message.get("method"), "RPC method")
            params = {
                **self._sanitize_rpc_params(message.get("params", {})),
                "_runtime_request_id": request_id,
            }
            if self._rpc_handler is None:
                error = {
                    "code": "runtime_rpc_unavailable",
                    "message": "Widget Runtime capability RPC is not configured",
                }
                await binding.connection.send_json(
                    {
                        "type": "rpc_response",
                        "session_id": binding.session_id,
                        "request_id": request_id,
                        "error": error,
                    }
                )
                return None
            try:
                result = self._rpc_handler(binding, method, params)
                if inspect.isawaitable(result):
                    result = await result
                response = {
                    "type": "rpc_response",
                    "session_id": binding.session_id,
                    "request_id": request_id,
                    "result": result,
                }
            except Exception as exc:
                error = exc.to_dict() if hasattr(exc, "to_dict") else {
                    "code": "runtime_rpc_failed",
                    "message": str(exc),
                }
                response = {
                    "type": "rpc_response",
                    "session_id": binding.session_id,
                    "request_id": request_id,
                    "error": error,
                }
            await binding.connection.send_json(response)
            return None

        if message_type == "frame":
            return {
                "type": "frame",
                "format": "jpeg" if message.get("format") != "png" else "png",
                "data": self._require_string(message.get("data"), "frame data"),
                "width": int(message.get("width", 0)),
                "height": int(message.get("height", 0)),
            }
        if message_type == "ready":
            return {"type": "ready"}
        if message_type == "runtime_error":
            error = message.get("error") if isinstance(message.get("error"), dict) else {}
            return {
                "type": "runtime_error",
                "error": {
                    "code": str(error.get("code") or "widget_runtime_failed")[:128],
                    "message": str(error.get("message") or "Widget Runtime failed")[:4096],
                    "classification": str(error.get("classification") or "operator")[:64],
                },
            }
        if message_type == "host_event":
            event = message.get("event")
            if event in {"fullscreen", "minimize"}:
                return {"type": "host_event", "event": event}
            if event == "send_message":
                return {
                    "type": "host_event",
                    "event": "send_message",
                    "text": str(message.get("text") or "")[:16_384],
                }
        raise ValueError("Unsupported Widget Runtime message")

    async def receive_runtime_message(self, session_id: str) -> dict[str, Any]:
        binding = self.binding(session_id)
        receiver = getattr(binding.connection, "receive_json", None)
        if receiver is None:
            raise RuntimeError("Widget Runtime connection does not support reads")
        return await receiver()

    async def send_to_runtime(self, session_id: str, message: dict[str, Any]) -> None:
        binding = self.binding(session_id)
        sanitized = {key: value for key, value in message.items() if key not in _IDENTITY_FIELDS}
        await binding.connection.send_json(
            {"session_id": binding.session_id, **sanitized}
        )
