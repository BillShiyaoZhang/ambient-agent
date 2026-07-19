"""Authenticated localhost bridge from Docker Ambient Agent to host Codex."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hmac
import json
import os
import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app_manifest import ManifestValidationError, validate_app_id
from backend.codex_service import (
    CodexAgentStartupError,
    CodexAgentTimeoutError,
    _codex_environment,
    _codex_exec_argv,
    _run_codex_exec,
)
from backend.opencode_service import _is_link_or_junction, _parse_command_argv

load_dotenv()

app = FastAPI(title="Ambient Codex Host Bridge", docs_url=None, redoc_url=None, openapi_url=None)


class HostCodexRunRequest(BaseModel):
    app_id: str
    staging_dir: str = Field(min_length=1, max_length=180)
    prompt: str = Field(min_length=1, max_length=1024 * 1024)
    timeout_seconds: float = Field(gt=0, le=3600)


def _bridge_token() -> str:
    return os.getenv("CODEX_HOST_BRIDGE_TOKEN", "").strip()


def _require_bridge_token(authorization: str | None = Header(default=None)) -> None:
    expected = _bridge_token()
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="Host bridge token is not configured")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid bridge token")


def _host_apps_root() -> Path:
    workspace = Path(os.getenv("AMBIENT_HOST_WORKSPACE_DIR", "workspace"))
    try:
        root = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"Host workspace is unavailable: {exc!s}") from exc
    apps_root = root / "apps"
    if not apps_root.is_dir() or _is_link_or_junction(apps_root):
        raise HTTPException(status_code=503, detail="Host Apps directory is unavailable")
    return apps_root


def _resolve_staging(request: HostCodexRunRequest) -> Path:
    try:
        validate_app_id(request.app_id)
    except ManifestValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid App ID: {exc!s}") from exc
    expected = re.compile(rf"^\.{re.escape(request.app_id)}\.staging-[0-9a-f]{{32}}$")
    if not expected.fullmatch(request.staging_dir):
        raise HTTPException(status_code=422, detail="Invalid staging directory handle")
    apps_root = _host_apps_root()
    candidate = apps_root / request.staging_dir
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=f"Staging directory is unavailable: {exc!s}") from exc
    if resolved.parent != apps_root or not resolved.is_dir() or _is_link_or_junction(candidate):
        raise HTTPException(status_code=422, detail="Staging directory escaped the host Apps root")
    return resolved


def _host_codex_command() -> list[str]:
    command = os.getenv("CODEX_HOST_COMMAND", "codex")
    try:
        argv = _parse_command_argv(command)
    except ValueError as exc:
        raise CodexAgentStartupError(f"Invalid host Codex command: {exc!s}") from exc
    executable = argv[0]
    if not Path(executable).is_absolute() and shutil.which(executable) is None:
        raise CodexAgentStartupError("Host Codex CLI was not found")
    if Path(executable).is_absolute() and not Path(executable).is_file():
        raise CodexAgentStartupError("Host Codex CLI was not found")
    return argv


async def _run_status_command(argv: list[str]) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_codex_environment(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        return proc.returncode or 0, stdout[:16_384].decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, OSError, TimeoutError) as exc:
        return 1, str(exc)


@app.get("/health", dependencies=[Depends(_require_bridge_token)])
async def health() -> dict[str, Any]:
    try:
        command = _host_codex_command()
    except CodexAgentStartupError as exc:
        return {"ok": False, "authenticated": False, "version": "", "login_status": str(exc)}
    version_code, version = await _run_status_command([*command, "--version"])
    login_code, login_status = await _run_status_command([*command, "login", "status"])
    return {
        "ok": version_code == 0,
        "authenticated": login_code == 0,
        "version": version,
        "login_status": login_status,
    }


def _error_event(exc: Exception) -> dict[str, str]:
    if isinstance(exc, CodexAgentTimeoutError):
        code = "timeout"
    elif isinstance(exc, CodexAgentStartupError):
        code = "startup"
    else:
        code = "protocol"
    return {"type": "error", "code": code, "message": str(exc)}


@app.post("/v1/run", dependencies=[Depends(_require_bridge_token)])
async def run_codex(request: HostCodexRunRequest) -> StreamingResponse:
    staging_dir = _resolve_staging(request)
    command = _host_codex_command()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)

    async def emit(payload: Any) -> None:
        await queue.put({"type": "progress", "payload": payload})

    async def execute() -> None:
        try:
            output = await _run_codex_exec(
                _codex_exec_argv(command, staging_dir),
                cwd=staging_dir,
                prompt=request.prompt,
                timeout=request.timeout_seconds,
                on_update=emit,
            )
            await queue.put({"type": "result", "output": output})
        except Exception as exc:
            await queue.put(_error_event(exc))
        finally:
            await queue.put({"type": "done"})

    task = asyncio.create_task(execute())

    async def stream() -> AsyncIterator[bytes]:
        try:
            while True:
                event = await queue.get()
                if event.get("type") == "done":
                    break
                yield (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Ambient Agent Codex host bridge")
    parser.add_argument("--host", default=os.getenv("CODEX_HOST_BRIDGE_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("CODEX_HOST_BRIDGE_PORT", "8765")))
    args = parser.parse_args()
    if len(_bridge_token()) < 32:
        raise SystemExit("CODEX_HOST_BRIDGE_TOKEN must contain at least 32 characters")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
