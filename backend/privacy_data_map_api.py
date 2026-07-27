from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Literal

from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.privacy_data_map import (
    AppDeclarationSourceError,
    AuditProjectionSourceError,
    GraphSchemaSourceError,
    PrivacyDataMapIdCollisionError,
    PrivacyDataMapProjectionError,
    PrivacyDataMapResourceLimitError,
    PrivacyDataMapResponse,
)

PRIVACY_DATA_MAP_PATH = "/api/privacy-data-map"
_PUBLIC_ERROR_MESSAGE = "Privacy Map is temporarily unavailable."

PrivacyMapErrorCode = Literal[
    "privacy_map_audit_source_unreadable",
    "privacy_map_app_source_unreadable",
    "privacy_map_invalid_request",
    "privacy_map_schema_source_unreadable",
    "privacy_map_id_collision",
    "privacy_map_projection_failed",
    "privacy_map_resource_limit_exceeded",
]
PrivacyDataMapBuilder = Callable[[], PrivacyDataMapResponse]


@dataclass(frozen=True, slots=True)
class _SanitizedBuildFailure:
    error_type: type[Exception]


_BuildOutcome = PrivacyDataMapResponse | _SanitizedBuildFailure


class _PrivacyDataMapSingleFlight:
    """Share only an active build; completed outcomes are never cached."""

    def __init__(self, builder: PrivacyDataMapBuilder):
        self._builder = builder
        self._lock = Lock()
        self._active: asyncio.Task[_BuildOutcome] | None = None

    async def run(self) -> PrivacyDataMapResponse:
        with self._lock:
            task = self._active
            if task is None:
                task = asyncio.create_task(self._run_build())
                self._active = task

        outcome = await asyncio.shield(task)

        if isinstance(outcome, _SanitizedBuildFailure):
            raise outcome.error_type
        return outcome

    async def _run_build(self) -> _BuildOutcome:
        task = asyncio.current_task()
        try:
            return await run_in_threadpool(_invoke_builder, self._builder)
        except BaseException:
            return _SanitizedBuildFailure(PrivacyDataMapProjectionError)
        finally:
            with self._lock:
                if self._active is task:
                    self._active = None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PrivacyDataMapErrorDetail(_StrictModel):
    code: PrivacyMapErrorCode
    message: Literal["Privacy Map is temporarily unavailable."] = _PUBLIC_ERROR_MESSAGE


class PrivacyDataMapErrorResponse(_StrictModel):
    contract_version: Literal[1] = 1
    error: PrivacyDataMapErrorDetail


class _PrivacyDataMapResponseMiddleware:
    """Apply route-local response policy without changing platform access."""

    def __init__(self, app: ASGIApp):
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path")
        if path == f"{PRIVACY_DATA_MAP_PATH}/":
            await JSONResponse(
                status_code=404,
                content={"detail": "Not Found"},
            )(scope, receive, self._no_store_send(send))
            return
        if path != PRIVACY_DATA_MAP_PATH:
            await self._app(scope, receive, send)
            return

        protected_send = self._no_store_send(send)
        await self._app(scope, receive, protected_send)

    @staticmethod
    def _no_store_send(send: Send) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value) for name, value in message.get("headers", []) if name.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        return wrapped


def install_privacy_data_map(
    app: FastAPI,
    *,
    builder: PrivacyDataMapBuilder,
) -> None:
    """Install the read-only V1 endpoint with an injectable projection builder."""

    single_flight = _PrivacyDataMapSingleFlight(builder)

    async def get_privacy_data_map(request: Request):
        if request.scope.get("query_string"):
            return _error_response(
                "privacy_map_invalid_request",
                status_code=400,
            )

        try:
            response = await single_flight.run()
            return JSONResponse(
                status_code=200,
                content=response.model_dump(mode="json", by_alias=True),
            )
        except AuditProjectionSourceError:
            return _error_response(
                "privacy_map_audit_source_unreadable",
                status_code=500,
            )
        except AppDeclarationSourceError:
            return _error_response(
                "privacy_map_app_source_unreadable",
                status_code=500,
            )
        except GraphSchemaSourceError:
            return _error_response(
                "privacy_map_schema_source_unreadable",
                status_code=500,
            )
        except PrivacyDataMapIdCollisionError:
            return _error_response(
                "privacy_map_id_collision",
                status_code=500,
            )
        except PrivacyDataMapResourceLimitError:
            return _error_response(
                "privacy_map_resource_limit_exceeded",
                status_code=503,
            )
        except PrivacyDataMapProjectionError:
            return _error_response(
                "privacy_map_projection_failed",
                status_code=500,
            )
        except Exception:
            return _error_response(
                "privacy_map_projection_failed",
                status_code=500,
            )

    app.add_api_route(
        PRIVACY_DATA_MAP_PATH,
        get_privacy_data_map,
        methods=["GET"],
        response_model=PrivacyDataMapResponse,
        responses={
            400: {"model": PrivacyDataMapErrorResponse},
            500: {"model": PrivacyDataMapErrorResponse},
            503: {"model": PrivacyDataMapErrorResponse},
        },
    )
    app.add_middleware(_PrivacyDataMapResponseMiddleware)


def _invoke_builder(builder: PrivacyDataMapBuilder) -> _BuildOutcome:
    try:
        response = builder()
        if type(response) is not PrivacyDataMapResponse:
            return _SanitizedBuildFailure(PrivacyDataMapProjectionError)
        return response
    except AuditProjectionSourceError:
        return _SanitizedBuildFailure(AuditProjectionSourceError)
    except AppDeclarationSourceError:
        return _SanitizedBuildFailure(AppDeclarationSourceError)
    except GraphSchemaSourceError:
        return _SanitizedBuildFailure(GraphSchemaSourceError)
    except PrivacyDataMapIdCollisionError:
        return _SanitizedBuildFailure(PrivacyDataMapIdCollisionError)
    except PrivacyDataMapResourceLimitError:
        return _SanitizedBuildFailure(PrivacyDataMapResourceLimitError)
    except PrivacyDataMapProjectionError:
        return _SanitizedBuildFailure(PrivacyDataMapProjectionError)
    except Exception:
        return _SanitizedBuildFailure(PrivacyDataMapProjectionError)


def _error_response(
    code: PrivacyMapErrorCode,
    *,
    status_code: int,
) -> JSONResponse:
    payload = PrivacyDataMapErrorResponse(error=PrivacyDataMapErrorDetail(code=code))
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )
