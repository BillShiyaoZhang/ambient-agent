"""Codex coding-agent adapter using the official non-interactive CLI surface."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.opencode_service import (
    CodingAgentStagedResult,
    _prepare_staging_app,
    _terminate_process,
    promote_coding_agent_staging,
    validate_coding_agent_staging,
)

_MAX_CODEX_EVENT_BYTES = 4 * 1024 * 1024
_MAX_CODEX_STDERR_BYTES = 64 * 1024
_MAX_BRIDGE_LINE_BYTES = 1024 * 1024
_CODEX_ENV_ALLOWLIST = {
    "ALL_PROXY",
    "APPDATA",
    "COMSPEC",
    "CODEX_ACCESS_TOKEN",
    "CODEX_API_KEY",
    "CODEX_CA_CERTIFICATE",
    "CODEX_HOME",
    "CODEX_SQLITE_HOME",
    "HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "LOGNAME",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "USERPROFILE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


class CodexAgentError(RuntimeError):
    pass


class CodexAgentInputError(CodexAgentError, ValueError):
    pass


class CodexAgentStartupError(CodexAgentError):
    pass


class CodexAgentTimeoutError(CodexAgentError, TimeoutError):
    pass


class CodexAgentProtocolError(CodexAgentError):
    pass


def _codex_environment() -> dict[str, str]:
    """Pass Codex-native auth and process essentials, never Ambient LLM secrets."""
    return {name: value for name in _CODEX_ENV_ALLOWLIST if (value := os.environ.get(name)) is not None}


def _codex_exec_argv(command_argv: list[str], staging_dir: Path) -> list[str]:
    return [
        *command_argv,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-C",
        str(staging_dir),
        "-",
    ]


def _host_bridge_config() -> tuple[str, str]:
    raw_url = os.getenv("CODEX_HOST_BRIDGE_URL", "http://host.docker.internal:8765").strip()
    token = os.getenv("CODEX_HOST_BRIDGE_TOKEN", "").strip()
    if not token:
        raise CodexAgentStartupError("CODEX_HOST_BRIDGE_TOKEN is required for host Codex")
    parsed = urlparse(raw_url)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise CodexAgentInputError("CODEX_HOST_BRIDGE_URL must be an unauthenticated http URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise CodexAgentInputError("CODEX_HOST_BRIDGE_URL must not contain a path, query, or fragment")
    hostname = parsed.hostname.lower()
    allowed = hostname in {"host.docker.internal", "localhost", "127.0.0.1", "::1"}
    if not allowed:
        try:
            allowed = ip_address(hostname).is_private
        except ValueError:
            allowed = False
    if not allowed:
        raise CodexAgentInputError("CODEX_HOST_BRIDGE_URL must target localhost or a private host address")
    return raw_url.rstrip("/"), token


async def _emit(callback: Callable[[Any], Any] | None, payload: Any) -> None:
    if callback is None:
        return
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


def _event_update(event: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (final-message text, progress text) for a Codex JSONL event."""
    event_type = event.get("type")
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    item_type = item.get("type")
    if event_type == "item.completed" and item_type == "agent_message":
        text = item.get("text")
        return (text if isinstance(text, str) else None, text if isinstance(text, str) else None)
    if event_type == "item.started" and item_type == "command_execution":
        command = item.get("command")
        return None, f"\n🛠️ Codex: {command}" if isinstance(command, str) else "\n🛠️ Codex is running a command..."
    if event_type == "item.completed" and item_type == "file_change":
        return None, "\n✅ Codex updated the staging App."
    if event_type == "turn.failed":
        error = event.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        return None, f"Codex turn failed: {error or 'unknown error'}"
    if event_type == "error":
        message = event.get("message")
        return None, f"Codex error: {message or 'unknown error'}"
    return None, None


async def _run_codex_exec(
    argv: list[str],
    *,
    cwd: Path,
    prompt: str,
    timeout: float,
    on_update: Callable[[Any], Any] | None,
) -> str:
    process_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        process_kwargs["start_new_session"] = True
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_codex_environment(),
            **process_kwargs,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise CodexAgentStartupError(f"Unable to start Codex: {exc!s}") from exc
    if proc.stdin is None or proc.stdout is None or proc.stderr is None:
        await _terminate_process(proc, process_group=True)
        raise CodexAgentStartupError("Codex process did not expose stdio pipes")

    messages: list[str] = []
    protocol_errors: list[str] = []
    stdout_bytes = 0

    async def consume_stdout() -> None:
        nonlocal stdout_bytes
        async for raw_line in proc.stdout:
            stdout_bytes += len(raw_line)
            if stdout_bytes > _MAX_CODEX_EVENT_BYTES:
                raise CodexAgentProtocolError("Codex event stream exceeded the size limit")
            try:
                event = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CodexAgentProtocolError("Codex emitted malformed JSONL output") from exc
            if not isinstance(event, dict):
                raise CodexAgentProtocolError("Codex emitted a non-object event")
            message, update = _event_update(event)
            if message:
                messages.append(message)
            if update:
                if event.get("type") in {"turn.failed", "error"}:
                    protocol_errors.append(update)
                await _emit(on_update, update)

    async def consume_stderr() -> str:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            remaining = _MAX_CODEX_STDERR_BYTES - size
            if remaining > 0:
                chunks.append(chunk[:remaining])
                size += min(len(chunk), remaining)
        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    stdout_task = asyncio.create_task(consume_stdout())
    stderr_task = asyncio.create_task(consume_stderr())
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        with contextlib.suppress(Exception):
            await proc.stdin.wait_closed()
        try:
            return_code, _ = await asyncio.wait_for(
                asyncio.gather(proc.wait(), stdout_task),
                timeout=timeout,
            )
        except TimeoutError as exc:
            await _terminate_process(proc, process_group=True)
            raise CodexAgentTimeoutError(f"Codex timed out after {timeout:g} seconds") from exc
        stderr = await stderr_task
        if return_code != 0:
            detail = protocol_errors[-1] if protocol_errors else stderr or f"exit code {return_code}"
            raise CodexAgentProtocolError(f"Codex execution failed: {detail}")
        if protocol_errors:
            raise CodexAgentProtocolError(protocol_errors[-1])
        return "\n".join(messages).strip()
    except asyncio.CancelledError:
        await _terminate_process(proc, process_group=True)
        raise
    finally:
        for task in (stdout_task, stderr_task):
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, CodexAgentProtocolError):
                await task
        if proc.returncode is None:
            await _terminate_process(proc, process_group=True)


def _codex_timeout() -> float:
    try:
        timeout = float(os.getenv("CODEX_TIMEOUT", "600.0"))
    except ValueError as exc:
        raise CodexAgentInputError("CODEX_TIMEOUT must be a number") from exc
    if timeout <= 0:
        raise CodexAgentInputError("CODEX_TIMEOUT must be positive")
    return timeout


def _codex_prompt(app_id: str, instruction: str, language: str) -> str:
    from backend.agent.prompts.manager import PromptManager

    return PromptManager().get_prompt(
        "opencode_system.md",
        app_id=app_id,
        target_dir=".",
        instruction=instruction,
        language=language,
    )


async def codex_host_bridge_status() -> dict[str, Any]:
    try:
        base_url, token = _host_bridge_config()
        timeout = httpx.Timeout(3.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.get(
                f"{base_url}/health",
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("invalid bridge health response")
        bridge_ready = payload.get("ok") is True
        return {
            "available": bridge_ready,
            "authenticated": bool(payload.get("authenticated")),
            "version": str(payload.get("version") or ""),
            "status_detail": str(payload.get("login_status") or ""),
        }
    except (CodexAgentError, httpx.HTTPError, TypeError, ValueError) as exc:
        return {
            "available": False,
            "authenticated": False,
            "version": "",
            "status_detail": str(exc),
        }


async def _run_codex_via_host_bridge(
    *,
    app_id: str,
    staging_dir: Path,
    prompt: str,
    timeout: float,
    on_update: Callable[[Any], Any] | None,
) -> str:
    base_url, token = _host_bridge_config()
    request_timeout = httpx.Timeout(timeout + 15.0, connect=3.0)
    total_bytes = 0
    output: str | None = None
    try:
        async with httpx.AsyncClient(timeout=request_timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                f"{base_url}/v1/run",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "app_id": app_id,
                    "staging_dir": staging_dir.name,
                    "prompt": prompt,
                    "timeout_seconds": timeout,
                },
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread())[:_MAX_BRIDGE_LINE_BYTES].decode("utf-8", errors="replace")
                    raise CodexAgentStartupError(
                        f"Host Codex bridge rejected the request ({response.status_code}): {body}"
                    )
                async for line in response.aiter_lines():
                    total_bytes += len(line.encode("utf-8"))
                    if total_bytes > _MAX_CODEX_EVENT_BYTES:
                        raise CodexAgentProtocolError("Host Codex bridge response exceeded the size limit")
                    if not line:
                        continue
                    if len(line.encode("utf-8")) > _MAX_BRIDGE_LINE_BYTES:
                        raise CodexAgentProtocolError("Host Codex bridge emitted an oversized event")
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise CodexAgentProtocolError("Host Codex bridge emitted malformed JSONL") from exc
                    if not isinstance(event, dict):
                        raise CodexAgentProtocolError("Host Codex bridge emitted a non-object event")
                    event_type = event.get("type")
                    if event_type == "progress":
                        await _emit(on_update, event.get("payload"))
                    elif event_type == "result" and isinstance(event.get("output"), str):
                        output = event["output"]
                    elif event_type == "error":
                        code = str(event.get("code") or "protocol_error")
                        message = str(event.get("message") or "Host Codex execution failed")
                        if code == "timeout":
                            raise CodexAgentTimeoutError(message)
                        if code == "startup":
                            raise CodexAgentStartupError(message)
                        raise CodexAgentProtocolError(message)
    except CodexAgentError:
        raise
    except httpx.TimeoutException as exc:
        raise CodexAgentTimeoutError(f"Host Codex bridge timed out after {timeout:g} seconds") from exc
    except httpx.HTTPError as exc:
        raise CodexAgentStartupError(f"Unable to reach Host Codex bridge: {exc!s}") from exc
    if output is None:
        raise CodexAgentProtocolError("Host Codex bridge completed without a result")
    return output


async def run_codex_agent(
    app_id: str,
    instruction: str,
    language: str = "zh",
    on_update: Callable[[Any], Any] | None = None,
    *,
    promote: bool = True,
) -> str | CodingAgentStagedResult:
    """Generate a Widget in staging through the authenticated host Codex bridge."""
    timeout = _codex_timeout()
    workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
    apps_dir = os.getenv("APPS_DIR", os.path.join(workspace_dir, "apps"))
    live_dir, staging_dir = _prepare_staging_app(apps_dir, app_id)
    retain_staging = False
    try:
        prompt = _codex_prompt(app_id, instruction, language)
        output = await _run_codex_via_host_bridge(
            app_id=app_id,
            staging_dir=staging_dir,
            prompt=prompt,
            timeout=timeout,
            on_update=on_update,
        )
        result = CodingAgentStagedResult(
            output=output,
            app_id=app_id,
            staging_dir=staging_dir,
            live_dir=live_dir,
        )
        validate_coding_agent_staging(result)
        if not promote:
            retain_staging = True
            return result
        promote_coding_agent_staging(result)
        return output
    finally:
        if not retain_staging and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
