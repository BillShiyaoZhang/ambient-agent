"""Trusted coding-agent registry with managed installation and authentication."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import platform
import re
import shlex
import shutil
import tarfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from backend.coding_agent_acp import _terminate_process

InstallState = Literal["not_installed", "installing", "installed", "failed"]
AuthState = Literal["not_required", "signed_out", "starting", "waiting", "signed_in", "failed", "cancelled", "expired"]
ModelMode = Literal["native", "shared_binding", "hybrid", "none"]
ACPTransport = Literal["native", "bridge"]

_CODEX_VERSION = "0.145.0"
_CODEX_ARCHIVE_LIMIT = 160 * 1024 * 1024
_CODEX_RELEASES = {
    ("darwin", "arm64"): (
        "aarch64-apple-darwin",
        "072a30a65f05666735889ef0f60b56db186adbdde9d5c5cc1a64be0b598530fe",
    ),
    ("darwin", "x86_64"): (
        "x86_64-apple-darwin",
        "4216d7a40aa49d74b65fab93d2a86d2e25a902482b827dbdb3f357777b09fadf",
    ),
    ("linux", "arm64"): (
        "aarch64-unknown-linux-musl",
        "d384f90bc842450b42bd675feef06a12a46a3b1ca97efcb22566b270e4a11227",
    ),
    ("linux", "x86_64"): (
        "x86_64-unknown-linux-musl",
        "bfaf13c9ba34f2ad764e4a916c49cf7177aeba329cf0f719e2227566fc8d662a",
    ),
}
# Exact regular-file sizes from the SHA-256-verified 0.145.0 release archives.
# The compressed download budget is separate from the expanded CLI payload.
_CODEX_BINARY_SIZES = {
    "aarch64-apple-darwin": 271134288,
    "x86_64-apple-darwin": 294456976,
    "aarch64-unknown-linux-musl": 269360944,
    "x86_64-unknown-linux-musl": 310730800,
}
_CODEX_CODING_VERSION = "0.159.3"
_CODEX_CODING_RELEASES = {
    ("darwin", "arm64"): (
        "aarch64-apple-darwin",
        "51de50a39ea592b5b0a64ae0474548c282181cb6b96d9bd08965a682ae12774c",
    ),
    ("darwin", "x86_64"): (
        "x86_64-apple-darwin",
        "cbaea8206d3189b8a7cd7c1b476e14a541ab22d469c37253f758ff71d4ed24b7",
    ),
    ("linux", "arm64"): (
        "aarch64-unknown-linux-musl",
        "cd5f307b3fcd6080773e684b86c3114a67d4f1c61dc447be09876b552eb4bea7",
    ),
    ("linux", "x86_64"): (
        "x86_64-unknown-linux-musl",
        "b48ca1b2d6b1bf42b944e02c3d937c898e24651916684cdc35fdedf31b291bcb",
    ),
}
_CODEX_CODING_BINARY_SIZES = {
    "aarch64-apple-darwin": 240351424,
    "x86_64-apple-darwin": 259442080,
    "aarch64-unknown-linux-musl": 247459224,
    "x86_64-unknown-linux-musl": 287086056,
}
_CODEX_CODE_MODE_HOST_ARCHIVE_LIMIT = 32 * 1024 * 1024
_CODEX_CODE_MODE_HOST_RELEASES = {
    ("darwin", "arm64"): (
        "aarch64-apple-darwin",
        "d1a3254374b733fff1fa31cbeb20f65e3ef871e3431c196353dea189df15d4c5",
    ),
    ("darwin", "x86_64"): (
        "x86_64-apple-darwin",
        "d27385b2c5bc0cd9537154c84abafeeddba38f93e135257f1cf6ca4e862fcb9f",
    ),
    ("linux", "arm64"): (
        "aarch64-unknown-linux-musl",
        "7cbb47c472c2dc115abfeebf52ff11bf66eb364bf8f5d14659742615919b8e6f",
    ),
    ("linux", "x86_64"): (
        "x86_64-unknown-linux-musl",
        "0f58dd9848c717382e5223e39c1fc8f43a8f4a8cbe20d7af0c0dee0ef3abd438",
    ),
}
# Exact regular-file sizes from the SHA-256-verified companion archives.
_CODEX_CODE_MODE_HOST_BINARY_SIZES = {
    "aarch64-apple-darwin": 65392880,
    "x86_64-apple-darwin": 69395776,
    "aarch64-unknown-linux-musl": 66921472,
    "x86_64-unknown-linux-musl": 74072976,
}
_OUTPUT_LIMIT = 64 * 1024
_APP_SERVER_OUTPUT_LIMIT = 1024 * 1024
_APP_SERVER_TIMEOUT = 15.0
_CODEX_ACP_PACKAGE = "@agentclientprotocol/codex-acp@2.1.1"
_CODEX_ACP_IMAGE_ENTRYPOINT = Path("/opt/coding-agent-acp/node_modules/@agentclientprotocol/codex-acp/dist/index.js")
_DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4,}-[A-Z0-9]{4,}\b")
_URL_RE = re.compile(r"https://[^\s\x1b]+")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SAFE_ENV = {
    "ALL_PROXY",
    "CODEX_ACCESS_TOKEN",
    "CODEX_API_KEY",
    "CODEX_CA_CERTIFICATE",
    "COMSPEC",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
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
    acp_transport: ACPTransport
    acp_command_env: str | None
    timeout_env: str


@dataclass(frozen=True)
class ACPLaunchDescriptor:
    """Complete, provider-neutral description for starting one ACP server."""

    agent_id: str
    agent_name: str
    argv: tuple[str, ...]
    environment: dict[str, str]
    timeout_seconds: float
    transport: ACPTransport


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
        acp_transport="native",
        acp_command_env=None,
        timeout_env="OPENCODE_TIMEOUT",
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
        acp_transport="bridge",
        acp_command_env="CODEX_ACP_COMMAND",
        timeout_env="CODEX_TIMEOUT",
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


def _platform_release(releases: dict[tuple[str, str], tuple[str, str]]) -> tuple[str, str] | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x64"}:
        machine = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        machine = "arm64"
    return releases.get((system, machine))


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
        self._auth_generations: dict[str, int] = {}
        self._logout_counts: dict[str, int] = {}
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

    def _coding_root(self) -> Path:
        return self.agent_root("codex") / "coding" / _CODEX_CODING_VERSION

    def _managed_coding_command(self) -> Path:
        return self._coding_root() / "bin" / self.managed_command("codex").name

    def _managed_code_mode_host(self) -> Path:
        return self._coding_root() / "bin" / "codex-code-mode-host"

    def _code_mode_host_file_ready(self) -> bool:
        host = self._managed_code_mode_host()
        release = _platform_release(_CODEX_CODE_MODE_HOST_RELEASES)
        if release is None or host.parent.is_symlink() or host.is_symlink():
            return False
        try:
            return (
                host.is_file()
                and host.stat().st_size == _CODEX_CODE_MODE_HOST_BINARY_SIZES[release[0]]
                and os.access(host, os.X_OK)
            )
        except OSError:
            return False

    async def _probe_code_mode_host(self, host: Path) -> bool:
        # The companion does not implement --version. Its pinned archive
        # establishes identity; --help verifies that the executable can run.
        code, output = await self._run_probe([str(host), "--help"], agent_id="codex")
        return code == 0 and output.startswith("Usage: codex-code-mode-host [OPTIONS]") and "--listen <URL>" in output

    async def _code_mode_host_ready(self) -> bool:
        return self._code_mode_host_file_ready() and await self._probe_code_mode_host(self._managed_code_mode_host())

    def _uses_managed_coding_cli(self, agent_id: str, command: list[str]) -> bool:
        return (
            agent_id == "codex"
            and not os.getenv("CODEX_COMMAND", "").strip()
            and Path(command[0]) == self._managed_coding_command()
        )

    async def ensure_coding_ready(self, agent_id: str) -> None:
        """Reject an unusable managed helper before a coding model session."""
        command = self.coding_command(agent_id)
        if command and self._uses_managed_coding_cli(agent_id, command) and not await self._code_mode_host_ready():
            raise CodingAgentRuntimeError(
                "Codex code mode is unavailable; repair the managed Codex installation before running coding tasks",
                code="coding_agent_code_mode_unavailable",
            )

    def coding_command(self, agent_id: str) -> list[str] | None:
        """Select coding execution without changing the pinned primary command."""
        if agent_id == "codex" and not os.getenv("CODEX_COMMAND", "").strip():
            upgraded = self._managed_coding_command()
            if upgraded.is_file():
                return [str(upgraded)]
        return self.command(agent_id)

    def inference_command(self, agent_id: str) -> list[str] | None:
        """Reuse the pinned upgraded CLI while retaining legacy/explicit commands."""
        if agent_id == "codex" and not os.getenv("CODEX_COMMAND", "").strip():
            upgraded = self._managed_coding_command()
            if upgraded.is_file():
                return [str(upgraded.absolute())]
        return self.command(agent_id)

    def inference_state_dir(self, agent_id: str) -> Path:
        """Keep upgraded inference caches and configuration apart from coding."""
        if (
            agent_id == "codex"
            and not os.getenv("CODEX_COMMAND", "").strip()
            and self._managed_coding_command().is_file()
        ):
            return (self.agent_root(agent_id) / "inference" / _CODEX_CODING_VERSION / "state").absolute()
        return self.state_dir(agent_id)

    def inference_environment(self, agent_id: str) -> dict[str, str]:
        """Share only the CLI-owned login with the managed inference profile."""
        environment = self.process_environment(agent_id)
        home = self.inference_state_dir(agent_id)
        if home == self.state_dir(agent_id):
            return environment
        self._prepare_inference_home(home)
        environment.update({"CODEX_HOME": str(home), "HOME": str(home)})
        return environment

    def _prepare_inference_home(self, home: Path) -> None:
        for directory in (home.parent.parent, home.parent, home):
            if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
                raise CodingAgentRuntimeError(
                    "Invalid managed Codex inference directory", code="coding_agent_auth_invalid"
                )
            directory.mkdir(exist_ok=True, mode=0o700)
            if directory.is_symlink() or not directory.is_dir():
                raise CodingAgentRuntimeError(
                    "Invalid managed Codex inference directory", code="coding_agent_auth_invalid"
                )
            with contextlib.suppress(OSError):
                directory.chmod(0o700)

        source_auth = (self.state_dir("codex") / "auth.json").absolute()
        alias = home / "auth.json"
        if alias.is_symlink():
            if alias.readlink() != source_auth:
                raise CodingAgentRuntimeError("Invalid managed Codex login link", code="coding_agent_auth_invalid")
        elif alias.exists():
            raise CodingAgentRuntimeError("Unexpected independent Codex login", code="coding_agent_auth_invalid")
        else:
            try:
                alias.symlink_to(source_auth)
            except FileExistsError:
                if not alias.is_symlink() or alias.readlink() != source_auth:
                    raise CodingAgentRuntimeError("Invalid managed Codex login link", code="coding_agent_auth_invalid")

    def coding_environment(self, agent_id: str) -> dict[str, str]:
        """Isolate coding caches while leaving authentication CLI-owned."""
        environment = self.process_environment(agent_id)
        if (
            agent_id != "codex"
            or os.getenv("CODEX_COMMAND", "").strip()
            or not self._managed_coding_command().is_file()
        ):
            return environment
        home = self._prepare_coding_home()
        environment.update({"CODEX_HOME": str(home), "HOME": str(home)})
        return environment

    def _prepare_coding_home(self) -> Path:
        home = self._coding_root() / "state"
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            home.chmod(0o700)
        source_auth = (self.state_dir("codex") / "auth.json").absolute()
        alias = home / "auth.json"
        if alias.is_symlink():
            if alias.resolve() != source_auth.resolve():
                raise CodingAgentRuntimeError("Invalid managed Codex login link", code="coding_agent_auth_invalid")
        elif alias.exists():
            raise CodingAgentRuntimeError("Unexpected independent Codex login", code="coding_agent_auth_invalid")
        else:
            try:
                alias.symlink_to(source_auth)
            except FileExistsError:
                # Concurrent catalog/ACP startup may have created the same alias.
                if not alias.is_symlink() or alias.resolve() != source_auth.resolve():
                    raise CodingAgentRuntimeError("Invalid managed Codex login link", code="coding_agent_auth_invalid")
        return home

    @staticmethod
    def _resolve_configured_command(value: str, *, setting: str) -> list[str]:
        try:
            argv = shlex.split(value, posix=os.name != "nt")
        except ValueError as exc:
            raise CodingAgentRuntimeError(
                f"Invalid {setting}: {exc!s}",
                code="coding_agent_command_invalid",
            ) from exc
        if not argv:
            raise CodingAgentRuntimeError(
                f"{setting} must name an executable",
                code="coding_agent_command_invalid",
            )
        executable = argv[0]
        if Path(executable).is_absolute():
            if not Path(executable).is_file():
                raise CodingAgentRuntimeError(
                    f"{setting} executable does not exist",
                    code="coding_agent_acp_unavailable",
                )
            return argv
        resolved = shutil.which(executable)
        if resolved is None:
            raise CodingAgentRuntimeError(
                f"{setting} executable is unavailable",
                code="coding_agent_acp_unavailable",
            )
        return [resolved, *argv[1:]]

    def _bridge_command(self, spec: CodingAgentSpec) -> list[str]:
        if spec.acp_command_env and (configured := os.getenv(spec.acp_command_env, "").strip()):
            return self._resolve_configured_command(configured, setting=spec.acp_command_env)

        node = shutil.which("node")
        if node and _CODEX_ACP_IMAGE_ENTRYPOINT.is_file():
            return [node, str(_CODEX_ACP_IMAGE_ENTRYPOINT)]
        if global_bridge := shutil.which("codex-acp"):
            return [global_bridge]
        raise CodingAgentRuntimeError(
            f"{spec.name} requires the pinned {_CODEX_ACP_PACKAGE} bridge; install it or set {spec.acp_command_env}",
            code="coding_agent_acp_unavailable",
        )

    def acp_launch(
        self,
        agent_id: str,
        *,
        native_model: str | None = None,
        extra_environment: dict[str, str] | None = None,
    ) -> ACPLaunchDescriptor:
        """Resolve an Agent into the single ACP execution contract."""

        spec = spec_for(agent_id)
        command = self.coding_command(agent_id)
        if command is None:
            raise CodingAgentRuntimeError("Coding agent is not installed", code="coding_agent_not_installed")
        if self._uses_managed_coding_cli(agent_id, command) and not self._code_mode_host_file_ready():
            raise CodingAgentRuntimeError(
                "Codex code mode is unavailable; repair the managed Codex installation before running coding tasks",
                code="coding_agent_code_mode_unavailable",
            )

        if spec.acp_transport == "native":
            argv = [*command, "acp"]
        else:
            if len(command) != 1:
                raise CodingAgentRuntimeError(
                    f"{spec.command_env} must resolve to one executable for the ACP bridge",
                    code="coding_agent_command_invalid",
                )
            argv = self._bridge_command(spec)

        environment = self.coding_environment(agent_id)
        if extra_environment:
            environment.update(extra_environment)
        if agent_id == "codex":
            environment.update(
                {
                    "CODEX_PATH": command[0],
                    "INITIAL_AGENT_MODE": "agent",
                    "NO_BROWSER": "1",
                }
            )
            if native_model:
                environment["CODEX_CONFIG"] = json.dumps({"model": native_model}, separators=(",", ":"))

        raw_timeout = os.getenv(spec.timeout_env, "600.0")
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise CodingAgentRuntimeError(
                f"{spec.timeout_env} must be a number",
                code="coding_agent_configuration_error",
            ) from exc
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise CodingAgentRuntimeError(
                f"{spec.timeout_env} must be finite and positive",
                code="coding_agent_configuration_error",
            )
        return ACPLaunchDescriptor(
            agent_id=agent_id,
            agent_name=spec.name,
            argv=tuple(argv),
            environment=environment,
            timeout_seconds=timeout_seconds,
            transport=spec.acp_transport,
        )

    async def _run_probe(self, argv: list[str], *, agent_id: str) -> tuple[int, str]:
        proc: asyncio.subprocess.Process | None = None
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
        finally:
            if proc is not None:
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                # communicate resumes cancelled pipe readers and awaits EOF/reaping.
                await proc.communicate()
                # Process has no public close API; EOF alone can leave its transport
                # open on Unix until a later callback, beyond the caller's loop.
                proc._transport.close()

    async def status(self, agent_id: str) -> dict[str, Any]:
        spec = spec_for(agent_id)
        command = self.coding_command(agent_id)
        managed_codex = agent_id == "codex" and not os.getenv("CODEX_COMMAND", "").strip()
        update_available = (
            managed_codex
            and self.command(agent_id) is not None
            and (not self._managed_coding_command().is_file() or not self._code_mode_host_file_ready())
        )
        update = {
            "update_available": update_available,
            "target_version": _CODEX_CODING_VERSION if managed_codex else "",
        }
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
                **update,
            }
        version_code, version = await self._run_probe([*command, "--version"], agent_id=agent_id)
        installed = version_code == 0
        authenticated: bool | None = None
        auth_state: AuthState = "not_required"
        detail = ""
        if spec.auth_methods and installed:
            login_command = self.command(agent_id) or command
            login_code, detail = await self._run_probe([*login_command, "login", "status"], agent_id=agent_id)
            authenticated = login_code == 0
            auth_state = "signed_in" if authenticated else "signed_out"
            active_auth = self._auth_sessions.get(agent_id)
            if active_auth and active_auth["status"] in {"starting", "waiting"}:
                auth_state = active_auth["status"]
            if self._logout_counts.get(agent_id, 0):
                authenticated = False
                auth_state = "signed_out"
        available = installed
        if installed and self._uses_managed_coding_cli(agent_id, command) and not await self._code_mode_host_ready():
            available = False
            detail = "Codex code mode is unavailable; repair the managed Codex installation"
            update["update_available"] = True
        if installed and spec.acp_transport == "bridge":
            try:
                self._bridge_command(spec)
            except CodingAgentRuntimeError as exc:
                if available:
                    detail = str(exc)
                available = False
        operation = self._active_install(agent_id)
        install_state = "installed" if installed else "failed"
        if agent_id in self._install_tasks:
            install_state = "installing"
        elif operation and operation["status"] == "failed":
            install_state = "failed"
        return {
            "installed": installed,
            "install_state": install_state,
            "install_operation": operation,
            "available": available,
            "authenticated": authenticated,
            "auth_state": auth_state,
            "version": version if installed else "",
            "status_detail": detail,
            **update,
        }

    async def models(self, agent_id: str) -> dict[str, Any]:
        """Return the native model catalog advertised by the coding agent."""

        spec = spec_for(agent_id)
        if spec.catalog_source != "agent":
            raise CodingAgentRuntimeError(
                "This coding agent does not expose a native model catalog",
                code="model_catalog_unsupported",
            )
        command = self.coding_command(agent_id)
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
                env=self.coding_environment(agent_id),
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
                    "clientInfo": {"name": "ambient-agent", "title": "Ambient Agent", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            try:
                proc.stdin.write(b'{"method":"initialized","params":{}}\n')
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                raise CodingAgentRuntimeError(
                    "Model catalog process closed before completing initialization",
                    code="model_catalog_failed",
                ) from exc
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
            task = self._install_tasks.get(agent_id)
            if task and not task.done():
                return self._active_install(agent_id) or {}
            if self.command(agent_id) and (
                os.getenv(spec.command_env, "").strip()
                or (self._managed_coding_command().is_file() and await self._code_mode_host_ready())
            ):
                return {
                    "id": "installed",
                    "agent_id": agent_id,
                    "status": "installed",
                    "created_at": time.time(),
                    "error": "",
                }
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
                if not self.command(agent_id):
                    await self._install_codex(operation_id)
                await self._install_coding_codex(operation_id)
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
        await self._install_codex_release(
            operation_id, self.agent_root("codex"), _CODEX_VERSION, _CODEX_RELEASES, _CODEX_BINARY_SIZES
        )

    async def _install_coding_codex(self, operation_id: str) -> None:
        self.process_environment("codex")
        self._prepare_coding_home()
        if not self._managed_coding_command().is_file():
            await self._install_codex_release(
                operation_id + "-coding",
                self._coding_root(),
                _CODEX_CODING_VERSION,
                _CODEX_CODING_RELEASES,
                _CODEX_CODING_BINARY_SIZES,
            )
        if not await self._code_mode_host_ready():
            await self._install_code_mode_host(operation_id)

    async def _install_code_mode_host(self, operation_id: str) -> None:
        await self._install_codex_release(
            operation_id + "-code-mode-host",
            self._coding_root(),
            _CODEX_CODING_VERSION,
            _CODEX_CODE_MODE_HOST_RELEASES,
            _CODEX_CODE_MODE_HOST_BINARY_SIZES,
            binary_name="codex-code-mode-host",
            archive_limit=_CODEX_CODE_MODE_HOST_ARCHIVE_LIMIT,
        )

    async def _install_codex_release(
        self,
        operation_id: str,
        agent_root: Path,
        release_version: str,
        releases: dict[tuple[str, str], tuple[str, str]],
        binary_sizes: dict[str, int],
        *,
        binary_name: str = "codex",
        archive_limit: int = _CODEX_ARCHIVE_LIMIT,
    ) -> None:
        agent_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        staging = agent_root / f".install-{operation_id}"
        staging.mkdir(mode=0o700)
        install_dir = staging / "bin"
        install_dir.mkdir(mode=0o700)
        try:
            release = _platform_release(releases)
            if release is None:
                raise CodingAgentRuntimeError(
                    "Codex managed installation does not support this operating system or architecture",
                    code="install_unsupported",
                )
            target, expected_sha256 = release
            expected_binary_size = binary_sizes[target]
            asset_name = f"{binary_name}-{target}.tar.gz"
            asset_url = f"https://github.com/openai/codex/releases/download/rust-v{release_version}/{asset_name}"
            archive_path = staging / asset_name
            digest = hashlib.sha256()
            size = 0
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", asset_url) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > archive_limit:
                        raise CodingAgentRuntimeError("Codex release asset is too large", code="install_failed")
                    with archive_path.open("xb") as archive_file:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > archive_limit:
                                raise CodingAgentRuntimeError("Codex release asset is too large", code="install_failed")
                            digest.update(chunk)
                            archive_file.write(chunk)
            if size == 0:
                raise CodingAgentRuntimeError("Codex release asset was empty", code="install_failed")
            if digest.hexdigest() != expected_sha256:
                raise CodingAgentRuntimeError(
                    "Codex release checksum verification failed",
                    code="install_failed",
                )

            binary = install_dir / binary_name
            expected_member = f"{binary_name}-{target}"
            try:
                with tarfile.open(archive_path, mode="r:gz") as archive:
                    if binary_name == "codex-code-mode-host" and (
                        len(archive.getmembers()) != 1 or archive.getmembers()[0].name != expected_member
                    ):
                        raise CodingAgentRuntimeError("Codex companion archive was invalid", code="install_failed")
                    member = archive.getmember(expected_member)
                    if not member.isfile() or member.size != expected_binary_size:
                        raise CodingAgentRuntimeError(
                            "Codex release did not contain the expected CLI binary",
                            code="install_failed",
                        )
                    source = archive.extractfile(member)
                    if source is None:
                        raise CodingAgentRuntimeError(
                            "Codex release did not contain the expected CLI binary",
                            code="install_failed",
                        )
                    with source, binary.open("xb") as destination_file:
                        shutil.copyfileobj(source, destination_file)
                    if binary.stat().st_size != expected_binary_size:
                        raise CodingAgentRuntimeError(
                            "Codex release binary size verification failed", code="install_failed"
                        )
            except (KeyError, tarfile.TarError, OSError) as exc:
                raise CodingAgentRuntimeError(
                    "Codex release archive was invalid",
                    code="install_failed",
                ) from exc
            binary.chmod(0o700)
            if binary_name == "codex-code-mode-host":
                if not await self._probe_code_mode_host(binary):
                    raise CodingAgentRuntimeError("Installed Codex companion failed validation", code="install_failed")
            else:
                code, version = await self._run_probe([str(binary), "--version"], agent_id="codex")
                if code != 0 or version != f"codex-cli {release_version}":
                    raise CodingAgentRuntimeError(
                        f"Installed Codex failed validation: {version}", code="install_failed"
                    )
            destination = agent_root / "bin"
            if binary_name == "codex-code-mode-host":
                if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
                    raise CodingAgentRuntimeError("Invalid managed Codex binary directory", code="install_failed")
                destination.mkdir(exist_ok=True, mode=0o700)
                # Atomic replacement repairs a missing/damaged companion without
                # replacing the already installed CLI or touching shared login.
                binary.replace(destination / binary_name)
            else:
                if destination.exists():
                    raise CodingAgentRuntimeError("Codex became installed concurrently", code="install_conflict")
                install_dir.replace(destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def authentication_generation(self, agent_id: str) -> int:
        """Invalidate pending consumers when native authentication changes."""
        spec_for(agent_id)
        return self._auth_generations.get(agent_id, 0)

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
            self._auth_generations[agent_id] = self.authentication_generation(agent_id) + 1
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
            self._auth_generations[agent_id] = self.authentication_generation(agent_id) + 1
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return self.auth_session(agent_id)

    async def logout(self, agent_id: str) -> dict[str, Any]:
        spec = spec_for(agent_id)
        if not spec.auth_methods:
            raise CodingAgentRuntimeError("This coding agent has no native login", code="auth_not_required")
        # Invalidate before the asynchronous CLI logout, including after restart
        # when there is no in-memory auth session to mark as cancelled.
        self._auth_generations[agent_id] = self.authentication_generation(agent_id) + 1
        self._logout_counts[agent_id] = self._logout_counts.get(agent_id, 0) + 1
        try:
            await self.cancel_auth(agent_id)
            command = self.command(agent_id)
            if command is None:
                raise CodingAgentRuntimeError("Coding agent is not installed", code="coding_agent_not_installed")
            code, output = await self._run_probe([*command, "logout"], agent_id=agent_id)
            if code != 0:
                raise CodingAgentRuntimeError(f"Unable to sign out: {output}", code="auth_logout_failed")
            self._auth_sessions.pop(agent_id, None)
            return self.auth_session(agent_id)
        finally:
            self._logout_counts[agent_id] -= 1
            self._auth_generations[agent_id] = self.authentication_generation(agent_id) + 1

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
