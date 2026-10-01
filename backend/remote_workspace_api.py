"""Local-only pairing/approval controls; these routes are never tunnel targets."""

from __future__ import annotations

import os
from collections.abc import Callable
from ipaddress import ip_address, ip_network
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.client_widget_runtime import ClientWidgetRuntimeTicketError, client_runtime_origin
from backend.remote_workspace import RemoteWorkspaceConnector, RemoteWorkspaceDenied


class RemoteWorkspacePair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gateway_url: str = Field(max_length=2048)
    portal_url: str = Field(max_length=2048)
    name: str = Field(min_length=1, max_length=80)
    scopes: list[Literal["workspace.control", "workspace.manage"]] = Field(min_length=1, max_length=2)
    expires_in: int = Field(default=86400, ge=300, le=30 * 86400)


class RemoteWorkspaceApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=256)
    grant_id: str = Field(min_length=1, max_length=256)


def _require_local(request: Request) -> None:
    try:
        address = ip_address(request.client.host if request.client else "")
        allowed = address.is_loopback or (
            address.version == 6 and address.ipv4_mapped and address.ipv4_mapped.is_loopback
        )
        if not allowed:
            for value in os.getenv("AMBIENT_TRUSTED_HOST_PEERS", "").split(","):
                if value.strip() and address in ip_network(value.strip(), strict=False):
                    allowed = True
        if not allowed:
            raise ValueError("Peer denied")
        if request.headers.get("origin"):
            client_runtime_origin(request.headers)
    except (ValueError, ClientWidgetRuntimeTicketError) as exc:
        raise HTTPException(status_code=403, detail="Remote workspace pairing requires the trusted local host") from exc


def create_remote_workspace_router(connector: Callable[[], RemoteWorkspaceConnector]) -> APIRouter:
    router = APIRouter(prefix="/api/remote-workspace")

    @router.get("/status")
    async def status(request: Request):
        _require_local(request)
        return connector().status()

    @router.post("/pair")
    async def pair(request: Request, data: RemoteWorkspacePair):
        _require_local(request)
        try:
            return await connector().pair(data.model_dump())
        except (RemoteWorkspaceDenied, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise HTTPException(status_code=502, detail="Workspace gateway is unavailable") from exc

    @router.post("/approve")
    async def approve(request: Request, data: RemoteWorkspaceApprove):
        _require_local(request)
        try:
            return await connector().approve(data.account_id, data.grant_id)
        except (RemoteWorkspaceDenied, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise HTTPException(status_code=502, detail="Workspace gateway is unavailable") from exc

    @router.post("/revoke")
    async def revoke(request: Request):
        _require_local(request)
        return await connector().revoke()

    return router
