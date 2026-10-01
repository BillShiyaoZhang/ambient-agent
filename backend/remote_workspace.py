"""Opt-in outbound workspace transport; the local workspace remains authoritative."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import math
import os
import random
import re
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus, WebSocketException

MAX_HTTP_BYTES = 2 * 1024 * 1024
MAX_WS_BYTES = 256 * 1024
MAX_TUNNEL_BYTES = 3 * 1024 * 1024
MAX_CONNECTIONS = 16
SCOPES = frozenset({"workspace.control", "workspace.manage"})
IDENTITY_FIELDS = ("node_id", "account_id", "grant_id", "scopes", "workspace_origin")
PUBLIC_STATE_FIELDS = (
    "status",
    "node_id",
    "name",
    "gateway_url",
    "portal_url",
    "scopes",
    "expires_at",
    "account_id",
    "account_label",
    "grant_id",
    "pairing_code",
    "pairing_expires_at",
    "workspace_origin",
    "last_error",
)
PRIVATE_STATE_FIELDS = frozenset((*PUBLIC_STATE_FIELDS, "connector_token", "approved"))
MAX_RETRY_AFTER = 86400
UPSTREAMS = {
    "frontend": "http://127.0.0.1:5173",
    "backend": "http://127.0.0.1:8000",
    "frame": "http://127.0.0.1:8001",
}
DOCKER_UPSTREAMS = {
    "frontend": "http://frontend:5173",
    "backend": "http://127.0.0.1:8000",
    "frame": "http://widget-frame:8001",
}
FRAME_PATHS = frozenset(
    {
        "/frame.html",
        "/frame_shell.css",
        "/frame_shell.mjs",
        "/controller_facade.mjs",
        "/presentation_context.mjs",
        "/vendor/babel.min.js",
        "/vendor/htm-preact.js",
    }
)
REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-language",
        "content-type",
        "if-none-match",
        "if-modified-since",
    }
)
RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "cache-control",
        "etag",
        "last-modified",
        "vary",
        "content-security-policy",
        "permissions-policy",
        "referrer-policy",
        "x-content-type-options",
        "access-control-allow-origin",
    }
)
API_PATH = re.compile(
    r"^/api/(?:sessions(?:/[^/]+(?:/(?:messages|language|model))?)?|canvas|"
    r"runs(?:/[^/]+(?:/(?:cancel|retry|reconcile))?)?|run-interactions/[^/]+/resolve|"
    r"runtimes(?:/[^/]+/stop)?|app-store(?:/layout)?|app-types|chat/commands|"
    r"llm/(?:catalog|providers(?:/[^/]+(?:/(?:discover-models|test))?)?|settings)|"
    r"coding-agents(?:/settings|/[^/]+(?:/(?:model|install|auth|models|operations/[^/]+))?)?|"
    r"skill-market(?:/sources/[^/]+)?|skills(?:/install|/[^/]+(?:/authorization)?)?|"
    r"capabilities/[^/]+(?:/ui)?|apps(?:/[^/]+(?:/(?:client-runtime-ticket|diagnostics|"
    r"files/(?:read|list|write|delete)|graph/mutate|data-sources/[^/]+/request))?)?|"
    r"audit-logs|data-map|graph/(?:explorer|mutate|query))$"
)
WS_PATH = re.compile(r"^/ws/(?:chat|runs|run-live|widgets/[^/]+/(?:client-runtime|runtime))$")


class RemoteWorkspaceDenied(ValueError):
    """A request is outside the locally approved workspace grant."""


class RemoteWorkspaceGatewayError(ValueError):
    """A safe control-plane failure; never retain Gateway bodies or request secrets."""

    def __init__(self, status_code: int, *, retry_after: int | None = None):
        self.status_code = status_code if status_code in {401, 403, 409, 410, 422, 429} else 502
        self.retry_after = retry_after if self.status_code == 429 else None
        descriptions = {
            401: "The saved device credential is no longer valid. Generate a new enrollment token in the portal.",
            403: "The device authorization ended. Generate a new enrollment token in the portal.",
            409: "The enrollment token expired, was already used, or the pairing state changed. Generate a new token in the portal.",
            410: "The account or workspace authorization ended. Sign in and authorize a new connection.",
            422: "Check the enrollment token and connection settings.",
            429: "The workspace gateway is temporarily busy or its connection quota is reached. Wait before trying again.",
            502: "Workspace gateway is unavailable.",
        }
        super().__init__(descriptions[self.status_code])


def _retry_after(value: str | None, now: datetime) -> int | None:
    if not value or len(value) > 128:
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,6}", value):
        delay = int(value)
    else:
        try:
            instant = parsedate_to_datetime(value)
            if instant.tzinfo is None:
                return None
            delay = math.ceil((instant - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return max(1, delay) if 0 <= delay <= MAX_RETRY_AFTER else None


def _expiry(value: Any) -> datetime:
    if not isinstance(value, str):
        raise RemoteWorkspaceDenied("Grant expiry is missing")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemoteWorkspaceDenied("Grant expiry is invalid") from exc
    if result.tzinfo is None:
        raise RemoteWorkspaceDenied("Grant expiry requires a timezone")
    return result


def normalize_gateway_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    _ = parsed.port
    try:
        loopback = parsed.hostname == "localhost" or ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost" or bool(parsed.hostname and parsed.hostname.endswith(".localhost"))
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or (parsed.scheme == "http" and not loopback)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTPS origin; HTTP is allowed only for loopback development")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _safe_path(path: Any) -> str | None:
    if not isinstance(path, str) or len(path) > 4096 or not path.startswith("/") or path.startswith("//"):
        return None
    if any(ord(char) < 32 for char in path) or "\\" in path or "#" in path:
        return None
    parsed = urlsplit(path)
    decoded = unquote(parsed.path)
    if re.search(r"%2f|%5c", parsed.path, re.I):
        return None
    if "%" in decoded or "\\" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
        return None
    if parsed.netloc or parsed.scheme:
        return None
    return decoded


def allowed_route(service: str, method: str, path: str, scopes: list[str], *, websocket: bool = False) -> bool:
    pathname = _safe_path(path)
    if pathname is None or "workspace.control" not in scopes:
        return False
    method = method.upper()
    if service == "frame":
        return not websocket and method in {"GET", "HEAD"} and pathname in FRAME_PATHS
    if service == "frontend":
        return (
            not websocket
            and method in {"GET", "HEAD"}
            and (
                pathname == "/"
                or pathname == "/index.html"
                or pathname == "/favicon.ico"
                or pathname == "/vite.svg"
                or pathname.startswith("/assets/")
            )
        )
    if service != "backend":
        return False
    if websocket:
        return method == "GET" and bool(WS_PATH.fullmatch(pathname))
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"} or not API_PATH.fullmatch(pathname):
        return False
    managed = pathname.startswith(
        ("/api/llm/", "/api/coding-agents", "/api/skills", "/api/skill-market", "/api/capabilities", "/api/runtimes")
    )
    managed = managed or pathname.endswith("/reconcile")
    return not (managed and method not in {"GET", "HEAD"} and "workspace.manage" not in scopes)


def _decode_body(value: Any, limit: int) -> bytes:
    if not isinstance(value, str) or len(value) > ((limit + 2) // 3) * 4:
        raise OverflowError("Payload exceeds the transport limit")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RemoteWorkspaceDenied("Payload must be base64") from exc
    if len(result) > limit:
        raise OverflowError("Payload exceeds the transport limit")
    return result


def _headers(values: Any, allowlist: frozenset[str]) -> list[tuple[str, str]]:
    if not isinstance(values, list) or len(values) > 64:
        raise RemoteWorkspaceDenied("Headers are invalid")
    result = []
    for pair in values:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or not all(isinstance(item, str) for item in pair):
            raise RemoteWorkspaceDenied("Headers are invalid")
        key, value = pair
        if len(key) > 128 or len(value) > 8192 or any(char in key + value for char in "\r\n\x00"):
            raise RemoteWorkspaceDenied("Headers are invalid")
        if key.lower() in allowlist:
            result.append((key, value))
    return result


class RemoteWorkspaceNodeStore:
    """Private persisted grant; public status deliberately omits device credentials."""

    def __init__(self, workspace_dir: str | Path, *, now: Callable[[], datetime] | None = None):
        self.path = Path(workspace_dir) / ".ambient" / "remote-workspace" / "node.json"
        self.now = now or (lambda: datetime.now(UTC))
        self._state: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._state = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self._state, dict):
                    raise ValueError("Invalid state")
            except (ValueError, OSError):
                self._state = {"status": "invalid", "last_error": "Saved connection cannot be read safely"}

    def save(self, state: dict[str, Any]) -> None:
        state = {key: value for key, value in state.items() if key in PRIVATE_STATE_FIELDS}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix=".node-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(filename, 0o600)
            os.replace(filename, self.path)
            self._state = dict(state)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def status(self) -> dict[str, Any]:
        result = {key: self._state[key] for key in PUBLIC_STATE_FIELDS if key in self._state}
        result.setdefault("status", "disconnected")
        result.setdefault("scopes", [])
        if result["status"] in {"pending", "claimed", "paired"}:
            with contextlib.suppress(RemoteWorkspaceDenied):
                deadlines = [_expiry(result.get("expires_at"))]
                if result["status"] == "pending" and result.get("pairing_expires_at"):
                    deadlines.append(_expiry(result["pairing_expires_at"]))
                if min(deadlines) <= self.now():
                    result["status"] = "expired"
        return result

    def identity(self) -> dict[str, Any]:
        state = self._state
        if state.get("status") != "paired" or state.get("approved") is not True:
            raise RemoteWorkspaceDenied("Workspace is not locally approved")
        if _expiry(state.get("expires_at")) <= self.now():
            raise RemoteWorkspaceDenied("Workspace grant expired")
        if not all(isinstance(state.get(key), str) and state[key] for key in IDENTITY_FIELDS if key != "scopes"):
            raise RemoteWorkspaceDenied("Workspace identity is incomplete")
        scopes = state.get("scopes")
        if not isinstance(scopes, list) or "workspace.control" not in scopes or not set(scopes) <= SCOPES:
            raise RemoteWorkspaceDenied("Workspace scope is invalid")
        normalize_gateway_url(state["workspace_origin"])
        return {key: state[key] for key in IDENTITY_FIELDS}

    def authorize(self, message: dict[str, Any]) -> dict[str, Any]:
        identity = self.identity()
        if any(message.get(key) != identity[key] for key in IDENTITY_FIELDS):
            raise RemoteWorkspaceDenied("Workspace identity does not match the local grant")
        return identity

    def revoke(self) -> None:
        self.save({**self.status(), "status": "revoked", "approved": False, "pairing_code": None})


class RemoteWorkspaceConnector:
    """Bounded bridge to fixed loopback or local Docker services, disabled until approved."""

    def __init__(
        self,
        store: RemoteWorkspaceNodeStore,
        *,
        local_http: httpx.AsyncClient | None = None,
        gateway_http: httpx.AsyncClient | None = None,
        local_ws_connect: Callable[..., Awaitable[Any]] | None = None,
        tunnel_connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[], float] | None = None,
    ):
        upstream_mode = os.getenv("AMBIENT_REMOTE_UPSTREAM_MODE", "loopback")
        if upstream_mode not in {"loopback", "docker"}:
            raise ValueError("AMBIENT_REMOTE_UPSTREAM_MODE must be loopback or docker")
        self.upstreams = dict(DOCKER_UPSTREAMS if upstream_mode == "docker" else UPSTREAMS)
        self.store = store
        self.local_http = local_http
        self.gateway_http = gateway_http
        self.local_ws_connect = local_ws_connect or connect
        self.tunnel_connect = tunnel_connect or connect
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or random.random
        self._cooldown_until: datetime | None = None
        self._failure_count = 0
        self.online = False
        self.last_error: str | None = None
        self._runner: asyncio.Task | None = None
        self._tunnel: Any = None
        self._ws: dict[str, tuple[Any, asyncio.Task, dict[str, Any]]] = {}
        self._requests: dict[str, asyncio.Task] = {}
        self._mutation = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        state = self.store.status()
        return {
            **state,
            "online": self.online,
            "last_error": self.last_error or state.get("last_error"),
            "retry_after": self._cooldown_remaining(),
        }

    def _cooldown_remaining(self) -> int:
        return (
            max(0, math.ceil((self._cooldown_until - self.store.now()).total_seconds())) if self._cooldown_until else 0
        )

    async def _terminate(self, status: str, error: str | None = None) -> None:
        self.store.save({**self.store.status(), "status": status, "approved": False, "pairing_code": None})
        self.last_error = error
        await self.stop()

    async def _expire_if_due(self) -> bool:
        state = self.store._state
        if state.get("status") not in {"pending", "claimed", "paired"}:
            return False
        try:
            deadlines = [_expiry(state.get("expires_at"))]
            if state.get("status") == "pending" and state.get("pairing_expires_at"):
                deadlines.append(_expiry(state["pairing_expires_at"]))
            if min(deadlines) > self.store.now():
                return False
        except RemoteWorkspaceDenied:
            await self._terminate("revoked", "Saved workspace authorization is invalid. Authorize a new connection.")
            return True
        await self._terminate(
            "expired", "Workspace authorization expired. Generate a new enrollment token in the portal."
        )
        return True

    async def _gateway(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        if delay := self._cooldown_remaining():
            raise RemoteWorkspaceGatewayError(429, retry_after=delay)
        state = self.store._state
        headers = {"Authorization": "Bearer " + state["connector_token"]} if state.get("connector_token") else {}
        own = self.gateway_http is None
        client = self.gateway_http or httpx.AsyncClient(timeout=30, trust_env=False)
        try:
            response = await client.request(method, state["gateway_url"] + path, headers=headers, **kwargs)
            if not response.is_success:
                if self.store._state is not state:
                    raise RemoteWorkspaceDenied("Workspace connection changed while the request was pending")
                retry = (
                    _retry_after(response.headers.get("retry-after"), self.store.now())
                    if response.status_code == 429
                    else None
                )
                if retry:
                    self._cooldown_until = self.store.now() + timedelta(seconds=retry)
                error = RemoteWorkspaceGatewayError(response.status_code, retry_after=retry)
                if response.status_code == 410 or (headers and response.status_code in {401, 403}):
                    await self._terminate("revoked", str(error))
                raise error
            if len(response.content) > 64 * 1024:
                raise RemoteWorkspaceDenied("Gateway response exceeds the limit")
            result = response.json()
            if not isinstance(result, dict):
                raise RemoteWorkspaceDenied("Gateway response is invalid")
            return result
        finally:
            if own:
                await client.aclose()

    async def pair(self, data: dict[str, Any]) -> dict[str, Any]:
        async with self._mutation:
            token = data.get("enrollment_token")
            if not isinstance(token, str) or not 20 <= len(token) <= 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
                raise RemoteWorkspaceDenied("A valid one-time enrollment token from the portal is required")
            if delay := self._cooldown_remaining():
                raise RemoteWorkspaceGatewayError(429, retry_after=delay)
            gateway_url = normalize_gateway_url(data["gateway_url"])
            portal_url = normalize_gateway_url(data["portal_url"])
            scopes = data["scopes"]
            if not isinstance(scopes, list) or "workspace.control" not in scopes or not set(scopes) <= SCOPES:
                raise ValueError("Select workspace.control and optionally workspace.manage")
            if self.store.status()["status"] not in {"disconnected", "revoked", "expired", "invalid"}:
                raise ValueError("Revoke the existing connection before pairing another account")
            await self.stop()
            self.store.save({"gateway_url": gateway_url, "portal_url": portal_url, "status": "disconnected"})
            remote = await self._gateway(
                "POST",
                "/v1/connector/pairings",
                json={
                    "enrollment_token": token,
                    "name": data["name"],
                    "scopes": scopes,
                    "expires_in": data["expires_in"],
                },
            )
            for key in (
                "node_id",
                "connector_token",
                "pairing_code",
                "pairing_expires_at",
                "workspace_origin",
                "expires_at",
            ):
                if not isinstance(remote.get(key), str) or not remote[key]:
                    raise RemoteWorkspaceDenied("Pairing response is incomplete")
            normalize_gateway_url(remote["workspace_origin"])
            expiry = _expiry(remote["expires_at"])
            if expiry <= self.store.now() or expiry > self.store.now() + timedelta(seconds=data["expires_in"] + 10):
                raise RemoteWorkspaceDenied("Gateway expiry exceeds the locally requested duration")
            _expiry(remote["pairing_expires_at"])
            self.store.save(
                {
                    **{
                        key: remote[key]
                        for key in (
                            "node_id",
                            "connector_token",
                            "pairing_code",
                            "pairing_expires_at",
                            "workspace_origin",
                            "expires_at",
                        )
                    },
                    "gateway_url": gateway_url,
                    "portal_url": portal_url,
                    "name": data["name"],
                    "scopes": scopes,
                    "status": "pending",
                    "approved": False,
                }
            )
            await self.start()
            return self.status()

    async def refresh(self) -> dict[str, Any]:
        if await self._expire_if_due():
            return self.status()
        state = self.store._state
        if not state.get("connector_token") or state.get("status") in {"revoked", "invalid"}:
            return self.status()
        try:
            remote = await self._gateway("GET", "/v1/connector/state")
        except RemoteWorkspaceDenied:
            if self.store._state is not state:
                return self.status()
            raise
        if self.store._state is not state:
            return self.status()
        if remote.get("node_id") != state.get("node_id") or remote.get("scopes") != state.get("scopes"):
            raise RemoteWorkspaceDenied("Gateway changed node or scopes")
        if remote.get("status") in {"revoked", "expired"}:
            await self._terminate(remote["status"])
            return self.status()
        if state.get("approved"):
            self.store.authorize(remote)
            if remote.get("expires_at") != state.get("expires_at"):
                raise RemoteWorkspaceDenied("Gateway changed the approved expiry")
        elif remote.get("status") == "claimed":
            if state.get("account_id") and remote.get("account_id") != state["account_id"]:
                raise RemoteWorkspaceDenied("Claimed account cannot be replaced")
            if not remote.get("account_id") or not remote.get("grant_id"):
                raise RemoteWorkspaceDenied("Claimed identity is incomplete")
            if remote.get("workspace_origin") != state.get("workspace_origin"):
                raise RemoteWorkspaceDenied("Gateway changed workspace origin")
            if _expiry(remote.get("expires_at")) > _expiry(state.get("expires_at")):
                raise RemoteWorkspaceDenied("Gateway extended the requested grant")
            self.store.save(
                {
                    **state,
                    **{
                        key: remote[key]
                        for key in ("account_id", "account_label", "grant_id", "expires_at")
                        if key in remote
                    },
                    "status": "claimed",
                }
            )
        self.last_error = None
        return self.status()

    async def approve(self, account_id: str, grant_id: str) -> dict[str, Any]:
        async with self._mutation:
            await self.refresh()
            state = self.store._state
            if (
                state.get("status") != "claimed"
                or account_id != state.get("account_id")
                or grant_id != state.get("grant_id")
            ):
                raise RemoteWorkspaceDenied("The claimed account and grant must match local confirmation")
            if _expiry(state.get("expires_at")) <= self.store.now():
                raise RemoteWorkspaceDenied("Workspace grant expired")
            remote = await self._gateway(
                "POST", "/v1/connector/approve", json={"account_id": account_id, "grant_id": grant_id}
            )
            for key in IDENTITY_FIELDS:
                if remote.get(key) != state.get(key):
                    raise RemoteWorkspaceDenied("Approved identity differs from local confirmation")
            if remote.get("status") != "paired" or remote.get("expires_at") != state.get("expires_at"):
                raise RemoteWorkspaceDenied("Approved grant differs from local confirmation")
            self.store.save({**state, "status": "paired", "approved": True, "pairing_code": None})
            await self.start()
            return self.status()

    async def revoke(self) -> dict[str, Any]:
        async with self._mutation:
            previous = dict(self.store._state)
            self.store.revoke()
            await self.stop()
            if previous.get("connector_token"):
                if self._cooldown_remaining():
                    self.last_error = "Local access revoked; cloud notification postponed by gateway cooldown"
                    return self.status()
                own = self.gateway_http is None
                client = self.gateway_http or httpx.AsyncClient(timeout=10, trust_env=False)
                try:
                    response = await client.post(
                        previous["gateway_url"] + "/v1/connector/revoke",
                        headers={"Authorization": "Bearer " + previous["connector_token"]},
                    )
                    response.raise_for_status()
                except (httpx.HTTPError, OSError):
                    self.last_error = "Local access revoked; cloud notification unavailable"
                finally:
                    if own:
                        await client.aclose()
            return self.status()

    async def start(self) -> None:
        if await self._expire_if_due():
            return
        if self._runner is None or self._runner.done():
            if self.store._state.get("connector_token") and self.store.status()["status"] in {
                "pending",
                "claimed",
                "paired",
            }:
                self._runner = asyncio.create_task(self._run())

    async def stop(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None and runner is not asyncio.current_task():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
            except Exception:
                self.last_error = "Workspace transport ended unexpectedly"
        await self._close_connections()

    async def _close_connections(self) -> None:
        self.online = False
        for task in self._requests.values():
            task.cancel()
        pending = list(self._requests.values())
        self._requests.clear()
        for request_id in list(self._ws):
            await self._close_ws(request_id)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._tunnel is not None:
            tunnel, self._tunnel = self._tunnel, None
            with contextlib.suppress(Exception):
                await tunnel.close()

    async def _run(self) -> None:
        self._failure_count = 0
        try:
            while self.store._state.get("connector_token"):
                try:
                    await self.refresh()
                    if self.store.status()["status"] in {"revoked", "expired", "invalid"}:
                        return
                    if self.store._state.get("approved"):
                        await self._serve_tunnel()
                        if not self.store._state.get("connector_token"):
                            return
                        raise OSError("Workspace tunnel ended")
                    else:
                        self._failure_count = 0
                        await self._sleep(2)
                except InvalidStatus as exc:
                    status = exc.response.status_code
                    if status in {401, 410}:
                        await self._terminate("revoked", str(RemoteWorkspaceGatewayError(status)))
                        return
                    if status == 403:
                        # A pre-accept capacity close also appears as 403. Device HTTP state is authoritative.
                        try:
                            await self.refresh()
                        except (ValueError, httpx.HTTPError, OSError, TimeoutError):
                            pass
                        if not self.store._state.get("connector_token") or await self._expire_if_due():
                            return
                    retry = (
                        _retry_after(exc.response.headers.get("retry-after"), self.store.now())
                        if status == 429
                        else None
                    )
                    if retry:
                        self._cooldown_until = self.store.now() + timedelta(seconds=retry)
                    self.last_error = "Workspace gateway is unavailable or rejected the connection"
                    await self._close_connections()
                    self._failure_count += 1
                    await self._retry_wait(self._failure_count)
                except (OSError, ValueError, httpx.HTTPError, TimeoutError, WebSocketException):
                    if not self.store._state.get("connector_token") or await self._expire_if_due():
                        return
                    self.last_error = "Workspace gateway is unavailable or rejected the connection"
                    await self._close_connections()
                    self._failure_count += 1
                    await self._retry_wait(self._failure_count)
        finally:
            await self._close_connections()

    async def _retry_wait(self, failures: int) -> None:
        base = min(60, 3 * 2 ** min(failures - 1, 5))
        delay = max(base + base * 0.25 * self._jitter(), self._cooldown_remaining())
        state = self.store._state
        deadlines = [_expiry(state["expires_at"])]
        if state.get("status") == "pending" and state.get("pairing_expires_at"):
            deadlines.append(_expiry(state["pairing_expires_at"]))
        remaining = max(0, (min(deadlines) - self.store.now()).total_seconds())
        await self._sleep(min(delay, remaining))

    async def _serve_tunnel(self) -> None:
        state = self.store._state
        identity = self.store.identity()
        url = state["gateway_url"].replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        async with self.tunnel_connect(
            url + "/v1/connector/tunnel",
            additional_headers={"Authorization": "Bearer " + state["connector_token"]},
            max_size=MAX_TUNNEL_BYTES,
            max_queue=16,
            open_timeout=15,
            ping_interval=20,
            proxy=None,
        ) as tunnel:
            self._tunnel = tunnel
            hello = json.loads(await asyncio.wait_for(tunnel.recv(), timeout=15))
            self.store.authorize(hello)
            if hello.get("type") != "hello":
                raise RemoteWorkspaceDenied("Tunnel hello is missing")
            self.online = True
            self.last_error = None
            self._failure_count = 0
            send_lock = asyncio.Lock()

            async def send(message):
                async with send_lock:
                    await tunnel.send(json.dumps(message, separators=(",", ":")))

            async def heartbeat():
                while True:
                    await self._sleep(
                        min(
                            5, max(0, (_expiry(self.store._state.get("expires_at")) - self.store.now()).total_seconds())
                        )
                    )
                    self.store.authorize(identity)
                    await send({"type": "ping"})

            timer = asyncio.create_task(heartbeat())
            receive: asyncio.Task | None = None
            try:
                while True:
                    receive = asyncio.create_task(tunnel.recv())
                    done, _ = await asyncio.wait({receive, timer}, return_when=asyncio.FIRST_COMPLETED)
                    if timer in done:
                        receive.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await receive
                        await timer
                        return
                    raw = receive.result()
                    if not isinstance(raw, str) or len(raw.encode()) > MAX_TUNNEL_BYTES:
                        raise RemoteWorkspaceDenied("Tunnel message exceeds the limit")
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise RemoteWorkspaceDenied("Tunnel message is invalid")
                    if message.get("type") == "revoked":
                        await self._terminate("revoked")
                        return
                    await self.handle_message(message, send)
            finally:
                if receive is not None and not receive.done():
                    receive.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await receive
                timer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await timer
                await self._close_connections()

    @staticmethod
    def _origin() -> str:
        from backend.client_widget_runtime import allowed_client_runtime_origins

        for candidate in ("http://localhost:5173", "http://127.0.0.1:5173"):
            if candidate in allowed_client_runtime_origins():
                return candidate
        raise RemoteWorkspaceDenied("A trusted local frontend origin must remain configured")

    async def handle_http(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = message.get("id")
        try:
            identity = self.store.authorize(message)
            service, method, path = message.get("service"), message.get("method", ""), message.get("path", "")
            if not isinstance(method, str) or not allowed_route(service, method, path, identity["scopes"]):
                raise RemoteWorkspaceDenied("Workspace route or management scope denied")
            body = _decode_body(message.get("body", ""), MAX_HTTP_BYTES)
            headers = _headers(message.get("headers", []), REQUEST_HEADERS)
            frame_asset = service == "frame"
            if frame_asset:
                headers = [(key, value) for key, value in headers if key.lower() != "accept-encoding"]
                headers.append(("Accept-Encoding", "gzip"))
            headers.append(("Origin", self._origin()))
            own = self.local_http is None
            client = self.local_http or httpx.AsyncClient(timeout=30, trust_env=False)
            try:
                async with client.stream(
                    method, self.upstreams[service] + path, headers=headers, content=body, follow_redirects=False
                ) as response:
                    encoding = response.headers.get("content-encoding", "").lower()
                    if frame_asset and encoding not in {"", "identity", "gzip"}:
                        raise ValueError("Fixed frame asset returned unsupported encoding")
                    compressed_frame = frame_asset and encoding == "gzip"
                    chunks = response.aiter_raw() if compressed_frame else response.aiter_bytes()
                    content = bytearray()
                    async for chunk in chunks:
                        if len(content) + len(chunk) > MAX_HTTP_BYTES:
                            raise OverflowError("Upstream response exceeds the transport limit")
                        content.extend(chunk)
                    self.store.authorize(message)
                    if (
                        service == "backend"
                        and _safe_path(path).endswith("/client-runtime-ticket")
                        and response.status_code == 200
                    ):
                        payload = json.loads(content)
                        payload["frame_url"] = identity["workspace_origin"] + "/_ambient/frame.html"
                        content = json.dumps(payload).encode()
                    return {
                        "type": "http.response",
                        "id": request_id,
                        "status": response.status_code,
                        "headers": _headers(
                            list(response.headers.multi_items()),
                            RESPONSE_HEADERS | {"content-encoding"} if compressed_frame else RESPONSE_HEADERS,
                        ),
                        "body": base64.b64encode(content).decode(),
                    }
            finally:
                if own:
                    await client.aclose()
        except (RemoteWorkspaceDenied, OverflowError, httpx.HTTPError, OSError, ValueError) as exc:
            status = 413 if isinstance(exc, OverflowError) else 403 if isinstance(exc, RemoteWorkspaceDenied) else 502
            return {
                "type": "http.response",
                "id": request_id,
                "status": status,
                "headers": [["content-type", "application/json"], ["cache-control", "no-store"]],
                "body": base64.b64encode(
                    json.dumps(
                        {
                            "detail": "Remote workspace request denied"
                            if status == 403
                            else "Workspace upstream unavailable or payload too large"
                        }
                    ).encode()
                ).decode(),
            }

    async def handle_message(self, message: dict[str, Any], send: Callable[..., Awaitable[None]]) -> None:
        kind, request_id = message.get("type"), message.get("id")
        if kind in {"ping", "pong"}:
            self.store.identity()
            if kind == "ping":
                await send({"type": "pong"})
            return
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise RemoteWorkspaceDenied("Request identity is invalid")
        if kind == "http.request":
            if request_id in self._requests or len(self._requests) >= MAX_CONNECTIONS:
                await send({"type": "http.response", "id": request_id, "status": 429, "headers": [], "body": ""})
                return

            async def respond():
                try:
                    await send(await asyncio.wait_for(self.handle_http(message), timeout=30))
                except TimeoutError:
                    await send({"type": "http.response", "id": request_id, "status": 504, "headers": [], "body": ""})
                finally:
                    self._requests.pop(request_id, None)

            self._requests[request_id] = asyncio.create_task(respond())
        elif kind == "ws.open":
            await self._open_ws(message, send)
        elif kind == "ws.data":
            entry = self._ws.get(request_id)
            if entry is None:
                await send(
                    {"type": "ws.close", "id": request_id, "code": 4403, "reason": "Workspace session unavailable"}
                )
                return
            try:
                self.store.authorize(entry[2])
                data = message.get("data")
                if message.get("kind") == "bytes":
                    data = _decode_body(data, MAX_WS_BYTES)
                elif message.get("kind") != "text" or not isinstance(data, str) or len(data.encode()) > MAX_WS_BYTES:
                    raise RemoteWorkspaceDenied("WebSocket frame is invalid")
                await entry[0].send(data)
            except (RemoteWorkspaceDenied, OverflowError):
                await self._close_ws(request_id)
                await send({"type": "ws.close", "id": request_id, "code": 4403, "reason": "Workspace grant denied"})
        elif kind == "ws.close":
            await self._close_ws(request_id)
        else:
            raise RemoteWorkspaceDenied("Unknown tunnel message")

    async def _open_ws(self, message, send) -> None:
        request_id = message["id"]
        try:
            identity = self.store.authorize(message)
            if request_id in self._ws or len(self._ws) >= MAX_CONNECTIONS:
                raise RemoteWorkspaceDenied("WebSocket session limit exceeded")
            if not allowed_route(
                message.get("service"), "GET", message.get("path", ""), identity["scopes"], websocket=True
            ):
                raise RemoteWorkspaceDenied("WebSocket route denied")
            protocols = message.get("subprotocols", [])
            if (
                not isinstance(protocols, list)
                or len(protocols) > 8
                or any(
                    not isinstance(value, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,256}", value)
                    for value in protocols
                )
            ):
                raise RemoteWorkspaceDenied("WebSocket subprotocols are invalid")
            url = self.upstreams["backend"].replace("http://", "ws://", 1) + message["path"]
            socket = await self.local_ws_connect(
                url,
                origin=self._origin(),
                subprotocols=protocols or None,
                max_size=MAX_WS_BYTES,
                max_queue=16,
                open_timeout=15,
                proxy=None,
            )
            try:
                self.store.authorize(message)
                await send({"type": "ws.accept", "id": request_id, "subprotocol": socket.subprotocol})
            except BaseException:
                await socket.close()
                raise

            async def read():
                try:
                    while True:
                        data = await socket.recv()
                        self.store.authorize(message)
                        if (
                            not isinstance(data, (str, bytes))
                            or len(data.encode() if isinstance(data, str) else data) > MAX_WS_BYTES
                        ):
                            raise RemoteWorkspaceDenied("Upstream frame exceeds the limit")
                        await send(
                            {
                                "type": "ws.data",
                                "id": request_id,
                                "kind": "bytes" if isinstance(data, bytes) else "text",
                                "data": base64.b64encode(data).decode() if isinstance(data, bytes) else data,
                            }
                        )
                except Exception:
                    with contextlib.suppress(Exception):
                        await send(
                            {"type": "ws.close", "id": request_id, "code": 1011, "reason": "Workspace session ended"}
                        )
                finally:
                    self._ws.pop(request_id, None)
                    await socket.close()

            task = asyncio.create_task(read())
            self._ws[request_id] = (socket, task, dict(message))
        except (RemoteWorkspaceDenied, OSError, TimeoutError, ValueError, WebSocketException):
            await send({"type": "ws.close", "id": request_id, "code": 4403, "reason": "Workspace session denied"})

    async def _close_ws(self, request_id: str) -> None:
        entry = self._ws.pop(request_id, None)
        if entry is not None:
            socket, task, _ = entry
            if task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            with contextlib.suppress(Exception):
                await socket.close()
