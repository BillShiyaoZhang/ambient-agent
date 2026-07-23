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
from pathlib import Path
from typing import Any

from backend.coding_agent_runtime import CodingAgentRuntime
from backend.opencode_service import (
    CodingAgentDraftError,
    CodingAgentStagedResult,
    OpenCodeArtifactError,
    _prepare_staging_app,
    _terminate_process,
    promote_coding_agent_staging,
    resume_coding_agent_staging,
    validate_coding_agent_staging,
)

_MAX_CODEX_EVENT_BYTES = 4 * 1024 * 1024
_MAX_CODEX_STDERR_BYTES = 64 * 1024
_MAX_CODEX_VALIDATION_REPAIRS = 3
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


def _codex_exec_argv(command_argv: list[str], staging_dir: Path, native_model: str | None = None) -> list[str]:
    argv = [
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
    ]
    if native_model:
        argv.extend(["--model", native_model])
    argv.append("-")
    return argv


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
    environment: dict[str, str] | None = None,
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
            env=environment or _codex_environment(),
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


def _approved_runtime_contract_excerpt(instruction: str) -> str:
    marker = "[APPROVED RUNTIME CONTRACT — COPY EXACTLY INTO MANIFEST V2]"
    start = instruction.find(marker)
    if start < 0:
        return ""
    end = instruction.find("\n\n[SYSTEM CAPABILITIES]", start)
    excerpt = instruction[start : end if end >= 0 else None]
    return excerpt[:24_000]


async def run_codex_agent(
    app_id: str,
    instruction: str,
    language: str = "zh",
    on_update: Callable[[Any], Any] | None = None,
    *,
    promote: bool = True,
    runtime: CodingAgentRuntime | None = None,
    native_model: str | None = None,
    staged_result: CodingAgentStagedResult | None = None,
) -> str | CodingAgentStagedResult:
    """Generate a Widget with the managed container Codex runtime."""
    timeout = _codex_timeout()
    workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
    runtime = runtime or CodingAgentRuntime(workspace_dir)
    command = runtime.command("codex")
    if command is None:
        raise CodexAgentStartupError("Codex is not installed")
    apps_dir = os.getenv("APPS_DIR", os.path.join(workspace_dir, "apps"))
    owns_staging = staged_result is None
    if staged_result is None:
        live_dir, staging_dir = _prepare_staging_app(apps_dir, app_id)
    else:
        if staged_result.app_id != app_id:
            raise CodexAgentInputError("Retained staging App ID does not match the requested App")
        live_dir, staging_dir = resume_coding_agent_staging(staged_result)
    retain_staging = False
    output = ""
    try:
        prompt = _codex_prompt(app_id, instruction, language)
        approved_contract = _approved_runtime_contract_excerpt(instruction)
        result: CodingAgentStagedResult | None = None
        for attempt in range(_MAX_CODEX_VALIDATION_REPAIRS + 1):
            output = await _run_codex_exec(
                _codex_exec_argv(command, staging_dir, native_model),
                cwd=staging_dir,
                prompt=prompt,
                timeout=timeout,
                on_update=on_update,
                environment=runtime.process_environment("codex"),
            )
            result = CodingAgentStagedResult(
                output=output,
                app_id=app_id,
                staging_dir=staging_dir,
                live_dir=live_dir,
            )
            try:
                validate_coding_agent_staging(result)
                break
            except OpenCodeArtifactError as exc:
                if attempt == _MAX_CODEX_VALIDATION_REPAIRS:
                    raise
                diagnostic = str(exc)[:12_000]
                await _emit(
                    on_update,
                    "\n🔧 Codex generated code that failed mandatory validation; asking it to repair the staging App in place.",
                )
                contract_context = f"\n\n{approved_contract}" if approved_contract else ""
                prompt = (
                    "The staged App failed mandatory validation. Fix the existing controller.js and/or manifest.json "
                    "in place, do not create any other files, and preserve the requested functionality while obeying "
                    "the Widget runtime and network boundary. Inspect the whole files for the same class of "
                    "mistake, not only the reported line. HTM component closing syntax is `<//>`; never emit "
                    "React-like `</${Component}>` or malformed `</${Component>`. Re-run your own inspection "
                    "before finishing. If capability use and Manifest grants disagree, make both match the approved "
                    "Runtime Contract exactly; never add unapproved entities, operations, sources, paths, or actions.\n\n"
                    f"[VALIDATION ERROR]\n{diagnostic}{contract_context}"
                )
        if result is None:  # pragma: no cover - the bounded loop always executes
            raise CodexAgentProtocolError("Codex did not produce a staging result")
        if not promote:
            retain_staging = True
            return result
        promote_coding_agent_staging(result)
        return output
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if not promote:
            retain_staging = True
            if isinstance(exc, CodingAgentDraftError):
                raise
            raise CodingAgentDraftError(
                str(exc),
                staged_result=CodingAgentStagedResult(
                    output=output[-64_000:],
                    app_id=app_id,
                    staging_dir=staging_dir,
                    live_dir=live_dir,
                ),
                error_code=str(getattr(exc, "code", type(exc).__name__)),
            ) from exc
        raise
    finally:
        if owns_staging and not retain_staging and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
