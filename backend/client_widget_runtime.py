"""Short-lived browser Runtime tickets and server-owned session bindings."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit


CLIENT_WIDGET_RUNTIME_PROTOCOL = "ambient-widget-client-v1"
CLIENT_WIDGET_RUNTIME_PROTOCOL_VERSION = 1
CLIENT_WIDGET_RUNTIME_TICKET_TTL_SECONDS = 30.0
CLIENT_WIDGET_RUNTIME_TICKET_PREFIX = "ticket."
CLIENT_WIDGET_RUNTIME_POLICY_VIOLATION = 4403
CLIENT_WIDGET_RUNTIME_PROTOCOL_ERROR = 4400
CLIENT_WIDGET_RUNTIME_SESSION_LIMIT = 4429

_DEFAULT_FRONTEND_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
_MAX_CONTROLLER_BYTES = 1024 * 1024


class ClientWidgetRuntimeTicketError(ValueError):
    """Raised when a browser Runtime ticket cannot be issued or consumed."""


class ClientWidgetRuntimeSessionLimitError(RuntimeError):
    """Raised when the browser Runtime active-session budget is exhausted."""


def normalize_client_runtime_origin(value: Any) -> str:
    """Return a canonical HTTP(S) origin, rejecting opaque and wildcard origins."""

    if not isinstance(value, str):
        raise ClientWidgetRuntimeTicketError("Client Runtime Origin header is required")
    value = value.strip()
    if not value or value == "null" or value == "*":
        raise ClientWidgetRuntimeTicketError("Client Runtime Origin is not allowed")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ClientWidgetRuntimeTicketError("Client Runtime Origin is invalid") from exc
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ClientWidgetRuntimeTicketError("Client Runtime Origin is invalid")

    hostname = hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if scheme == "http" else 443
    netloc = host if port is None or port == default_port else f"{host}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def allowed_client_runtime_origins() -> frozenset[str]:
    """Read the explicit browser Runtime Origin allowlist."""

    configured = os.getenv("AMBIENT_FRONTEND_ORIGINS")
    values = _DEFAULT_FRONTEND_ORIGINS if configured is None else tuple(configured.split(","))
    allowed: set[str] = set()
    for value in values:
        try:
            allowed.add(normalize_client_runtime_origin(value))
        except ClientWidgetRuntimeTicketError:
            # Invalid entries, including wildcards, never broaden the allowlist.
            continue
    return frozenset(allowed)


def client_runtime_origin(headers: Mapping[str, str]) -> str:
    """Require an allowlisted Origin for a client Runtime HTTP or WS request."""

    origin = normalize_client_runtime_origin(headers.get("origin"))
    if origin not in allowed_client_runtime_origins():
        raise ClientWidgetRuntimeTicketError("Client Runtime Origin is not allowed")
    return origin


def client_runtime_frame_url(origin: str) -> str:
    """Resolve the isolated iframe document URL for a trusted frontend Origin."""

    configured = os.getenv("WIDGET_FRAME_URL")
    if configured is not None and configured.strip():
        value = configured.strip()
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as exc:
            raise ClientWidgetRuntimeTicketError("WIDGET_FRAME_URL is invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ClientWidgetRuntimeTicketError("WIDGET_FRAME_URL is invalid")
        return value

    parsed = urlsplit(normalize_client_runtime_origin(origin))
    hostname = parsed.hostname
    assert hostname is not None
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urlunsplit((parsed.scheme, f"{host}:8001", "/frame.html", "", ""))


@dataclass(frozen=True, slots=True)
class ClientWidgetRuntimeArtifact:
    app_id: str
    manifest_revision: str
    grants_digest: str
    artifact_digest: str
    controller_source: str = field(repr=False, compare=False)
    capability_ids: tuple[str, ...]


def load_client_runtime_artifact(
    app_manager: Any,
    app_id: str,
) -> ClientWidgetRuntimeArtifact:
    """Load and validate the immutable App snapshot captured by a ticket."""

    app = app_manager.get_app_files(app_id)
    if not isinstance(app, dict):
        raise KeyError(f"App '{app_id}' was not found")
    if app.get("id") != app_id:
        raise ClientWidgetRuntimeTicketError(
            "Client Runtime App identity does not match the requested App"
        )
    source = app.get("js")
    revision = app.get("manifest_revision")
    grants_digest = app.get("grants_digest")
    if not isinstance(source, str) or not source:
        raise ClientWidgetRuntimeTicketError(
            "App Controller is required for a client Runtime session"
        )
    source_bytes = source.encode("utf-8")
    if len(source_bytes) > _MAX_CONTROLLER_BYTES:
        raise ClientWidgetRuntimeTicketError(
            "App Controller exceeds the client Runtime byte limit"
        )
    if not isinstance(revision, str) or not revision:
        raise ClientWidgetRuntimeTicketError(
            "App manifest revision is required for a client Runtime session"
        )
    if not isinstance(grants_digest, str) or not grants_digest:
        raise ClientWidgetRuntimeTicketError(
            "App grants digest is required for a client Runtime session"
        )
    capabilities = app.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, list) else []
    capability_ids = tuple(
        sorted(
            {
                item["id"]
                for item in capabilities
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        )
    )
    return ClientWidgetRuntimeArtifact(
        app_id=app_id,
        manifest_revision=revision,
        grants_digest=grants_digest,
        artifact_digest=hashlib.sha256(source_bytes).hexdigest(),
        controller_source=source,
        capability_ids=capability_ids,
    )


@dataclass(frozen=True, slots=True)
class ClientWidgetRuntimeTicket:
    token: str = field(repr=False)
    origin: str
    artifact: ClientWidgetRuntimeArtifact
    expires_at: datetime
    expires_monotonic: float = field(repr=False)


@dataclass(frozen=True, slots=True)
class ClientWidgetRuntimeBinding:
    session_id: str
    app_id: str
    manifest_revision: str
    grants_digest: str
    artifact_digest: str
    connection: Any = field(repr=False, compare=False)


class ClientWidgetRuntimeTicketStore:
    """In-memory, bounded, atomic single-use ticket registry."""

    def __init__(
        self,
        app_manager: Any,
        *,
        ttl_seconds: float = CLIENT_WIDGET_RUNTIME_TICKET_TTL_SECONDS,
        max_pending: int = 256,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Client Runtime ticket TTL must be positive")
        if max_pending <= 0:
            raise ValueError("Client Runtime pending ticket limit must be positive")
        self.app_manager = app_manager
        self.ttl_seconds = float(ttl_seconds)
        self.max_pending = max_pending
        self._monotonic = monotonic
        self._utcnow = utcnow or (lambda: datetime.now(UTC))
        self._tickets: dict[str, ClientWidgetRuntimeTicket] = {}
        self._lock = threading.Lock()

    def _discard_expired_locked(self, now: float) -> None:
        for token, ticket in list(self._tickets.items()):
            if ticket.expires_monotonic <= now:
                self._tickets.pop(token, None)

    def issue(self, app_id: str, origin: str) -> ClientWidgetRuntimeTicket:
        artifact = load_client_runtime_artifact(self.app_manager, app_id)
        now = self._monotonic()
        ticket = ClientWidgetRuntimeTicket(
            token=secrets.token_urlsafe(32),
            origin=normalize_client_runtime_origin(origin),
            artifact=artifact,
            expires_at=self._utcnow() + timedelta(seconds=self.ttl_seconds),
            expires_monotonic=now + self.ttl_seconds,
        )
        with self._lock:
            self._discard_expired_locked(now)
            if len(self._tickets) >= self.max_pending:
                oldest = min(
                    self._tickets,
                    key=lambda token: self._tickets[token].expires_monotonic,
                )
                self._tickets.pop(oldest, None)
            self._tickets[ticket.token] = ticket
        return ticket

    def consume(
        self,
        token: str,
        *,
        app_id: str,
        origin: str,
    ) -> ClientWidgetRuntimeTicket:
        normalized_origin = normalize_client_runtime_origin(origin)
        now = self._monotonic()
        with self._lock:
            ticket = self._tickets.get(token)
            if ticket is None:
                raise ClientWidgetRuntimeTicketError(
                    "Client Runtime ticket is invalid"
                )
            if ticket.expires_monotonic <= now:
                self._tickets.pop(token, None)
                raise ClientWidgetRuntimeTicketError(
                    "Client Runtime ticket has expired"
                )
            if ticket.artifact.app_id != app_id or ticket.origin != normalized_origin:
                raise ClientWidgetRuntimeTicketError(
                    "Client Runtime ticket binding does not match"
                )
            # The successful comparison and removal happen under one lock, so
            # concurrent handshakes cannot both consume the same ticket.
            self._tickets.pop(token, None)

        current = load_client_runtime_artifact(self.app_manager, app_id)
        expected = ticket.artifact
        if (
            current.app_id != expected.app_id
            or current.manifest_revision != expected.manifest_revision
            or current.grants_digest != expected.grants_digest
            or current.artifact_digest != expected.artifact_digest
        ):
            raise ClientWidgetRuntimeTicketError(
                "Client Runtime App snapshot changed after ticket issuance"
            )
        return ticket

    def clear(self) -> None:
        with self._lock:
            self._tickets.clear()


class ClientWidgetRuntimeSessionStore:
    """Tracks active browser sessions without accepting client-supplied identity."""

    def __init__(self, *, max_sessions: int = 16) -> None:
        if max_sessions <= 0:
            raise ValueError("Client Runtime session limit must be positive")
        self.max_sessions = max_sessions
        self._sessions: dict[str, ClientWidgetRuntimeBinding] = {}
        self._lock = threading.Lock()

    def open(
        self,
        ticket: ClientWidgetRuntimeTicket,
        connection: Any,
    ) -> ClientWidgetRuntimeBinding:
        artifact = ticket.artifact
        binding = ClientWidgetRuntimeBinding(
            session_id=secrets.token_urlsafe(24),
            app_id=artifact.app_id,
            manifest_revision=artifact.manifest_revision,
            grants_digest=artifact.grants_digest,
            artifact_digest=artifact.artifact_digest,
            connection=connection,
        )
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise ClientWidgetRuntimeSessionLimitError(
                    "Client Runtime session limit reached"
                )
            self._sessions[binding.session_id] = binding
        return binding

    def close(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def binding(self, session_id: str) -> ClientWidgetRuntimeBinding | None:
        with self._lock:
            return self._sessions.get(session_id)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)


class LockedClientWidgetRuntimeConnection:
    """Serialize server sends that can originate from RPC and subscription tasks."""

    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self._send_lock = asyncio.Lock()

    async def send_json(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(message)

    async def close(self, *, code: int = 1000) -> None:
        async with self._send_lock:
            await self.websocket.close(code=code)
