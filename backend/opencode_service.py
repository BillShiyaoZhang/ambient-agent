import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp import Client, connect_to_agent, default_environment, text_block
from acp.exceptions import RequestError
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    CreateTerminalResponse,
    DeniedOutcome,
    EnvVariable,
    FileSystemCapabilities,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalExitStatus,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

from backend.app_manifest import AppManifest, ManifestValidationError, validate_app_id

logger = logging.getLogger("opencode_service")

_DEFAULT_TERMINAL_OUTPUT_BYTE_LIMIT = 256 * 1024
_MAX_TERMINAL_OUTPUT_BYTE_LIMIT = 4 * 1024 * 1024
_MAX_CONTROLLER_BYTES = 2 * 1024 * 1024
_PROCESS_TERMINATION_GRACE_SECONDS = 2.0
_SHELL_CONTROL_PATTERN = re.compile(r"[\x00\r\n;&|<>`]|\$\(")
_TERMINAL_INHERITED_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_COLOR",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
}
_TERMINAL_REQUEST_ENV = {
    "CI",
    "FORCE_COLOR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NODE_ENV",
    "NO_COLOR",
    "TZ",
}


class OpenCodeAgentError(RuntimeError):
    """Base error for a failed OpenCode execution."""


class OpenCodeACPError(OpenCodeAgentError):
    """Base error for a failed OpenCode ACP execution."""


class OpenCodeACPInputError(OpenCodeACPError, ValueError):
    """Raised before execution when an App ID or path is unsafe."""


class OpenCodeACPStartupError(OpenCodeACPError):
    """Raised when the OpenCode ACP process cannot be started."""


class OpenCodeACPTimeoutError(OpenCodeACPError, TimeoutError):
    """Raised when the OpenCode ACP turn exceeds its deadline."""


class OpenCodeACPProtocolError(OpenCodeACPError):
    """Raised when ACP initialization, session creation, or prompting fails."""


class OpenCodeArtifactError(OpenCodeACPError):
    """Raised when staged output is missing or malformed."""


@dataclass(frozen=True, slots=True)
class OpenCodeStagedResult:
    """A validated staged App whose caller must either promote or discard."""

    output: str
    app_id: str
    staging_dir: Path
    live_dir: Path


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _resolve_in_workspace(path_str: str | Path, workspace_root: Path) -> Path:
    """Resolve a path and require that it remains inside a real workspace directory."""
    if not isinstance(path_str, (str, os.PathLike)):
        raise ValueError("Path must be a string or path-like value")
    try:
        root = workspace_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid workspace root: {exc!s}") from exc
    if not root.is_dir():
        raise ValueError("Workspace root is not a directory")

    raw_path = Path(path_str)
    if ".." in raw_path.parts:
        raise ValueError("Directory traversal attempt blocked")
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Invalid path: {exc!s}") from exc
    if resolved != root and not resolved.is_relative_to(root):
        raise ValueError("Directory traversal attempt blocked")

    # Reject links even when they happen to point back into the jail. This avoids
    # link-swap surprises between validation and the subsequent filesystem call.
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Directory traversal attempt blocked") from exc
    cursor = root
    for part in relative.parts:
        cursor /= part
        if _is_link_or_junction(cursor):
            raise ValueError("Symbolic links are not allowed in the workspace path")
    return resolved


def _parse_command_argv(command: str, args: list[str] | None = None) -> list[str]:
    """Parse ACP command fields into argv without ever invoking a shell."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Command must be a non-empty string")
    supplied_args = [] if args is None else args
    if not isinstance(supplied_args, list) or not all(isinstance(arg, str) for arg in supplied_args):
        raise ValueError("Command args must be a list of strings")
    if _SHELL_CONTROL_PATTERN.search(command):
        raise ValueError("Shell control syntax is not allowed")
    if any("\x00" in arg for arg in supplied_args):
        raise ValueError("Command args must not contain null bytes")
    try:
        command_parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise ValueError(f"Invalid command quoting: {exc!s}") from exc
    if supplied_args and len(command_parts) != 1:
        raise ValueError("Command must contain only the executable when args are supplied")
    argv = [*command_parts, *supplied_args]
    if not argv:
        raise ValueError("Command must include an executable")
    return argv


def _safe_terminal_environment(env: list[EnvVariable] | None) -> dict[str, str]:
    process_env = {name: value for name in _TERMINAL_INHERITED_ENV if (value := os.environ.get(name)) is not None}
    for item in env or []:
        name = getattr(item, "name", None)
        value = getattr(item, "value", None)
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("Environment entries must contain string names and values")
        if name not in _TERMINAL_REQUEST_ENV:
            raise ValueError(f"Environment variable is not allowed: {name}")
        if "\x00" in value:
            raise ValueError(f"Environment variable contains a null byte: {name}")
        process_env[name] = value
    return process_env


async def _terminate_process(
    proc: asyncio.subprocess.Process,
    *,
    process_group: bool,
    grace_seconds: float = _PROCESS_TERMINATION_GRACE_SECONDS,
) -> None:
    """Terminate a process (and, when isolated, its group), then escalate to kill."""
    if proc.returncode is not None:
        return

    def send(sig: signal.Signals) -> None:
        try:
            if process_group and os.name != "nt":
                os.killpg(proc.pid, sig)
            elif sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
        except ProcessLookupError:
            pass

    send(signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        send(signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        await proc.wait()


@contextlib.asynccontextmanager
async def spawn_agent_process(
    to_client: Client,
    command: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    transport_kwargs: Mapping[str, Any] | None = None,
    **connection_kwargs: Any,
) -> AsyncIterator[tuple[Any, asyncio.subprocess.Process]]:
    """Spawn an ACP process in an isolated process group with bounded shutdown."""
    transport_options = dict(transport_kwargs or {})
    shutdown_timeout = float(transport_options.pop("shutdown_timeout", _PROCESS_TERMINATION_GRACE_SECONDS))
    stream_limit = int(transport_options.pop("limit", 1024 * 1024))
    if transport_options:
        raise ValueError(f"Unsupported ACP transport options: {', '.join(sorted(transport_options))}")

    process_env = dict(default_environment())
    if env:
        process_env.update(env)
    process_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_kwargs["start_new_session"] = True

    proc = await asyncio.create_subprocess_exec(
        command,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd is not None else None,
        env=process_env,
        limit=stream_limit,
        **process_kwargs,
    )
    if proc.stdin is None or proc.stdout is None or proc.stderr is None:
        await _terminate_process(proc, process_group=True, grace_seconds=shutdown_timeout)
        raise OpenCodeACPStartupError("OpenCode ACP process did not expose stdio pipes")

    async def drain_stderr() -> None:
        while await proc.stderr.read(4096):
            pass

    stderr_task = asyncio.create_task(drain_stderr())
    try:
        conn = connect_to_agent(to_client, proc.stdin, proc.stdout, **connection_kwargs)
        try:
            yield conn, proc
        finally:
            await conn.close()
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
            with contextlib.suppress(Exception):
                await proc.stdin.wait_closed()
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=shutdown_timeout)
            except TimeoutError:
                await _terminate_process(proc, process_group=True, grace_seconds=shutdown_timeout)
        if not stderr_task.done():
            stderr_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stderr_task


def _prepare_staging_app(apps_dir: str | Path, app_id: str) -> tuple[Path, Path]:
    """Copy an existing App into a private sibling staging directory."""
    staging_dir: Path | None = None
    try:
        validate_app_id(app_id)
    except ManifestValidationError as exc:
        raise OpenCodeACPInputError(f"invalid app_id: {exc!s}") from exc

    try:
        apps_root = Path(apps_dir).resolve(strict=False)
        apps_root.mkdir(parents=True, exist_ok=True)
        live_dir = apps_root / app_id
        if _is_link_or_junction(live_dir):
            raise OpenCodeACPInputError("App path must not be a symbolic link or junction")
        if live_dir.exists() and not live_dir.is_dir():
            raise OpenCodeACPInputError("App path must be a directory")
        if live_dir.resolve(strict=False).parent != apps_root:
            raise OpenCodeACPInputError("App path must be a direct child of the Apps directory")

        staging_dir = apps_root / f".{app_id}.staging-{uuid.uuid4().hex}"
        if live_dir.exists():
            for path in live_dir.rglob("*"):
                if _is_link_or_junction(path):
                    raise OpenCodeACPInputError(f"Existing App contains an unsafe link: {path.relative_to(live_dir)}")
            shutil.copytree(live_dir, staging_dir)
        else:
            staging_dir.mkdir()
        return live_dir, staging_dir
    except OpenCodeACPError:
        raise
    except (OSError, RuntimeError) as exc:
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise OpenCodeACPStartupError(f"Unable to prepare coding-agent staging directory: {exc!s}") from exc


def _validate_staged_app(staging_dir: Path, app_id: str) -> None:
    controller_path = _resolve_in_workspace("controller.js", staging_dir)
    if not controller_path.is_file():
        raise OpenCodeArtifactError("Coding agent did not produce the required controller.js artifact")
    try:
        raw = controller_path.read_bytes()
    except OSError as exc:
        raise OpenCodeArtifactError(f"Unable to read generated controller.js: {exc!s}") from exc
    if not raw or len(raw) > _MAX_CONTROLLER_BYTES:
        raise OpenCodeArtifactError("Generated controller.js is empty or exceeds the size limit")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OpenCodeArtifactError("Generated controller.js must be valid UTF-8") from exc
    if "\x00" in source or re.search(r"\bexport\s+default\b", source) is None:
        raise OpenCodeArtifactError("Generated controller.js must contain a default export")

    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.is_file():
        raise OpenCodeArtifactError("Coding agent did not produce the required Manifest V2 artifact")
    try:
        AppManifest.read(manifest_path, expected_app_id=app_id)
    except ManifestValidationError as exc:
        raise OpenCodeArtifactError(
            f"App manifest validation failed: {exc!s}. Fix manifest.json according to the App Runtime Contract."
        ) from exc

    allowed_names = {".ambient-promotion.json", "README.md", "controller.js", "data", "manifest.json"}
    unexpected = sorted(path.name for path in staging_dir.iterdir() if path.name not in allowed_names)
    if unexpected:
        raise OpenCodeArtifactError(
            f"App contains unsupported files outside the Runtime Contract: {', '.join(unexpected)}"
        )

    verifier = Path(__file__).resolve().parent.parent / "scripts" / "verify_widget_controller.mjs"
    node_executable = shutil.which("node")
    if node_executable is None or not verifier.is_file():
        raise OpenCodeArtifactError("Widget syntax/runtime verifier is unavailable")
    try:
        completed = subprocess.run(
            [node_executable, str(verifier), str(controller_path)],
            cwd=staging_dir,
            env=_safe_terminal_environment(None),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenCodeArtifactError(f"Widget syntax/runtime verification failed: {exc!s}") from exc
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout or "unknown verifier error").strip()
        try:
            structured = json.loads(diagnostic)
        except json.JSONDecodeError:
            structured = None
        if isinstance(structured, dict):
            code = str(structured.get("code") or "widget_verification_failed")
            message = str(structured.get("message") or "Widget verification failed")
            hint = str(structured.get("hint") or "Fix the generated App and retry validation.")
            raise OpenCodeArtifactError(
                f"Widget syntax/runtime/security verification failed [{code}]: {message}\nSuggested fix: {hint}"
            )
        raise OpenCodeArtifactError(
            f"Widget syntax/runtime/security verification failed: {diagnostic[:_DEFAULT_TERMINAL_OUTPUT_BYTE_LIMIT]}"
        )


def _promote_staging_app(staging_dir: Path, live_dir: Path) -> None:
    """Promote a validated sibling directory, restoring the old App if the swap fails."""
    if not live_dir.exists():
        staging_dir.replace(live_dir)
        return

    backup_dir = live_dir.parent / f".{live_dir.name}.backup-{uuid.uuid4().hex}"
    journal = live_dir.parent / f".ambient-promotion-{live_dir.name}-{uuid.uuid4().hex}.json"
    journal.write_text(
        json.dumps(
            {
                "app_id": live_dir.name,
                "live_name": live_dir.name,
                "staging_name": staging_dir.name,
                "backup_name": backup_dir.name,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    live_dir.replace(backup_dir)
    try:
        staging_dir.replace(live_dir)
    except Exception:
        with contextlib.suppress(Exception):
            backup_dir.replace(live_dir)
        with contextlib.suppress(OSError):
            journal.unlink()
        raise
    try:
        shutil.rmtree(backup_dir)
        journal.unlink()
    except OSError:
        # The startup recovery pass will finish cleanup. Keeping the journal is
        # safer than guessing which directory is authoritative after a crash.
        logger.warning("Unable to finalize OpenCode promotion journal %s", journal, exc_info=True)


def _validated_staging_handle(result: OpenCodeStagedResult, *, require_exists: bool) -> tuple[Path, Path]:
    if not isinstance(result, OpenCodeStagedResult):
        raise TypeError("staged result must be an OpenCodeStagedResult")
    try:
        validate_app_id(result.app_id)
    except ManifestValidationError as exc:
        raise OpenCodeACPInputError(f"invalid staged app_id: {exc!s}") from exc

    try:
        apps_root = result.live_dir.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise OpenCodeACPInputError(f"Invalid Apps directory: {exc!s}") from exc
    expected_live = apps_root / result.app_id
    if result.live_dir.resolve(strict=False) != expected_live:
        raise OpenCodeACPInputError("Staged live path does not match its App ID")

    staging_dir = result.staging_dir
    expected_name = re.compile(rf"\.{re.escape(result.app_id)}\.staging-[0-9a-f]{{32}}")
    if staging_dir.parent.resolve(strict=False) != apps_root or expected_name.fullmatch(staging_dir.name) is None:
        raise OpenCodeACPInputError("Staging path is not a recognized direct child of the Apps directory")
    if _is_link_or_junction(staging_dir):
        raise OpenCodeACPInputError("Staging path must not be a symbolic link or junction")
    if require_exists and not staging_dir.is_dir():
        raise OpenCodeACPInputError("Staging directory no longer exists")
    if _is_link_or_junction(expected_live):
        raise OpenCodeACPInputError("Live App path must not be a symbolic link or junction")
    return expected_live, staging_dir


def validate_opencode_staging(result: OpenCodeStagedResult) -> Path:
    """Validate a retained staging result and return its controller artifact path."""
    _, staging_dir = _validated_staging_handle(result, require_exists=True)
    _validate_staged_app(staging_dir, result.app_id)
    return staging_dir / "controller.js"


def promote_opencode_staging(result: OpenCodeStagedResult) -> Path:
    """Revalidate and promote a retained staging result to its live App directory."""
    live_dir, staging_dir = _validated_staging_handle(result, require_exists=True)
    _validate_staged_app(staging_dir, result.app_id)
    try:
        _promote_staging_app(staging_dir, live_dir)
    except Exception as exc:
        raise OpenCodeArtifactError(f"Unable to promote generated App: {exc!s}") from exc
    return live_dir


def validate_opencode_promotion(result: OpenCodeStagedResult, run_id: str) -> Path | None:
    """Return the promoted controller when a matching durable marker exists."""

    live_dir, staging_dir = _validated_staging_handle(result, require_exists=False)
    if staging_dir.exists() or not live_dir.is_dir() or _is_link_or_junction(live_dir):
        return None
    marker = live_dir / ".ambient-promotion.json"
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpenCodeACPProtocolError("Published App has an invalid promotion marker") from exc
    if payload.get("run_id") != run_id:
        return None
    _validate_staged_app(live_dir, result.app_id)
    controller = live_dir / "controller.js"
    artifact_hash = hashlib.sha256(controller.read_bytes()).hexdigest()
    if payload.get("artifact_hash") != artifact_hash:
        raise OpenCodeACPProtocolError("Published App does not match its promotion marker")
    return controller


def discard_opencode_staging(result: OpenCodeStagedResult) -> None:
    """Idempotently discard a retained staging result without touching the live App."""
    _, staging_dir = _validated_staging_handle(result, require_exists=False)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)


# Provider-neutral names for the shared staging contract. The OpenCode names
# remain public compatibility aliases for existing extensions and checkpoints.
CodingAgentStagedResult = OpenCodeStagedResult
validate_coding_agent_staging = validate_opencode_staging
promote_coding_agent_staging = promote_opencode_staging
validate_coding_agent_promotion = validate_opencode_promotion
discard_coding_agent_staging = discard_opencode_staging


def cleanup_orphaned_opencode_staging(
    apps_dir: str | Path,
    *,
    referenced_staging_paths: set[str | Path],
    grace_seconds: float = 3600.0,
    now_epoch: float | None = None,
) -> list[Path]:
    """Remove only old, unreferenced, recognized staging directories."""

    if not isinstance(grace_seconds, (int, float)) or not math.isfinite(grace_seconds) or grace_seconds < 0:
        raise ValueError("grace_seconds must be a finite non-negative number")
    current_time = time.time() if now_epoch is None else now_epoch
    if not isinstance(current_time, (int, float)) or not math.isfinite(current_time):
        raise ValueError("now_epoch must be a finite number")

    root_path = Path(apps_dir)
    if _is_link_or_junction(root_path):
        raise ValueError("Apps directory must not be a symbolic link or junction")
    root = root_path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Apps directory must be a directory")

    referenced: set[Path] = set()
    for raw_path in referenced_staging_paths:
        try:
            candidate = Path(raw_path).resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if candidate.parent == root:
            referenced.add(candidate)

    pattern = re.compile(r"^\.([a-z0-9]+(?:-[a-z0-9]+)*)\.staging-[0-9a-f]{32}$")
    removed: list[Path] = []
    for candidate in root.iterdir():
        match = pattern.fullmatch(candidate.name)
        if match is None or candidate in referenced or _is_link_or_junction(candidate):
            continue
        try:
            validate_app_id(match.group(1))
            if not candidate.is_dir():
                continue
            age_seconds = float(current_time) - candidate.stat(follow_symlinks=False).st_mtime
            if age_seconds < grace_seconds:
                continue
            # Recheck immediately before deletion so a simple link swap fails
            # closed rather than following an out-of-root target.
            if _is_link_or_junction(candidate) or candidate.parent.resolve(strict=True) != root:
                continue
            shutil.rmtree(candidate)
            removed.append(candidate)
        except (ManifestValidationError, OSError, RuntimeError):
            logger.warning("Unable to clean orphan OpenCode staging directory %s", candidate, exc_info=True)
    return removed


def recover_interrupted_opencode_promotions(apps_dir: str | Path) -> list[dict[str, str]]:
    """Recover the two-rename promotion window using its durable journal."""

    root_path = Path(apps_dir)
    if _is_link_or_junction(root_path):
        raise ValueError("Apps directory must not be a symbolic link or junction")
    root = root_path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Apps directory must be a directory")
    journal_pattern = re.compile(r"^\.ambient-promotion-([a-z0-9]+(?:-[a-z0-9]+)*)-[0-9a-f]{32}\.json$")
    recovered: list[dict[str, str]] = []
    for journal in root.iterdir():
        match = journal_pattern.fullmatch(journal.name)
        if match is None or _is_link_or_junction(journal) or not journal.is_file():
            continue
        try:
            payload = json.loads(journal.read_text(encoding="utf-8"))
            app_id = validate_app_id(payload.get("app_id"))
            if app_id != match.group(1) or payload.get("live_name") != app_id:
                raise ValueError("Promotion journal App identity does not match its filename")
            staging_name = str(payload.get("staging_name") or "")
            backup_name = str(payload.get("backup_name") or "")
            if re.fullmatch(rf"\.{re.escape(app_id)}\.staging-[0-9a-f]{{32}}", staging_name) is None:
                raise ValueError("Promotion journal has an invalid staging path")
            if re.fullmatch(rf"\.{re.escape(app_id)}\.backup-[0-9a-f]{{32}}", backup_name) is None:
                raise ValueError("Promotion journal has an invalid backup path")
            live = root / app_id
            staging = root / staging_name
            backup = root / backup_name
            if any(_is_link_or_junction(path) for path in (live, staging, backup)):
                raise ValueError("Promotion journal references an unsafe link")

            if not live.exists() and backup.is_dir():
                # Crash after old live -> backup but before staging -> live.
                backup.replace(live)
                journal.unlink()
                recovered.append({"app_id": app_id, "action": "restored_previous_live"})
            elif live.is_dir() and backup.is_dir() and not staging.exists():
                # Crash after the new staging became live; finish cleanup.
                shutil.rmtree(backup)
                journal.unlink()
                recovered.append({"app_id": app_id, "action": "finalized_new_live"})
            elif live.is_dir() and staging.is_dir() and not backup.exists():
                # Journal was durable before any rename; no effect occurred.
                journal.unlink()
                recovered.append({"app_id": app_id, "action": "cleared_unstarted_journal"})
        except (ManifestValidationError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            logger.error("OpenCode promotion journal requires manual attention: %s", journal, exc_info=True)
    return recovered


_OPENCODE_NPM_BY_PRESET = {
    "openai": "@ai-sdk/openai",
    "openai_responses": "@ai-sdk/openai",
    "anthropic": "@ai-sdk/anthropic",
    "anthropic_compatible": "@ai-sdk/anthropic",
    "gemini": "@ai-sdk/google",
    "vertex_ai": "@ai-sdk/google-vertex",
    "xai": "@ai-sdk/xai",
    "mistral": "@ai-sdk/mistral",
    "cohere": "@ai-sdk/cohere",
    "azure": "@ai-sdk/azure",
    "bedrock": "@ai-sdk/amazon-bedrock",
}


def _opencode_runtime_env() -> dict[str, str]:
    """Build a process-local OpenCode override from the active run snapshot."""
    from backend.llm_runtime import coding_selection
    from backend.llm_service import get_default_llm_store

    selection = coding_selection()
    if selection is None:
        return {}
    resolved = get_default_llm_store().resolve(selection)
    provider_key = f"ambient-{resolved.provider_id}"
    npm = _OPENCODE_NPM_BY_PRESET.get(resolved.preset, "@ai-sdk/openai-compatible")
    options: dict[str, Any] = {}
    if resolved.credentials.get("api_key"):
        options["apiKey"] = resolved.credentials["api_key"]
    if resolved.connection.get("base_url"):
        base_url = str(resolved.connection["base_url"]).rstrip("/")
        if resolved.preset == "ollama" and not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        options["baseURL"] = base_url
    headers = resolved.connection.get("headers")
    if isinstance(headers, dict):
        options["headers"] = dict(headers)
    if resolved.credentials.get("secret_headers"):
        try:
            secret_headers = json.loads(resolved.credentials["secret_headers"])
            if isinstance(secret_headers, dict):
                options["headers"] = {**options.get("headers", {}), **secret_headers}
        except (TypeError, json.JSONDecodeError):
            pass
    if resolved.connection.get("timeout") is not None:
        options["timeout"] = float(resolved.connection["timeout"]) * 1000

    try:
        inline_config = json.loads(os.getenv("OPENCODE_CONFIG_CONTENT", "{}"))
        if not isinstance(inline_config, dict):
            inline_config = {}
    except json.JSONDecodeError:
        inline_config = {}
    providers = inline_config.get("provider")
    if not isinstance(providers, dict):
        providers = {}
    providers[provider_key] = {
        "npm": npm,
        "name": resolved.provider_name,
        "options": options,
        "models": {resolved.model_id: {"name": resolved.model_id}},
    }
    inline_config["provider"] = providers
    inline_config["model"] = f"{provider_key}/{resolved.model_id}"

    environment = {"OPENCODE_CONFIG_CONTENT": json.dumps(inline_config, ensure_ascii=False)}
    credential_env_names = {
        "aws_access_key_id": "AWS_ACCESS_KEY_ID",
        "aws_secret_access_key": "AWS_SECRET_ACCESS_KEY",
        "aws_session_token": "AWS_SESSION_TOKEN",
    }
    for credential, env_name in credential_env_names.items():
        if resolved.credentials.get(credential):
            environment[env_name] = resolved.credentials[credential]
    if resolved.connection.get("region"):
        environment["AWS_REGION"] = str(resolved.connection["region"])
    if resolved.connection.get("profile"):
        environment["AWS_PROFILE"] = str(resolved.connection["profile"])
    return environment


class PermissionPolicyManager:
    def __init__(self, config_path: str = None):
        if config_path is None:
            # Look in backend/opencode_permissions.json relative to this file
            config_path = os.path.join(os.path.dirname(__file__), "opencode_permissions.json")
        self.config_path = config_path
        self.policy = self._load_policy()

    def _load_policy(self) -> dict:
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading permission policy: {e}")
        return {
            "policy_mode": "strict",
            "files": {
                "allowed_filenames": ["controller.js", "manifest.json", "README.md"],
            },
            "commands": {
                "allowed_commands": ["npm test", "npm run build"],
                "allowed_prefixes": [],
                "blocklist": ["rm -rf", "curl", "wget", "sudo", "mv"],
            },
        }

    def validate_file_path(self, path_str: str, workspace_root: Path) -> bool:
        try:
            resolved_path = _resolve_in_workspace(path_str, workspace_root)
        except (OSError, ValueError):
            return False

        # Coding-agent access is an exact artifact allowlist, never a suffix grant.
        filename = resolved_path.name
        allowed_filenames = self.policy.get("files", {}).get("allowed_filenames", [])
        return len(resolved_path.relative_to(workspace_root.resolve()).parts) == 1 and filename in allowed_filenames

    def validate_command(self, command_str: str) -> bool:
        try:
            argv = _parse_command_argv(command_str)
        except ValueError:
            return False
        return self.validate_argv(argv)

    def validate_argv(self, argv: list[str]) -> bool:
        if not argv or not all(isinstance(arg, str) and arg for arg in argv):
            return False
        commands_cfg = self.policy.get("commands", {})
        canonical = shlex.join(argv)

        for blocked in commands_cfg.get("blocklist", []):
            if blocked in canonical:
                return False

        for allowed in commands_cfg.get("allowed_commands", []):
            try:
                if argv == _parse_command_argv(allowed):
                    return True
            except ValueError:
                logger.warning("Ignoring malformed allowed command in %s: %r", self.config_path, allowed)

        return False


class FastAPIACPClient(Client):
    def __init__(self, workspace_root: Path, on_update_callback: Callable[[str], None]):
        self.workspace_root = workspace_root.resolve(strict=True)
        if not self.workspace_root.is_dir():
            raise ValueError("ACP workspace root must be a directory")
        self.on_update_callback = on_update_callback
        self.terminals: dict[str, asyncio.subprocess.Process] = {}
        self.terminal_buffers: dict[str, bytes] = {}
        self.terminal_tasks: dict[str, asyncio.Task] = {}
        self.terminal_output_limits: dict[str, int] = {}
        self.terminal_output_bytes: dict[str, int] = {}
        self.terminal_output_truncated: dict[str, bool] = {}
        self.terminal_process_groups: set[str] = set()
        self.output_buffer: list[str] = []

    def _artifact_path(self, path: str) -> Path:
        full_path = _resolve_in_workspace(path, self.workspace_root)
        relative = full_path.relative_to(self.workspace_root)
        if len(relative.parts) != 1 or relative.name not in {"README.md", "controller.js", "manifest.json"}:
            raise ValueError("Coding agents may access only README.md, controller.js, and manifest.json")
        return full_path

    async def read_text_file(
        self, session_id: str, path: str, line: int | None = None, limit: int | None = None, **kwargs: Any
    ) -> ReadTextFileResponse:
        try:
            full_path = self._artifact_path(path)
        except ValueError as exc:
            message = str(exc)
            if "traversal" in message.lower() or "symbolic" in message.lower():
                message = "Directory traversal attempt blocked."
            raise RequestError.invalid_params(message)

        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
            return ReadTextFileResponse(content=content)
        except Exception as e:
            raise RequestError.internal_error(str(e))

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        try:
            full_path = self._artifact_path(path)
        except ValueError as exc:
            message = str(exc)
            if "traversal" in message.lower() or "symbolic" in message.lower():
                message = "Directory traversal attempt blocked."
            raise RequestError.invalid_params(message)

        try:
            os.makedirs(full_path.parent, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return WriteTextFileResponse()
        except Exception as e:
            raise RequestError.internal_error(str(e))

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[PermissionOption], **kwargs: Any
    ) -> RequestPermissionResponse:
        policy_mgr = PermissionPolicyManager()
        tool_kind = getattr(tool_call, "kind", "other")
        logger.info(
            f"request_permission request received: tool_kind={tool_kind}, title={getattr(tool_call, 'title', None)}, raw_input={getattr(tool_call, 'raw_input', None)}"
        )

        is_allowed = False
        details = ""

        if tool_kind == "execute":
            cmd = ""
            args: list[str] = []
            raw_input = getattr(tool_call, "raw_input", None)
            if isinstance(raw_input, dict):
                cmd = raw_input.get("command", "")
                args = raw_input.get("args", [])
            elif isinstance(getattr(tool_call, "title", None), str):
                cmd = tool_call.title

            try:
                argv = _parse_command_argv(cmd, args)
            except ValueError as exc:
                return RequestPermissionResponse(
                    outcome=DeniedOutcome(outcome="cancelled", message=f"Invalid command: {exc!s}")
                )
            details = f"Command: {shlex.join(argv)}"
            is_allowed = policy_mgr.validate_argv(argv)

        elif tool_kind in ("edit", "read", "delete", "move"):
            # File system operations
            path_str = ""
            raw_in = getattr(tool_call, "raw_input", None)
            if isinstance(raw_in, str):
                try:
                    import json

                    raw_in = json.loads(raw_in)
                except Exception:
                    pass

            if isinstance(raw_in, dict):
                path_str = raw_in.get("path", "")

            if not path_str and tool_call.content:
                for item in tool_call.content:
                    if hasattr(item, "path"):
                        path_str = item.path
                        break
                    elif isinstance(item, dict) and "path" in item:
                        path_str = item["path"]
                        break

            if not path_str and getattr(tool_call, "title", None):
                import re

                match = re.search(r"([\w\-_\.\/]+\.[a-zA-Z0-9]+)", tool_call.title)
                if match:
                    path_str = match.group(1)

            details = f"File {tool_kind}: {path_str}"

            try:
                _resolve_in_workspace(path_str, self.workspace_root)
            except (OSError, ValueError):
                logger.warning(f"Blocking directory traversal attempt in permission request: {path_str}")
                return RequestPermissionResponse(
                    outcome=DeniedOutcome(outcome="cancelled", message="Directory traversal blocked")
                )

            is_allowed = policy_mgr.validate_file_path(path_str, self.workspace_root)

        else:
            logger.warning("Blocking unknown ACP tool kind: %r", tool_kind)
            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled", message=f"Unsupported tool kind: {tool_kind!s}")
            )

        if is_allowed:
            for opt in options:
                if opt.kind in ("allow_once", "allow_always"):
                    return RequestPermissionResponse(
                        outcome=AllowedOutcome(option_id=opt.option_id, outcome="selected")
                    )
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        # An external ACP turn cannot be suspended safely while releasing its
        # durable worker lease. Anything outside the exact pre-approved policy
        # therefore fails closed; there is no process-local approval Future.
        logger.warning("Blocking ACP request outside the exact policy: %s", details)
        return RequestPermissionResponse(
            outcome=DeniedOutcome(
                outcome="cancelled",
                message="Operation is outside the pre-approved OpenCode policy",
            )
        )

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        try:
            argv = _parse_command_argv(command, args)
            if not PermissionPolicyManager().validate_argv(argv):
                raise ValueError("Command is not allowed by the OpenCode terminal policy")
            exec_cwd = _resolve_in_workspace(cwd or ".", self.workspace_root)
            if not exec_cwd.is_dir():
                raise ValueError("Terminal cwd must be an existing directory")
            process_env = _safe_terminal_environment(env)
        except ValueError as exc:
            raise RequestError.invalid_params(str(exc))

        if output_byte_limit is None:
            effective_output_limit = _DEFAULT_TERMINAL_OUTPUT_BYTE_LIMIT
        elif not isinstance(output_byte_limit, int) or isinstance(output_byte_limit, bool) or output_byte_limit <= 0:
            raise RequestError.invalid_params("output_byte_limit must be positive")
        else:
            effective_output_limit = min(output_byte_limit, _MAX_TERMINAL_OUTPUT_BYTE_LIMIT)

        process_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(exec_cwd),
                env=process_env,
                **process_kwargs,
            )
            terminal_id = str(uuid.uuid4())
            self.terminals[terminal_id] = proc
            self.terminal_buffers[terminal_id] = b""
            self.terminal_output_limits[terminal_id] = effective_output_limit
            self.terminal_output_bytes[terminal_id] = 0
            self.terminal_output_truncated[terminal_id] = False
            self.terminal_process_groups.add(terminal_id)

            task = asyncio.create_task(self._read_terminal_output(terminal_id, proc))
            self.terminal_tasks[terminal_id] = task

            return CreateTerminalResponse(terminal_id=terminal_id)
        except Exception as e:
            raise RequestError.internal_error(str(e))

    async def _read_terminal_output(self, terminal_id: str, proc: asyncio.subprocess.Process):
        async def read_stream(stream):
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                limit = self.terminal_output_limits[terminal_id]
                stored = self.terminal_output_bytes[terminal_id]
                remaining = max(0, limit - stored)
                if remaining:
                    accepted = chunk[:remaining]
                    self.terminal_buffers[terminal_id] += accepted
                    self.terminal_output_bytes[terminal_id] += len(accepted)
                if len(chunk) > remaining:
                    self.terminal_output_truncated[terminal_id] = True

        await asyncio.gather(read_stream(proc.stdout), read_stream(proc.stderr))

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> TerminalOutputResponse:
        if terminal_id not in self.terminals:
            raise RequestError.invalid_params(f"Terminal {terminal_id} not found")

        proc = self.terminals[terminal_id]
        buffer = self.terminal_buffers.get(terminal_id, b"")
        self.terminal_buffers[terminal_id] = b""

        decoded_output = buffer.decode("utf-8", errors="ignore")

        exit_status = None
        if proc.returncode is not None:
            exit_status = TerminalExitStatus(exit_code=proc.returncode)

        return TerminalOutputResponse(
            output=decoded_output,
            truncated=self.terminal_output_truncated.get(terminal_id, False),
            exit_status=exit_status,
        )

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        if terminal_id not in self.terminals:
            raise RequestError.invalid_params(f"Terminal {terminal_id} not found")

        proc = self.terminals[terminal_id]
        await proc.wait()

        task = self.terminal_tasks.get(terminal_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        return WaitForTerminalExitResponse(exit_code=proc.returncode, signal=None)

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> KillTerminalResponse | None:
        if terminal_id not in self.terminals:
            return None
        proc = self.terminals[terminal_id]
        await _terminate_process(proc, process_group=terminal_id in self.terminal_process_groups)
        return KillTerminalResponse()

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        if terminal_id in self.terminals:
            proc = self.terminals[terminal_id]
            await _terminate_process(proc, process_group=terminal_id in self.terminal_process_groups)
            self.terminals.pop(terminal_id)
        self.terminal_buffers.pop(terminal_id, None)
        self.terminal_output_limits.pop(terminal_id, None)
        self.terminal_output_bytes.pop(terminal_id, None)
        self.terminal_output_truncated.pop(terminal_id, None)
        self.terminal_process_groups.discard(terminal_id)
        task = self.terminal_tasks.pop(terminal_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return ReleaseTerminalResponse()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        content_text = ""
        if hasattr(update, "session_update"):
            u_type = update.session_update
            if u_type == "agent_thought_chunk":
                if hasattr(update.content, "text"):
                    logger.debug(f"OpenCode Thought: {update.content.text}")
            elif u_type == "agent_message_chunk":
                if hasattr(update.content, "text"):
                    content_text = update.content.text
            elif u_type == "tool_call":
                content_text = f"\n🛠️ Calling tool: {update.title or update.kind}..."
            elif u_type == "tool_call_update":
                if update.status == "completed":
                    content_text = f"\n✅ Tool call completed: {update.title or update.kind}"
                elif update.status == "failed":
                    content_text = f"\n❌ Tool call failed: {update.title or update.kind}"

        if content_text:
            self.output_buffer.append(content_text)
            accumulated_text = "".join(self.output_buffer)

            if self.on_update_callback:
                if asyncio.iscoroutinefunction(self.on_update_callback):
                    await self.on_update_callback(accumulated_text)
                else:
                    self.on_update_callback(accumulated_text)


async def run_opencode_agent_acp(
    app_id: str,
    instruction: str,
    language: str = "zh",
    on_update: Callable[[str], None] = None,
    *,
    promote: bool = True,
) -> str | OpenCodeStagedResult:
    """
    Spawns OpenCode agent in ACP mode, runs its loop, and streams the output/logs back via on_update callback.
    """
    opencode_command = os.getenv("OPENCODE_COMMAND", "opencode")
    if os.name == "nt" and opencode_command == "opencode":
        resolved = shutil.which("opencode")
        if resolved:
            opencode_command = resolved
    try:
        opencode_argv = _parse_command_argv(opencode_command)
    except ValueError as exc:
        raise OpenCodeACPInputError(f"Invalid OPENCODE_COMMAND: {exc!s}") from exc

    workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
    apps_dir = os.getenv("APPS_DIR", os.path.join(workspace_dir, "apps"))
    live_dir, staging_dir = _prepare_staging_app(apps_dir, app_id)
    client = FastAPIACPClient(workspace_root=staging_dir, on_update_callback=on_update)
    session_id: str | None = None
    proc: asyncio.subprocess.Process | None = None
    retain_staging = False

    try:
        logger.info("Spawning OpenCode ACP agent for App %s inside staging directory", app_id)
        try:
            async with spawn_agent_process(
                client,
                opencode_argv[0],
                *opencode_argv[1:],
                "acp",
                cwd=staging_dir,
                env=_opencode_runtime_env(),
                transport_kwargs={"shutdown_timeout": _PROCESS_TERMINATION_GRACE_SECONDS},
            ) as (conn, spawned_proc):
                proc = spawned_proc
                await conn.initialize(
                    protocol_version=1,
                    client_capabilities=ClientCapabilities(
                        fs=FileSystemCapabilities(read_text_file=True, write_text_file=True), terminal=True
                    ),
                )

                session_resp = await conn.new_session(cwd=str(staging_dir))
                session_id = session_resp.session_id

                from backend.agent.prompts.manager import PromptManager

                prompt_text = PromptManager().get_prompt(
                    "opencode_system.md",
                    app_id=app_id,
                    target_dir=str(staging_dir),
                    instruction=instruction,
                    language=language,
                )

                try:
                    opencode_timeout = float(os.getenv("OPENCODE_TIMEOUT", "600.0"))
                except ValueError as exc:
                    raise OpenCodeACPInputError("OPENCODE_TIMEOUT must be a number") from exc
                if opencode_timeout <= 0:
                    raise OpenCodeACPInputError("OPENCODE_TIMEOUT must be positive")

                try:
                    prompt_response = await asyncio.wait_for(
                        conn.prompt(session_id=session_id, prompt=[text_block(prompt_text)]),
                        timeout=opencode_timeout,
                    )
                except TimeoutError as exc:
                    logger.warning("OpenCode ACP agent timed out after %s seconds for App %s", opencode_timeout, app_id)
                    await _terminate_process(spawned_proc, process_group=True)
                    raise OpenCodeACPTimeoutError(
                        f"OpenCode ACP agent timed out after {opencode_timeout:g} seconds"
                    ) from exc
                except asyncio.CancelledError:
                    await _terminate_process(spawned_proc, process_group=True)
                    raise

                if prompt_response.stop_reason != "end_turn":
                    raise OpenCodeACPProtocolError(
                        f"OpenCode ACP agent stopped before completion: {prompt_response.stop_reason}"
                    )
        except (OpenCodeACPError, asyncio.CancelledError):
            raise
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise OpenCodeACPStartupError(f"Unable to start OpenCode ACP agent: {exc!s}") from exc
        except Exception as exc:
            raise OpenCodeACPProtocolError(f"OpenCode ACP protocol failed: {exc!s}") from exc

        output = "".join(client.output_buffer)
        staged_result = OpenCodeStagedResult(
            output=output,
            app_id=app_id,
            staging_dir=staging_dir,
            live_dir=live_dir,
        )
        validate_opencode_staging(staged_result)
        if not promote:
            retain_staging = True
            return staged_result
        promote_opencode_staging(staged_result)
        return output
    finally:
        if proc is not None and proc.returncode is None:
            try:
                await _terminate_process(proc, process_group=True)
            except Exception:
                logger.warning("Unable to terminate OpenCode ACP process during cleanup", exc_info=True)
        if not retain_staging and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
