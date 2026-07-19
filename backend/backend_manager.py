import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from backend.app_manifest import AppManifest

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_MCP_CALL_TIMEOUT_SECONDS = 60.0
DEFAULT_MCP_INITIALIZE_TIMEOUT_SECONDS = 10.0
DEFAULT_MCP_STOP_TIMEOUT_SECONDS = 2.0
DEFAULT_MCP_MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_MCP_MAX_STDERR_BYTES = 16 * 1024
DEFAULT_HTTP_AGENT_TIMEOUT_SECONDS = 60.0
DEFAULT_HTTP_AGENT_MAX_REQUEST_BYTES = 256 * 1024
DEFAULT_HTTP_AGENT_MAX_RESPONSE_BYTES = 1024 * 1024
DEFAULT_HTTP_AGENT_MAX_EVENTS = 1024
HTTP_AGENT_READ_CHUNK_BYTES = 16 * 1024

# MCP processes get only the small set of host variables normally needed to
# locate executables, temporary directories, and locale data. Secrets and
# application-specific values must be declared explicitly in the manifest.
MCP_INHERITED_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
)

_PERMISSION_VALUE_UNSET = object()


class MCPTransportError(RuntimeError):
    """The MCP subprocess transport can no longer service requests."""


class MCPProtocolError(RuntimeError):
    """The MCP subprocess emitted an invalid JSON-RPC message."""


class StdioJsonRpcClient:
    def __init__(
        self,
        command: list[str],
        args: list[str],
        env: dict[str, str] | None = None,
        *,
        call_timeout_seconds: float = DEFAULT_MCP_CALL_TIMEOUT_SECONDS,
        initialize_timeout_seconds: float = DEFAULT_MCP_INITIALIZE_TIMEOUT_SECONDS,
        stop_timeout_seconds: float = DEFAULT_MCP_STOP_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MCP_MAX_RESPONSE_BYTES,
        max_stderr_bytes: int = DEFAULT_MCP_MAX_STDERR_BYTES,
    ):
        if not command:
            raise ValueError("MCP command must not be empty")
        if call_timeout_seconds <= 0 or initialize_timeout_seconds <= 0 or stop_timeout_seconds <= 0:
            raise ValueError("MCP timeouts must be positive")
        if max_response_bytes <= 0 or max_stderr_bytes < 0:
            raise ValueError("MCP output bounds must not be negative")

        self.command = command
        self.args = args
        self.env = env
        self.call_timeout_seconds = call_timeout_seconds
        self.initialize_timeout_seconds = initialize_timeout_seconds
        self.stop_timeout_seconds = stop_timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_stderr_bytes = max_stderr_bytes

        self.process: asyncio.subprocess.Process | None = None
        self.read_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.pending_requests: dict[int | str, asyncio.Future] = {}
        self.next_id = 1
        self.lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._initialized = False
        self._stopping = False
        self._transport_error: MCPTransportError | MCPProtocolError | None = None
        self._stderr_tail = bytearray()
        self.protocol_version: str | None = None
        self.server_capabilities: dict[str, dict[str, Any]] = {}

    @property
    def is_healthy(self) -> bool:
        return bool(
            self.process
            and self.process.returncode is None
            and self._initialized
            and self._transport_error is None
        )

    @property
    def stderr_tail(self) -> str:
        """Return the bounded tail of stderr for diagnostics."""

        return bytes(self._stderr_tail).decode("utf-8", errors="replace")

    def _subprocess_env(self) -> dict[str, str]:
        process_env = {key: value for key, value in os.environ.items() if key in MCP_INHERITED_ENV_ALLOWLIST}
        process_env.setdefault("PATH", os.defpath)
        if self.env:
            process_env.update(self.env)
        return process_env

    async def start(self):
        async with self._lifecycle_lock:
            if self.is_healthy:
                return
            if self.process is not None:
                await self._stop_unlocked()

            logger.info("Starting MCP process: %s %s", self.command, self.args)
            self._initialized = False
            self._stopping = False
            self._transport_error = None
            self._stderr_tail.clear()
            self.protocol_version = None
            self.server_capabilities = {}
            self.process = await asyncio.create_subprocess_exec(
                self.command[0],
                *self.command[1:],
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._subprocess_env(),
                # Keep StreamReader's own limit just above our application
                # bound so a line without a delimiter cannot grow unbounded.
                limit=self.max_response_bytes + 1,
            )
            self.read_task = asyncio.create_task(self._read_loop(), name="mcp-stdout-reader")
            self.stderr_task = asyncio.create_task(self._stderr_loop(), name="mcp-stderr-reader")

            try:
                result = await self._request(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "ambient-agent", "version": "0.1.0"},
                    },
                    timeout_seconds=self.initialize_timeout_seconds,
                    allow_uninitialized=True,
                )
                self._accept_initialize_result(result)
                await asyncio.wait_for(
                    self._send_notification("notifications/initialized", {}),
                    timeout=self.initialize_timeout_seconds,
                )
                self._initialized = True
            except BaseException:
                await self._stop_unlocked()
                raise

    def _accept_initialize_result(self, result: Any) -> None:
        if not isinstance(result, dict):
            raise MCPProtocolError("MCP initialize result must be a JSON object")
        protocol_version = result.get("protocolVersion")
        if not isinstance(protocol_version, str) or not protocol_version:
            raise MCPProtocolError("MCP initialize result is missing protocolVersion")
        if protocol_version != MCP_PROTOCOL_VERSION:
            raise MCPProtocolError(f"Unsupported MCP protocolVersion: {protocol_version}")
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict):
            raise MCPProtocolError("MCP initialize result capabilities must be an object")
        normalized: dict[str, dict[str, Any]] = {}
        for capability, details in capabilities.items():
            if not isinstance(capability, str) or not capability or not isinstance(details, dict):
                raise MCPProtocolError(f"MCP {capability!s} capability must be an object")
            normalized[capability] = dict(details)
        self.protocol_version = protocol_version
        self.server_capabilities = normalized

    def _require_method_capability(self, method: str) -> None:
        family = method.partition("/")[0]
        required = {
            "tools": "tools",
            "resources": "resources",
            "prompts": "prompts",
            "completion": "completions",
        }.get(family)
        if required is None:
            return
        capability = self.server_capabilities.get(required)
        if capability is None:
            raise MCPProtocolError(f"MCP method '{method}' requires the server {required} capability")
        if method in {"resources/subscribe", "resources/unsubscribe"} and capability.get("subscribe") is not True:
            raise MCPProtocolError(f"MCP method '{method}' requires resources.subscribe capability")

    async def _read_loop(self):
        failure: MCPTransportError | MCPProtocolError | None = None
        try:
            while self.process and self.process.stdout:
                try:
                    line = await self.process.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise MCPProtocolError(
                        f"MCP response exceeded {self.max_response_bytes} bytes"
                    ) from exc
                if not line:
                    failure = MCPTransportError("MCP server closed stdout")
                    break
                if len(line) > self.max_response_bytes:
                    raise MCPProtocolError(f"MCP response exceeded {self.max_response_bytes} bytes")
                try:
                    data = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise MCPProtocolError("MCP server emitted malformed JSON") from exc
                if not isinstance(data, dict):
                    raise MCPProtocolError("MCP server emitted a non-object JSON-RPC message")
                if data.get("jsonrpc") != "2.0":
                    raise MCPProtocolError("MCP server emitted a message without jsonrpc 2.0")

                req_id = data.get("id")
                if "method" in data:
                    method = data.get("method")
                    if not isinstance(method, str) or not method:
                        raise MCPProtocolError("MCP server request method must be a non-empty string")
                    if req_id is None:
                        # Server notification; no response is required.
                        continue
                    response: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
                    if method == "ping":
                        response["result"] = {}
                    else:
                        response["error"] = {
                            "code": -32601,
                            "message": f"Client method not supported: {method}",
                        }
                    await self._write_message(response)
                    continue
                if req_id is None or req_id not in self.pending_requests:
                    # Late or unknown responses are harmless after a caller
                    # deadline/cancellation removed its pending request.
                    continue
                if ("result" in data) == ("error" in data):
                    raise MCPProtocolError("MCP response must contain exactly one of result or error")
                fut = self.pending_requests.pop(req_id)
                if fut.done():
                    continue
                if "error" in data:
                    error = data["error"]
                    if isinstance(error, dict):
                        message = str(error.get("message", "Unknown RPC error"))
                        code = error.get("code")
                        if code is not None:
                            message = f"MCP RPC error {code}: {message}"
                    else:
                        message = f"MCP RPC error: {error}"
                    fut.set_exception(RuntimeError(message))
                else:
                    fut.set_result(data.get("result"))
        except asyncio.CancelledError:
            if not self._stopping:
                failure = MCPTransportError("MCP response reader was cancelled")
        except (MCPTransportError, MCPProtocolError) as exc:
            failure = exc
        except Exception as exc:
            failure = MCPTransportError(f"MCP response transport failed: {exc}")
        finally:
            if failure is not None and not self._stopping:
                self._fail_transport(failure)
                process = self.process
                if process and process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.terminate()

    async def _stderr_loop(self):
        try:
            while self.process and self.process.stderr:
                chunk = await self.process.stderr.read(4096)
                if not chunk:
                    break
                if self.max_stderr_bytes:
                    self._stderr_tail.extend(chunk)
                    overflow = len(self._stderr_tail) - self.max_stderr_bytes
                    if overflow > 0:
                        del self._stderr_tail[:overflow]
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Error capturing MCP stderr: %s", exc)

    def _fail_transport(self, exc: MCPTransportError | MCPProtocolError) -> None:
        if self._transport_error is None:
            self._transport_error = exc
        pending = list(self.pending_requests.values())
        self.pending_requests.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(self._transport_error)

    async def _write_message(self, message: dict[str, Any]) -> None:
        process = self.process
        if not process or process.returncode is not None or process.stdin is None:
            raise MCPTransportError("MCP server is not running")
        if self._transport_error is not None:
            raise self._transport_error
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            async with self._write_lock:
                process.stdin.write(payload)
                await process.stdin.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = MCPTransportError(f"Failed to write to MCP server: {exc}")
            self._fail_transport(failure)
            raise failure

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        await self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    async def _best_effort_cancel(self, request_id: int | str, reason: str) -> None:
        if not self.process or self.process.returncode is not None or self._transport_error is not None:
            return
        try:
            await asyncio.wait_for(
                self._send_notification(
                    "notifications/cancelled",
                    {"requestId": request_id, "reason": reason},
                ),
                timeout=min(0.25, self.call_timeout_seconds),
            )
        except Exception:
            # Cancellation is advisory in MCP; the original timeout or caller
            # cancellation remains the authoritative outcome.
            return

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float,
        allow_uninitialized: bool = False,
    ) -> Any:
        if not self.process or self.process.returncode is not None:
            raise MCPTransportError("MCP server is not running")
        if self._transport_error is not None:
            raise self._transport_error
        if not allow_uninitialized and not self._initialized:
            raise MCPProtocolError("MCP server initialization has not completed")

        async with self.lock:
            req_id = self.next_id
            self.next_id += 1

        fut = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = fut
        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            await asyncio.wait_for(self._write_message(request), timeout=timeout_seconds)
        except TimeoutError:
            current = self.pending_requests.pop(req_id, None)
            if current and not current.done():
                current.cancel()
            await self._best_effort_cancel(req_id, f"{method} timed out while sending")
            raise TimeoutError(f"MCP request '{method}' timed out after {timeout_seconds:g}s")
        except BaseException:
            current = self.pending_requests.pop(req_id, None)
            if current and not current.done():
                current.cancel()
            raise

        try:
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            return await asyncio.wait_for(asyncio.shield(fut), timeout=remaining)
        except TimeoutError:
            pending = self.pending_requests.pop(req_id, None)
            if pending is None and fut.done():
                return fut.result()
            if not fut.done():
                fut.cancel()
            await self._best_effort_cancel(req_id, f"{method} timed out")
            raise TimeoutError(f"MCP request '{method}' timed out after {timeout_seconds:g}s")
        except asyncio.CancelledError:
            self.pending_requests.pop(req_id, None)
            if not fut.done():
                fut.cancel()
            # Shield the advisory notification so task cancellation cannot
            # prevent it from reaching the subprocess.
            with suppress(Exception):
                await asyncio.shield(self._best_effort_cancel(req_id, f"{method} was cancelled"))
            raise

    async def call(self, method: str, params: dict, *, timeout_seconds: float | None = None) -> Any:
        if not isinstance(method, str) or not method:
            raise ValueError("MCP method must be a non-empty string")
        if not isinstance(params, dict):
            raise ValueError("MCP params must be a JSON object")
        self._require_method_capability(method)
        timeout = self.call_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("MCP call timeout must be positive")
        return await self._request(method, params, timeout_seconds=timeout)

    async def _cancel_task(self, task: asyncio.Task | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    async def _stop_unlocked(self):
        self._stopping = True
        self._initialized = False
        self.protocol_version = None
        self.server_capabilities = {}
        self._fail_transport(MCPTransportError("MCP client stopped"))

        process = self.process
        if process is not None:
            if process.returncode is None:
                with suppress(OSError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=self.stop_timeout_seconds)
                except TimeoutError:
                    logger.warning("MCP process did not terminate in time; killing it")
                    with suppress(OSError):
                        process.kill()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=self.stop_timeout_seconds)
            else:
                with suppress(Exception):
                    await process.wait()
            if process.stdin is not None:
                process.stdin.close()
                with suppress(Exception):
                    await asyncio.wait_for(process.stdin.wait_closed(), timeout=self.stop_timeout_seconds)

        await self._cancel_task(self.read_task)
        await self._cancel_task(self.stderr_task)
        self.read_task = None
        self.stderr_task = None
        self.process = None

    async def stop(self):
        async with self._lifecycle_lock:
            await self._stop_unlocked()


class BackendManager:
    def __init__(
        self,
        *,
        http_agent_timeout_seconds: float = DEFAULT_HTTP_AGENT_TIMEOUT_SECONDS,
        http_agent_max_request_bytes: int = DEFAULT_HTTP_AGENT_MAX_REQUEST_BYTES,
        http_agent_max_response_bytes: int = DEFAULT_HTTP_AGENT_MAX_RESPONSE_BYTES,
        http_agent_max_events: int = DEFAULT_HTTP_AGENT_MAX_EVENTS,
    ):
        if http_agent_timeout_seconds <= 0:
            raise ValueError("HTTP agent timeout must be positive")
        if http_agent_max_request_bytes <= 0 or http_agent_max_response_bytes <= 0:
            raise ValueError("HTTP agent request and response bounds must be positive")
        if http_agent_max_events <= 0:
            raise ValueError("HTTP agent event bound must be positive")
        self.workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.permissions_file = Path(self.workspace_dir) / "backend_permissions.json"
        self.http_agent_timeout_seconds = http_agent_timeout_seconds
        self.http_agent_max_request_bytes = http_agent_max_request_bytes
        self.http_agent_max_response_bytes = http_agent_max_response_bytes
        self.http_agent_max_events = http_agent_max_events
        self.mcp_clients: dict[str, StdioJsonRpcClient] = {}
        self.permissions: dict[str, Any] = {}
        self.agent_runtimes: dict[str, dict[str, Any]] = {}
        self._mcp_runtime_identities: dict[str, dict[str, Any]] = {}
        self._mcp_spawn_locks: dict[str, asyncio.Lock] = {}
        self._load_permissions()

    def _load_permissions(self):
        if self.permissions_file.exists():
            try:
                self.permissions = json.loads(self.permissions_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Error loading permissions: {e}")
                self.permissions = {}
        else:
            self.permissions = {}

    def _save_permissions(self):
        try:
            self.permissions_file.parent.mkdir(parents=True, exist_ok=True)
            self.permissions_file.write_text(json.dumps(self.permissions, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error saving permissions: {e}")

    @staticmethod
    def _mcp_permission_target(
        command: list[str],
        args: list[str],
        env: dict[str, str] | None | object = _PERMISSION_VALUE_UNSET,
        manifest_revision: Any = _PERMISSION_VALUE_UNSET,
    ) -> dict[str, Any]:
        target: dict[str, Any] = {"command": list(command), "args": list(args)}
        if env is not _PERMISSION_VALUE_UNSET or manifest_revision is not _PERMISSION_VALUE_UNSET:
            explicit_env = {} if env is _PERMISSION_VALUE_UNSET or env is None else dict(env)
            serialized_env = json.dumps(explicit_env, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            target["env_digest"] = hashlib.sha256(serialized_env.encode("utf-8")).hexdigest()
            target["manifest_revision"] = (
                None if manifest_revision is _PERMISSION_VALUE_UNSET else manifest_revision
            )
        return target

    @staticmethod
    def _manifest_revision(manifest: AppManifest) -> str:
        return f"{getattr(manifest, 'manifest_version', 'unknown')}:{getattr(manifest, 'app_version', 'unknown')}"

    def mcp_permission_identity(self, manifest: AppManifest) -> dict[str, Any]:
        if not manifest.mcp_server:
            raise ValueError("Manifest does not declare an MCP server")
        return self._mcp_permission_target(
            manifest.mcp_server["command"],
            manifest.mcp_server.get("args", []),
            manifest.mcp_server.get("env", {}),
            self._manifest_revision(manifest),
        )

    @staticmethod
    def _normalize_mcp_identity(identity: dict[str, Any]) -> dict[str, Any]:
        if set(identity) != {"command", "args", "env_digest", "manifest_revision"}:
            raise ValueError("Invalid MCP permission identity fields")
        command = identity["command"]
        args = identity["args"]
        env_digest = identity["env_digest"]
        revision = identity["manifest_revision"]
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise ValueError("Invalid MCP permission command")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError("Invalid MCP permission args")
        if (
            not isinstance(env_digest, str)
            or len(env_digest) != 64
            or any(character not in "0123456789abcdef" for character in env_digest)
        ):
            raise ValueError("Invalid MCP permission environment digest")
        if not isinstance(revision, str) or not revision:
            raise ValueError("Invalid MCP permission manifest revision")
        return {
            "command": list(command),
            "args": list(args),
            "env_digest": env_digest,
            "manifest_revision": revision,
        }

    def is_mcp_identity_approved(self, app_id: str, identity: dict[str, Any]) -> bool:
        try:
            normalized = self._normalize_mcp_identity(identity)
        except ValueError:
            return False
        app_perms = self.permissions.get(app_id, {})
        return normalized in app_perms.get("mcp_servers", [])

    def approve_mcp_identity(self, app_id: str, identity: dict[str, Any]) -> None:
        normalized = self._normalize_mcp_identity(identity)
        if app_id not in self.permissions:
            self.permissions[app_id] = {}
        if "mcp_servers" not in self.permissions[app_id]:
            self.permissions[app_id]["mcp_servers"] = []
        if normalized not in self.permissions[app_id]["mcp_servers"]:
            self.permissions[app_id]["mcp_servers"].append(normalized)
            self._save_permissions()

    def is_mcp_approved(
        self,
        app_id: str,
        command: list[str],
        args: list[str],
        env: dict[str, str] | None | object = _PERMISSION_VALUE_UNSET,
        manifest_revision: Any = _PERMISSION_VALUE_UNSET,
    ) -> bool:
        app_perms = self.permissions.get(app_id, {})
        mcp_perms = app_perms.get("mcp_servers", [])
        target = self._mcp_permission_target(command, args, env, manifest_revision)
        return target in mcp_perms

    def approve_mcp(
        self,
        app_id: str,
        command: list[str],
        args: list[str],
        env: dict[str, str] | None | object = _PERMISSION_VALUE_UNSET,
        manifest_revision: Any = _PERMISSION_VALUE_UNSET,
    ):
        if app_id not in self.permissions:
            self.permissions[app_id] = {}
        if "mcp_servers" not in self.permissions[app_id]:
            self.permissions[app_id]["mcp_servers"] = []
        target = self._mcp_permission_target(command, args, env, manifest_revision)
        if target not in self.permissions[app_id]["mcp_servers"]:
            self.permissions[app_id]["mcp_servers"].append(target)
            self._save_permissions()

    def is_agent_approved(self, app_id: str, agent_url: str) -> bool:
        app_perms = self.permissions.get(app_id, {})
        agent_perms = app_perms.get("agents", [])
        return agent_url in agent_perms

    def approve_agent(self, app_id: str, agent_url: str):
        if app_id not in self.permissions:
            self.permissions[app_id] = {}
        if "agents" not in self.permissions[app_id]:
            self.permissions[app_id]["agents"] = []
        if agent_url not in self.permissions[app_id]["agents"]:
            self.permissions[app_id]["agents"].append(agent_url)
            self._save_permissions()

    async def get_or_start_mcp_client(
        self, app_id: str, manifest: AppManifest, send_ws_message_func: Callable
    ) -> StdioJsonRpcClient | None:
        if not manifest.mcp_server:
            return None

        command = manifest.mcp_server["command"]
        args = manifest.mcp_server.get("args", [])
        env = manifest.mcp_server.get("env", {})
        manifest_revision = self._manifest_revision(manifest)
        identity = self._mcp_permission_target(command, args, env, manifest_revision)
        spawn_lock = self._mcp_spawn_locks.setdefault(app_id, asyncio.Lock())

        async with spawn_lock:
            existing = self.mcp_clients.get(app_id)
            if existing and existing.is_healthy and self._mcp_runtime_identities.get(app_id) == identity:
                return existing

            if not self.is_mcp_identity_approved(app_id, identity):
                raise PermissionError("MCP runtime identity is not durably approved")

            if existing is not None:
                await existing.stop()
                self.mcp_clients.pop(app_id, None)
                self._mcp_runtime_identities.pop(app_id, None)

            client = StdioJsonRpcClient(command, args, env)
            await client.start()
            self.mcp_clients[app_id] = client
            self._mcp_runtime_identities[app_id] = identity
            return client

    async def handle_agent_message(
        self, app_id: str, manifest: AppManifest, message: dict, send_ws_message_func: Callable
    ):
        agent_url = manifest.agent_url
        if not agent_url:
            raise RuntimeError("No agent_url configured in app manifest")

        if not self.is_agent_approved(app_id, agent_url):
            raise PermissionError("Agent endpoint is not durably approved")
        if not isinstance(message, dict):
            raise ValueError("Agent message must be a JSON object")
        parsed_url = httpx.URL(agent_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("Agent endpoint must be an absolute HTTP(S) URL")
        if parsed_url.username or parsed_url.password:
            raise ValueError("Agent endpoint must not contain user credentials")
        serialized_message = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized_message) > self.http_agent_max_request_bytes:
            raise ValueError(
                f"Agent request exceeded {self.http_agent_max_request_bytes} bytes"
            )

        self.agent_runtimes[app_id] = {
            "id": app_id,
            "type": "http_agent",
            "managed": False,
            "status": "connecting",
            "endpoint": agent_url,
        }
        try:
            async with asyncio.timeout(self.http_agent_timeout_seconds):
                timeout = httpx.Timeout(self.http_agent_timeout_seconds)
                async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                    async with client.stream("POST", agent_url, json=message) as response:
                        if response.status_code != 200:
                            logger.error("Agent URL returned status %s", response.status_code)
                            raise RuntimeError(f"Agent returned HTTP {response.status_code}")

                        received_bytes = 0
                        buffered = bytearray()
                        data_lines: list[bytes] = []
                        emitted_events = 0

                        async def emit_line(raw_line: bytes) -> None:
                            nonlocal emitted_events
                            line = raw_line.rstrip(b"\r")
                            if not line:
                                if not data_lines:
                                    return
                                raw_data = b"\n".join(data_lines)
                                data_lines.clear()
                            elif line.startswith(b":"):
                                return
                            elif line.startswith(b"data:"):
                                data_lines.append(line[5:].lstrip(b" "))
                                return
                            else:
                                # Other standard SSE fields (id, event, retry)
                                # carry metadata that AG-UI does not consume.
                                return
                            try:
                                event_data = json.loads(raw_data.decode("utf-8"))
                            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                                raise RuntimeError("Agent emitted malformed SSE JSON") from exc
                            if not isinstance(event_data, dict):
                                raise RuntimeError("Agent SSE event must be a JSON object")
                            emitted_events += 1
                            if emitted_events > self.http_agent_max_events:
                                raise RuntimeError(
                                    f"Agent response exceeded {self.http_agent_max_events} events"
                                )
                            await send_ws_message_func(
                                {"type": "ag_ui_event", "app_id": app_id, "event": event_data}
                            )

                        async for chunk in response.aiter_bytes(chunk_size=HTTP_AGENT_READ_CHUNK_BYTES):
                            received_bytes += len(chunk)
                            if received_bytes > self.http_agent_max_response_bytes:
                                raise RuntimeError(
                                    "Agent response exceeded "
                                    f"{self.http_agent_max_response_bytes} bytes"
                                )
                            buffered.extend(chunk)
                            while True:
                                newline = buffered.find(b"\n")
                                if newline < 0:
                                    break
                                line = bytes(buffered[:newline])
                                del buffered[: newline + 1]
                                await emit_line(line)
                        if buffered:
                            await emit_line(bytes(buffered))
                        if data_lines:
                            await emit_line(b"")
                        self.agent_runtimes[app_id]["status"] = "healthy"
        except asyncio.CancelledError:
            # The caller owns the durable cancellation outcome. Since the
            # remote endpoint may already have acted, do not claim failure or
            # successful cancellation here.
            self.agent_runtimes[app_id]["status"] = "unknown"
            raise
        except TimeoutError as exc:
            self.agent_runtimes[app_id]["status"] = "unhealthy"
            raise TimeoutError(
                f"Agent call timed out after {self.http_agent_timeout_seconds:g}s"
            ) from exc
        except Exception:
            self.agent_runtimes[app_id]["status"] = "unhealthy"
            raise

    def list_runtimes(self) -> list[dict[str, Any]]:
        runtimes: list[dict[str, Any]] = []
        for app_id, client in sorted(self.mcp_clients.items()):
            running = client.is_healthy
            runtimes.append(
                {
                    "id": app_id,
                    "type": "mcp",
                    "managed": True,
                    "status": "healthy" if running else "stopped",
                    "pid": client.process.pid if running and client.process else None,
                }
            )
        runtimes.extend(self.agent_runtimes.values())
        runtimes.append({"id": "internal:agent", "type": "internal", "managed": False, "status": "healthy"})
        return runtimes

    async def stop_runtime(self, runtime_id: str) -> bool:
        spawn_lock = self._mcp_spawn_locks.setdefault(runtime_id, asyncio.Lock())
        async with spawn_lock:
            client = self.mcp_clients.get(runtime_id)
            if client is None:
                return False
            await client.stop()
            self.mcp_clients.pop(runtime_id, None)
            self._mcp_runtime_identities.pop(runtime_id, None)
            return True

    async def shutdown(self):
        for runtime_id in list(self.mcp_clients):
            await self.stop_runtime(runtime_id)
