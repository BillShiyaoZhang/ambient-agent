"""Guarded App-scoped HTTP data sources and bounded runtime diagnostics."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import socket
import tempfile
import threading
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from backend.app_manager import AppManager

_MAX_DIAGNOSTIC_FILE_BYTES = 1024 * 1024
_MAX_DIAGNOSTIC_LINES = 256
_MAX_QUERY_ITEMS = 64
_MAX_QUERY_BYTES = 16 * 1024
_MAX_BODY_BYTES = 64 * 1024
_REQUEST_TIMEOUT_SECONDS = 15.0


class AppDataSourceError(RuntimeError):
    """Stable, actionable failure returned to Widgets and coding agents."""

    def __init__(
        self,
        code: str,
        message: str,
        hint: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "details": self.details,
        }


async def _ensure_public_hostname(hostname: str) -> None:
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise AppDataSourceError(
            "data_source_dns_failed",
            f"Unable to resolve data-source host '{hostname}'",
            "Check the manifest base_url and the container DNS/network configuration.",
            status_code=502,
            details={"hostname": hostname},
        ) from exc
    resolved = {item[4][0] for item in addresses}
    if not resolved or any(not ipaddress.ip_address(address).is_global for address in resolved):
        raise AppDataSourceError(
            "data_source_private_destination",
            "The data-source hostname resolves to a non-public address",
            "Use a public HTTPS API origin; localhost, private networks, and metadata endpoints are blocked.",
            details={"hostname": hostname},
        )


class AppRuntimeDiagnostics:
    def __init__(self, workspace_dir: str | Path) -> None:
        self.path = Path(workspace_dir) / ".ambient" / "app_runtime_diagnostics.jsonl"
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            if self.path.stat().st_size <= _MAX_DIAGNOSTIC_FILE_BYTES:
                return
            lines = deque(maxlen=_MAX_DIAGNOSTIC_LINES)
            with self.path.open(encoding="utf-8") as handle:
                lines.extend(handle)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=".app-runtime-diagnostics-",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.writelines(lines)
                temporary_path.replace(self.path)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def recent(self, app_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        records: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 20)))
        with self._lock:
            try:
                with self.path.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict) and item.get("app_id") == app_id:
                            records.append(item)
            except OSError:
                return []
        return list(records)


class AppDataSourceGateway:
    def __init__(
        self,
        app_manager: AppManager,
        workspace_dir: str | Path,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        public_host_resolver: Callable[[str], Awaitable[None] | None] = _ensure_public_hostname,
    ) -> None:
        self.app_manager = app_manager
        self.transport = transport
        self.public_host_resolver = public_host_resolver
        self.diagnostics = AppRuntimeDiagnostics(workspace_dir)

    def recent_diagnostics(self, app_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return self.diagnostics.recent(app_id, limit=limit)

    def _record(self, app_id: str, source_id: str, request: dict[str, Any], error: AppDataSourceError) -> None:
        query = request.get("query") if isinstance(request.get("query"), dict) else {}
        self.diagnostics.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "app_id": app_id,
                "source_id": source_id,
                "path": request.get("path") if isinstance(request.get("path"), str) else None,
                "method": request.get("method") if isinstance(request.get("method"), str) else None,
                "query_keys": sorted(str(key)[:128] for key in query)[:_MAX_QUERY_ITEMS],
                **error.to_dict(),
            }
        )

    async def _resolve(self, hostname: str) -> None:
        outcome = self.public_host_resolver(hostname)
        if inspect.isawaitable(outcome):
            await outcome

    @staticmethod
    def _validate_query(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > _MAX_QUERY_ITEMS:
            raise AppDataSourceError(
                "data_source_invalid_query",
                "Data-source query must be an object with at most 64 fields",
                "Pass scalar or scalar-array query values in ambient.net.request(..., { query }).",
            )
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise AppDataSourceError(
                    "data_source_invalid_query",
                    "Data-source query keys must be non-empty strings of at most 128 characters",
                    "Use the upstream API's documented query parameter names.",
                )
            values = item if isinstance(item, list) else [item]
            if len(values) > 100 or any(not isinstance(entry, (str, int, float, bool)) for entry in values):
                raise AppDataSourceError(
                    "data_source_invalid_query",
                    f"Unsupported query value for '{key}'",
                    "Query values must be strings, numbers, booleans, or arrays of those scalar types.",
                    details={"query_key": key},
                )
            result[key] = item
        if len(str(httpx.QueryParams(result)).encode("utf-8")) > _MAX_QUERY_BYTES:
            raise AppDataSourceError(
                "data_source_query_too_large",
                "Data-source query exceeds 16 KiB",
                "Send fewer or shorter query values, or use a dedicated backend capability.",
            )
        return result

    async def request(self, app_id: str, source_id: str, request: dict[str, Any]) -> Any:
        try:
            manifest = self.app_manager.get_manifest(app_id)
            if manifest is None:
                raise AppDataSourceError(
                    "app_manifest_unavailable",
                    f"App '{app_id}' has no valid manifest",
                    "Create or repair manifest.json before using ambient.net.request.",
                    status_code=404,
                )
            source = (manifest.data_sources or {}).get(source_id)
            if source is None:
                raise AppDataSourceError(
                    "data_source_not_declared",
                    f"Data source '{source_id}' is not declared by App '{app_id}'",
                    f"Add data_sources.{source_id} to manifest.json or use a declared source id.",
                    details={"declared_sources": sorted((manifest.data_sources or {}).keys())},
                )
            path = request.get("path")
            if path not in source["allowed_paths"]:
                raise AppDataSourceError(
                    "data_source_path_not_allowed",
                    f"Path '{path}' is not allowed for data source '{source_id}'",
                    "Add the exact public API path to manifest.json allowed_paths, then republish the App.",
                    details={"allowed_paths": source["allowed_paths"]},
                )
            method = str(request.get("method") or "GET").upper()
            if method not in source["methods"]:
                raise AppDataSourceError(
                    "data_source_method_not_allowed",
                    f"Method '{method}' is not allowed for data source '{source_id}'",
                    "Use a declared method or update manifest.json methods and republish the App.",
                    details={"allowed_methods": source["methods"]},
                )
            query = self._validate_query(request.get("query"))
            body = request.get("body")
            if method == "GET" and body is not None:
                raise AppDataSourceError(
                    "data_source_body_not_allowed",
                    "GET data-source requests cannot contain a body",
                    "Move values into the query object or declare and use POST.",
                )
            if body is not None:
                try:
                    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
                except (TypeError, ValueError) as exc:
                    raise AppDataSourceError(
                        "data_source_invalid_body",
                        "Data-source body must be JSON serializable",
                        "Pass only JSON objects, arrays, strings, numbers, booleans, or null.",
                    ) from exc
                if len(body_bytes) > _MAX_BODY_BYTES:
                    raise AppDataSourceError(
                        "data_source_body_too_large",
                        "Data-source request body exceeds 64 KiB",
                        "Send a smaller request or use a dedicated backend capability.",
                    )

            hostname = httpx.URL(source["base_url"]).host
            await self._resolve(hostname)
            url = f"{source['base_url']}{path}"
            timeout = httpx.Timeout(_REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method, url, params=query, json=body if method == "POST" else None
                ) as response:
                    if response.is_redirect:
                        raise AppDataSourceError(
                            "data_source_redirect_blocked",
                            "The upstream data source returned a redirect",
                            "Declare the final HTTPS origin and path directly in manifest.json.",
                            status_code=502,
                            details={"upstream_status": response.status_code},
                        )
                    if response.status_code < 200 or response.status_code >= 300:
                        raise AppDataSourceError(
                            "data_source_upstream_error",
                            f"The upstream data source returned HTTP {response.status_code}",
                            "Check the API path and query parameters; authenticated APIs require MCP/Capability binding.",
                            status_code=502,
                            details={"upstream_status": response.status_code},
                        )
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > source["response_limit"]:
                            raise AppDataSourceError(
                                "data_source_response_too_large",
                                "The upstream response exceeded the App manifest limit",
                                "Request fewer fields/items or raise response_limit within the platform maximum.",
                                status_code=502,
                                details={"response_limit": source["response_limit"]},
                            )
            try:
                return json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AppDataSourceError(
                    "data_source_invalid_json",
                    "The upstream response was not valid JSON",
                    "Verify that the declared path returns JSON or use a dedicated backend capability for other formats.",
                    status_code=502,
                ) from exc
        except AppDataSourceError as exc:
            self._record(app_id, source_id, request, exc)
            raise
        except httpx.HTTPError as exc:
            error = AppDataSourceError(
                "data_source_network_error",
                "The upstream data-source request failed",
                "Check container egress, DNS, TLS, and the manifest base_url, then retry.",
                status_code=502,
                details={"exception_type": type(exc).__name__},
            )
            self._record(app_id, source_id, request, error)
            raise error from exc
