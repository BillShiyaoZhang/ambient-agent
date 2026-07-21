"""Trusted coding-agent registry with managed installation and authentication."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from backend.opencode_service import _terminate_process

InstallState = Literal["not_installed", "installing", "installed", "failed"]
AuthState = Literal["not_required", "signed_out", "starting", "waiting", "signed_in", "failed", "cancelled", "expired"]
ModelMode = Literal["native", "shared_binding", "hybrid", "none"]

_INSTALL_SCRIPT_LIMIT = 2 * 1024 * 1024
_OUTPUT_LIMIT = 64 * 1024
_APP_SERVER_OUTPUT_LIMIT = 1024 * 1024
_APP_SERVER_TIMEOUT = 15.0
_DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4,}-[A-Z0-9]{4,}\b")
_URL_RE = re.compile(r"https://[^\s\x1b]+")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SAFE_ENV = {
    "ALL_PROXY",
    "CODEX_ACCESS_TOKEN",
    "CODEX_API_KEY",
    "CODEX_CA_CERTIFICATE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


class CodingAgentRuntimeError(RuntimeError):
    code = "coding_agent_runtime_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


@dataclass(frozen=True)
class CodingAgentSpec:
    id: str
    name: str
    description: str
    auth_hint: str
    command_env: str
    default_command: str
    install_handler: str | None
    auth_methods: tuple[str, ...]
    model_modes: tuple[ModelMode, ...]
    default_model_mode: ModelMode
    model_selection: Literal["none", "optional", "required"]
    catalog_source: Literal["none", "agent", "provider_registry"]


SPECS: tuple[CodingAgentSpec, ...] = (
    CodingAgentSpec(
        id="opencode",
        name="OpenCode",
        description="Uses OpenCode ACP with a model binding from the shared Provider Registry.",
        auth_hint="Uses the credentials referenced by its model binding.",
        command_env="OPENCODE_COMMAND",
        default_command="opencode",
        install_handler=None,
        auth_methods=(),
        model_modes=("shared_binding",),
        default_model_mode="shared_binding",
        model_selection="required",
        catalog_source="provider_registry",
    ),
    CodingAgentSpec(
        id="codex",
        name="Codex",
        description="Runs Codex in the managed container runtime using its own ChatGPT or API login.",
        auth_hint="Uses its own Codex login/subscription, never the Ambient model credentials.",
        command_env="CODEX_COMMAND",
        default_command="",
        install_handler="codex_standalone",
        auth_methods=("device_code",),
        model_modes=("native",),
        default_model_mode="native",
        model_selection="optional",
        catalog_source="agent",
    ),
)


def spec_for(agent_id: str) -> CodingAgentSpec:
    for spec in SPECS:
        if spec.id == agent_id:
            return spec
    raise CodingAgentRuntimeError("Unknown coding agent", code="coding_agent_not_found")


def _clean_output(value: bytes | str) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    return _ANSI_RE.sub("", text).replace("\r", "").strip()


def _safe_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    environment = {key: value for key in _SAFE_ENV if (value := os.environ.get(key)) is not None}
    environment.setdefault("PATH", os.defpath)
    if extra:
        environment.update(extra)
    return environment


class CodingAgentRuntime:
    """Owns managed CLI binaries, native credentials, and lifecycle operations."""

    def __init__(self, workspace_dir: str | Path):
        default_root = Path(workspace_dir) / "coding_agents" / "runtime"
        self.root = Path(os.getenv("CODING_AGENT_RUNTIME_DIR", str(default_root)))
        self._operations: dict[str, dict[str, Any]] = {}
        self._install_tasks: dict[str, asyncio.Task[None]] = {}
        self._auth_sessions: dict[str, dict[str, Any]] = {}
        self._auth_tasks: dict[str, asyncio.Task[None]] = {}
        self._auth_processes: dict[str, asyncio.subprocess.Process] = {}
        self._locks = {spec.id: asyncio.Lock() for spec in SPECS}

    def agent_root(self, agent_id: str) -> Path:
        spec_for(agent_id)
        return self.root / "agents" / agent_id

    def state_dir(self, agent_id: str) -> Path:
        return self.agent_root(agent_id) / "state"

    def managed_command(self, agent_id: str) -> Path:
        return self.agent_root(agent_id) / "bin" / ("codex.exe" if os.name == "nt" else "codex")

    def command(self, agent_id: str) -> list[str] | None:
        spec = spec_for(agent_id)
        configured = os.getenv(spec.command_env, "").strip()
        if configured:
            try:
                argv = shlex.split(configured, posix=os.name != "nt")
            except ValueError as exc:
                raise CodingAgentRuntimeError(
                    f"Invalid {spec.command_env}: {exc!s}", code="coding_agent_command_invalid"
                ) from exc
            if not argv:
                return None
            executable = argv[0]
            if Path(executable).is_absolute():
                return argv if Path(executable).is_file() else None
            resolved = shutil.which(executable)
            return [resolved, *argv[1:]] if resolved else None
        managed = self.managed_command(agent_id)
        if managed.is_file():
            return [str(managed)]
        if spec.default_command:
            resolved = shutil.which(spec.default_command)
            if resolved:
                return [resolved]
        return None

    def process_environment(self, agent_id: str) -> dict[str, str]:
        environment = _safe_environment()
        if agent_id == "codex":
            state_dir = self.state_dir(agent_id)
            state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with contextlib.suppress(OSError):
                state_dir.chmod(0o700)
            environment.update({"CODEX_HOME": str(state_dir), "HOME": str(state_dir)})
        return environment

    async def _run_probe(self, argv: list[str], *, agent_id: str) -> tuple[int, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self.process_environment(agent_id),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            return proc.returncode or 0, _clean_output(stdout[:_OUTPUT_LIMIT])
        except (FileNotFoundError, PermissionError, OSError, TimeoutError) as exc:
            return 1, str(exc)

    async def status(self, agent_id: str) -> dict[str, Any]:
        spec = spec_for(agent_id)
        command = self.command(agent_id)
        if command is None:
            operation = self._active_install(agent_id)
            if agent_id in self._install_tasks:
                install_state: InstallState = "installing"
            elif operation and operation["status"] == "failed":
                install_state = "failed"
            else:
                install_state = "not_installed"
            return {
                "installed": False,
                "install_state": install_state,
                "install_operation": operation,
                "available": False,
                "authenticated": False if spec.auth_methods else None,
                "auth_state": "signed_out" if spec.auth_methods else "not_required",
                "version": "",
                "status_detail": operation.get("error", "") if operation else "",
            }
        version_code, version = await self._run_probe([*command, "--version"], agent_id=agent_id)
        installed = version_code == 0
        authenticated: bool | None = None
        auth_state: AuthState = "not_required"
        detail = ""
        if spec.auth_methods and installed:
            login_code, detail = await self._run_probe([*command, "login", "status"], agent_id=agent_id)
            authenticated = login_code == 0
            auth_state = "signed_in" if authenticated else "signed_out"
            active_auth = self._auth_sessions.get(agent_id)
            if active_auth and active_auth["status"] in {"starting", "waiting"}:
                auth_state = active_auth["status"]
        return {
            "installed": installed,
            "install_state": "installed" if installed else "failed",
            "install_operation": self._active_install(agent_id),
            "available": installed,
            "authenticated": authenticated,
            "auth_state": auth_state,
            "version": version if installed else "",
            "status_detail": detail,
        }

    async def models(self, agent_id: str) -> dict[str, Any]:
        """Return the native model catalog advertised by the coding agent."""

        spec = spec_for(agent_id)
        if spec.catalog_source != "agent":
            raise CodingAgentRuntimeError(
                "This coding agent does not expose a native model catalog",
                code="model_catalog_unsupported",
            )
        command = self.command(agent_id)
        if command is None:
            raise CodingAgentRuntimeError("Coding agent is not installed", code="coding_agent_not_installed")
        status = await self.status(agent_id)
        if spec.auth_methods and not status["authenticated"]:
            raise CodingAgentRuntimeError("Sign in before loading models", code="coding_agent_auth_required")

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                "app-server",
                "--stdio",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.process_environment(agent_id),
                start_new_session=os.name != "nt",
                limit=_APP_SERVER_OUTPUT_LIMIT,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise CodingAgentRuntimeError(
                f"Unable to start the coding-agent model catalog: {exc!s}",
                code="model_catalog_failed",
            ) from exc
        if proc.stdin is None or proc.stdout is None or proc.stderr is None:
            await _terminate_process(proc, process_group=True)
            raise CodingAgentRuntimeError("Model catalog process did not expose stdio", code="model_catalog_failed")

        stderr = bytearray()

        async def drain_stderr() -> None:
            while chunk := await proc.stderr.read(4096):
                remaining = _OUTPUT_LIMIT - len(stderr)
                if remaining > 0:
                    stderr.extend(chunk[:remaining])

        stderr_task = asyncio.create_task(drain_stderr())
        bytes_read = 0

        async def request(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal bytes_read
            payload = (
                json.dumps(
                    {"id": request_id, "method": method, "params": params},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            try:
                proc.stdin.write(payload)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                raise CodingAgentRuntimeError(
                    "Model catalog process closed before accepting the request",
                    code="model_catalog_failed",
                ) from exc
            deadline = time.monotonic() + _APP_SERVER_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CodingAgentRuntimeError("Model catalog request timed out", code="model_catalog_failed")
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except TimeoutError as exc:
                    raise CodingAgentRuntimeError(
                        "Model catalog request timed out",
                        code="model_catalog_failed",
                    ) from exc
                if not line:
                    detail = _clean_output(bytes(stderr)) or "app-server closed its output stream"
                    raise CodingAgentRuntimeError(
                        f"Unable to load coding-agent models: {detail}", code="model_catalog_failed"
                    )
                bytes_read += len(line)
                if bytes_read > _APP_SERVER_OUTPUT_LIMIT:
                    raise CodingAgentRuntimeError(
                        "Model catalog response exceeded the size limit", code="model_catalog_failed"
                    )
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CodingAgentRuntimeError(
                        "Model catalog returned malformed JSON", code="model_catalog_failed"
                    ) from exc
                if not isinstance(message, dict) or message.get("id") != request_id:
                    continue
                if message.get("error"):
                    error = message["error"]
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise CodingAgentRuntimeError(
                        f"Coding-agent model catalog failed: {detail}",
                        code="model_catalog_failed",
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise CodingAgentRuntimeError(
                        "Model catalog returned an invalid result", code="model_catalog_failed"
                    )
                return result

        try:
            await request(
                1,
                "initialize",
                {
                    "clientInfo": {"name": "ambient-agent", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            request_id = 2
            cursor: str | None = None
            raw_models: list[dict[str, Any]] = []
            for _ in range(10):
                result = await request(
                    request_id,
                    "model/list",
                    {"cursor": cursor, "includeHidden": False, "limit": 100},
                )
                data = result.get("data")
                if not isinstance(data, list):
                    raise CodingAgentRuntimeError(
                        "Model catalog returned invalid model data", code="model_catalog_failed"
                    )
                raw_models.extend(item for item in data if isinstance(item, dict))
                next_cursor = result.get("nextCursor")
                if not next_cursor:
                    break
                cursor = str(next_cursor)
                request_id += 1
            else:
                raise CodingAgentRuntimeError(
                    "Model catalog exceeded the pagination limit", code="model_catalog_failed"
                )

            models: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in raw_models:
                model_id = str(item.get("id") or item.get("model") or "").strip()
                if not model_id or model_id in seen or item.get("hidden") is True:
                    continue
                seen.add(model_id)
                efforts = item.get("supportedReasoningEfforts")
                models.append(
                    {
                        "id": model_id,
                        "model": str(item.get("model") or model_id),
                        "display_name": str(item.get("displayName") or model_id),
                        "description": str(item.get("description") or ""),
                        "is_default": item.get("isDefault") is True,
                        "default_reasoning_effort": str(item.get("defaultReasoningEffort") or ""),
                        "supported_reasoning_efforts": [
                            str(option.get("reasoningEffort"))
                            for option in efforts or []
                            if isinstance(option, dict) and option.get("reasoningEffort")
                        ],
                    }
                )
            default_model = next((item["id"] for item in models if item["is_default"]), None)
            return {
                "agent_id": agent_id,
                "source": "agent",
                "default_model": default_model,
                "models": models,
            }
        finally:
            proc.stdin.close()
            with contextlib.suppress(Exception):
                await proc.stdin.wait_closed()
            if proc.returncode is None:
                await _terminate_process(proc, process_group=True)
            if not stderr_task.done():
                stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

    def _active_install(self, agent_id: str) -> dict[str, Any] | None:
        operations = [item for item in self._operations.values() if item["agent_id"] == agent_id]
        if not operations:
            return None
        return dict(max(operations, key=lambda item: item["created_at"]))

    def operation(self, agent_id: str, operation_id: str) -> dict[str, Any]:
        spec_for(agent_id)
        operation = self._operations.get(operation_id)
        if operation is None or operation["agent_id"] != agent_id:
            raise CodingAgentRuntimeError("Coding-agent operation not found", code="operation_not_found")
        return dict(operation)

    async def start_install(self, agent_id: str) -> dict[str, Any]:
        spec = spec_for(agent_id)
        if spec.install_handler is None:
            raise CodingAgentRuntimeError(
                "This coding agent is provided by the system image", code="install_unsupported"
            )
        async with self._locks[agent_id]:
            if self.command(agent_id):
                return {
                    "id": "installed",
                    "agent_id": agent_id,
                    "status": "installed",
                    "created_at": time.time(),
                    "error": "",
                }
            task = self._install_tasks.get(agent_id)
            if task and not task.done():
                return self._active_install(agent_id) or {}
            operation_id = uuid.uuid4().hex
            operation = {
                "id": operation_id,
                "agent_id": agent_id,
                "status": "installing",
                "created_at": time.time(),
                "error": "",
            }
            self._operations[operation_id] = operation
            task = asyncio.create_task(self._install(agent_id, operation_id))
            self._install_tasks[agent_id] = task
            return dict(operation)

    async def _install(self, agent_id: str, operation_id: str) -> None:
        operation = self._operations[operation_id]
        try:
            spec = spec_for(agent_id)
            if spec.install_handler == "codex_standalone":
                await self._install_codex(operation_id)
            else:
                raise CodingAgentRuntimeError("Unsupported installer", code="install_unsupported")
            operation["status"] = "installed"
        except asyncio.CancelledError:
            operation.update(status="failed", error="Installation was cancelled")
            raise
        except Exception as exc:
            operation.update(status="failed", error=str(exc))
        finally:
            self._install_tasks.pop(agent_id, None)

    async def _install_codex(self, operation_id: str) -> None:
        agent_root = self.agent_root("codex")
        agent_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = agent_root / f".install-{operation_id}"
        staging.mkdir(mode=0o700)
        install_dir = staging / "bin"
        install_dir.mkdir(mode=0o700)
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get("https://chatgpt.com/codex/install.sh")
                response.raise_for_status()
                script = response.content
            if not script or len(script) > _INSTALL_SCRIPT_LIMIT:
                raise CodingAgentRuntimeError("Codex installer response was empty or too large", code="install_failed")
            environment = self.process_environment("codex")
            environment.update(
                {
                    "CODEX_NON_INTERACTIVE": "1",
                    "CODEX_INSTALL_DIR": str(install_dir),
                }
            )
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-s",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=environment,
                start_new_session=os.name != "nt",
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(script), timeout=180.0)
            if proc.returncode != 0:
                detail = _clean_output(stdout[-_OUTPUT_LIMIT:])
                raise CodingAgentRuntimeError(f"Codex installation failed: {detail}", code="install_failed")
            binary = install_dir / "codex"
            if not binary.is_file():
                raise CodingAgentRuntimeError("Codex installer did not create the CLI binary", code="install_failed")
            code, version = await self._run_probe([str(binary), "--version"], agent_id="codex")
            if code != 0:
                raise CodingAgentRuntimeError(f"Installed Codex failed validation: {version}", code="install_failed")
            destination = agent_root / "bin"
            if destination.exists():
                raise CodingAgentRuntimeError("Codex became installed concurrently", code="install_conflict")
            install_dir.replace(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def auth_session(self, agent_id: str) -> dict[str, Any]:
        spec_for(agent_id)
        session = self._auth_sessions.get(agent_id)
        if session is None:
            return {
                "id": "",
                "agent_id": agent_id,
                "status": "signed_out",
                "method": "device_code",
                "verification_uri": "",
                "user_code": "",
                "expires_at": None,
                "error": "",
            }
        return dict(session)

    async def start_auth(self, agent_id: str, method: str = "device_code") -> dict[str, Any]:
        spec = spec_for(agent_id)
        if method not in spec.auth_methods:
            raise CodingAgentRuntimeError("Unsupported authentication method", code="auth_method_unsupported")
        command = self.command(agent_id)
        if command is None:
            raise CodingAgentRuntimeError(
                "Install the coding agent before signing in", code="coding_agent_not_installed"
            )
        current_status = await self.status(agent_id)
        if current_status["authenticated"]:
            session = {
                "id": "authenticated",
                "agent_id": agent_id,
                "status": "signed_in",
                "method": method,
                "verification_uri": "",
                "user_code": "",
                "expires_at": None,
                "error": "",
            }
            self._auth_sessions[agent_id] = session
            return dict(session)
        async with self._locks[agent_id]:
            task = self._auth_tasks.get(agent_id)
            if task and not task.done():
                return self.auth_session(agent_id)
            session = {
                "id": uuid.uuid4().hex,
                "agent_id": agent_id,
                "status": "starting",
                "method": method,
                "verification_uri": "",
                "user_code": "",
                "expires_at": datetime.fromtimestamp(time.time() + 900, UTC).isoformat(),
                "error": "",
            }
            self._auth_sessions[agent_id] = session
            task = asyncio.create_task(self._run_device_auth(agent_id, command))
            self._auth_tasks[agent_id] = task
            return dict(session)

    async def _run_device_auth(self, agent_id: str, command: list[str]) -> None:
        session = self._auth_sessions[agent_id]
        proc: asyncio.subprocess.Process | None = None
        output = ""
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                "login",
                "--device-auth",
                "-c",
                'cli_auth_credentials_store="file"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self.process_environment(agent_id),
                start_new_session=os.name != "nt",
            )
            self._auth_processes[agent_id] = proc
            if proc.stdout is None:
                raise CodingAgentRuntimeError("Codex login did not expose an output stream", code="auth_failed")
            deadline = time.monotonic() + 920
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    session.update(status="expired", verification_uri="", user_code="")
                    await _terminate_process(proc, process_group=True)
                    return
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except TimeoutError:
                    session.update(status="expired", verification_uri="", user_code="")
                    await _terminate_process(proc, process_group=True)
                    return
                if not line:
                    break
                output = (output + _clean_output(line) + "\n")[-_OUTPUT_LIMIT:]
                url = _URL_RE.search(output)
                code = _DEVICE_CODE_RE.search(output)
                if url and code:
                    session.update(
                        status="waiting",
                        verification_uri=url.group(0),
                        user_code=code.group(0),
                    )
            return_code = await proc.wait()
            if return_code == 0:
                session.update(status="signed_in", error="", verification_uri="", user_code="")
            elif session["status"] not in {"cancelled", "expired"}:
                session.update(
                    status="failed",
                    error="Codex device login failed",
                    verification_uri="",
                    user_code="",
                )
        except asyncio.CancelledError:
            if proc and proc.returncode is None:
                await _terminate_process(proc, process_group=True)
            if session["status"] not in {"cancelled", "expired"}:
                session["status"] = "cancelled"
            raise
        except Exception as exc:
            session.update(status="failed", error=str(exc), verification_uri="", user_code="")
            if proc and proc.returncode is None:
                await _terminate_process(proc, process_group=True)
        finally:
            self._auth_processes.pop(agent_id, None)
            self._auth_tasks.pop(agent_id, None)

    async def cancel_auth(self, agent_id: str) -> dict[str, Any]:
        spec_for(agent_id)
        session = self._auth_sessions.get(agent_id)
        if session:
            session.update(status="cancelled", verification_uri="", user_code="")
        task = self._auth_tasks.get(agent_id)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return self.auth_session(agent_id)

    async def logout(self, agent_id: str) -> dict[str, Any]:
        spec = spec_for(agent_id)
        if not spec.auth_methods:
            raise CodingAgentRuntimeError("This coding agent has no native login", code="auth_not_required")
        await self.cancel_auth(agent_id)
        command = self.command(agent_id)
        if command is None:
            raise CodingAgentRuntimeError("Coding agent is not installed", code="coding_agent_not_installed")
        code, output = await self._run_probe([*command, "logout"], agent_id=agent_id)
        if code != 0:
            raise CodingAgentRuntimeError(f"Unable to sign out: {output}", code="auth_logout_failed")
        self._auth_sessions.pop(agent_id, None)
        return self.auth_session(agent_id)

    async def shutdown(self) -> None:
        for agent_id in list(self._auth_tasks):
            await self.cancel_auth(agent_id)
        for task in list(self._install_tasks.values()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def model_capability(spec: CodingAgentSpec) -> dict[str, Any]:
    return {
        "modes": list(spec.model_modes),
        "default_mode": spec.default_model_mode,
        "selection": spec.model_selection,
        "catalog_source": spec.catalog_source,
        "supports_inherit": "shared_binding" in spec.model_modes,
    }
