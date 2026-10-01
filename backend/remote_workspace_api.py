"""Local-only pairing/approval controls; these routes are never tunnel targets."""

from __future__ import annotations

import os
from collections.abc import Callable
from ipaddress import ip_address, ip_network
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool

from backend.client_widget_runtime import ClientWidgetRuntimeTicketError, client_runtime_origin
from backend.remote_workspace import RemoteWorkspaceConnector, RemoteWorkspaceDenied, RemoteWorkspaceGatewayError


class RemoteWorkspacePair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gateway_url: str = Field(max_length=2048)
    portal_url: str = Field(max_length=2048)
    name: str = Field(min_length=1, max_length=80)
    scopes: list[Literal["workspace.control", "workspace.manage"]] = Field(min_length=1, max_length=2)
    expires_in: int = Field(default=86400, ge=300, le=30 * 86400)
    until_revoked: StrictBool = False
    enrollment_token: SecretStr = Field(min_length=20, max_length=128, exclude=True, repr=False)


class RemoteWorkspaceApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=256)
    grant_id: str = Field(min_length=1, max_length=256)


class _SafeRemoteWorkspaceRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, detail="Check the enrollment token and connection settings") from None

        return safe_handler


def _gateway_failure(exc: RemoteWorkspaceGatewayError) -> HTTPException:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
    return HTTPException(status_code=exc.status_code, detail=str(exc), headers=headers)


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
    router = APIRouter(prefix="/api/remote-workspace", route_class=_SafeRemoteWorkspaceRoute)

    @router.get("/status")
    async def status(request: Request):
        _require_local(request)
        return connector().status()

    @router.post("/pair")
    async def pair(request: Request, data: RemoteWorkspacePair):
        _require_local(request)
        try:
            return await connector().pair(
                {**data.model_dump(), "enrollment_token": data.enrollment_token.get_secret_value()}
            )
        except RemoteWorkspaceGatewayError as exc:
            raise _gateway_failure(exc) from None
        except (RemoteWorkspaceDenied, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Check the enrollment token and connection settings") from None
        except (httpx.HTTPError, OSError) as exc:
            raise HTTPException(status_code=502, detail="Workspace gateway is unavailable") from exc

    @router.post("/approve")
    async def approve(request: Request, data: RemoteWorkspaceApprove):
        _require_local(request)
        try:
            return await connector().approve(data.account_id, data.grant_id)
        except RemoteWorkspaceGatewayError as exc:
            raise _gateway_failure(exc) from None
        except (RemoteWorkspaceDenied, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="The claimed account or grant changed. Review the local connection before approving",
            ) from None
        except (httpx.HTTPError, OSError) as exc:
            raise HTTPException(status_code=502, detail="Workspace gateway is unavailable") from exc

    @router.post("/revoke")
    async def revoke(request: Request):
        _require_local(request)
        return await connector().revoke()

    return router
