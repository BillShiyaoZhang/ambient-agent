import asyncio
import threading
from datetime import UTC, datetime

import pytest
from anyio import to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from pydantic import ConfigDict

import backend.main as main
import backend.privacy_data_map_api as privacy_data_map_api
from backend.graph_db import GraphDatabase
from backend.privacy_data_map import (
    AppDeclarationSnapshot,
    AppDeclarationSourceError,
    AuditProjectionRecord,
    AuditProjectionSourceError,
    AuditSourceHealth,
    GraphSchemaSourceError,
    PrivacyDataMapIdCollisionError,
    PrivacyDataMapProjectionError,
    PrivacyDataMapResourceLimitError,
    PrivacyDataMapResponse,
    PrivacyDataMapService,
    SchemaIdSnapshot,
)
from backend.privacy_data_map_api import install_privacy_data_map


class EmptyAuditProjectionStream:
    def __init__(self):
        self._exhausted = False

    def iter_records(self):
        self._exhausted = True
        return iter(())

    def health_after_exhaustion(self):
        assert self._exhausted
        return AuditSourceHealth()


class ResourceLimitedAfterFirstRecordStream:
    def iter_records(self):
        yield AuditProjectionRecord(
            timestamp=datetime(2026, 7, 19, tzinfo=UTC),
            provider="OpenAI",
            model="partial-topology-canary",
            stage="chat",
        )
        raise PrivacyDataMapResourceLimitError

    def health_after_exhaustion(self):
        raise AssertionError("Health must not be exposed after a failed scan")


def _valid_response(
    generated_at: datetime = datetime(2026, 7, 19, tzinfo=UTC),
):
    return PrivacyDataMapService(clock=lambda: generated_at).build(
        EmptyAuditProjectionStream(),
        AppDeclarationSnapshot(declarations=(), invalid_app_count=0),
        SchemaIdSnapshot(schema_ids=(), unsafe_schema_id_count=0),
    )


def _create_app(builder, *, on_unrelated=None):
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/unrelated")
    async def unrelated():
        if on_unrelated is not None:
            on_unrelated()
        return {"status": "untouched"}

    install_privacy_data_map(app, builder=builder)
    return app


async def _request(app, method="GET", *, headers=None):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
    ) as client:
        return await client.request(
            method,
            "/api/privacy-data-map",
            headers=headers,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("base_url", "headers"),
    [
        (
            "http://192.168.1.40:8000",
            {
                "origin": "http://192.168.1.40:5173",
                "accept": "application/json",
            },
        ),
        ("http://ambient-agent.local:8000", None),
        (
            "https://ambient-agent.example",
            {
                "origin": "https://workspace.example",
                "forwarded": "host=ambient-agent.example;proto=https",
            },
        ),
    ],
    ids=["lan-browser", "non-browser", "platform-managed-proxy"],
)
async def test_privacy_map_does_not_add_a_route_local_network_gate(
    base_url,
    headers,
):
    async with AsyncClient(
        transport=ASGITransport(app=_create_app(_valid_response)),
        base_url=base_url,
    ) as client:
        response = await client.get("/api/privacy-data-map", headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["contract_version"] == 1


@pytest.mark.asyncio
async def test_privacy_map_preflight_uses_the_enclosing_app_cors_policy():
    app = _create_app(_valid_response)
    response = await _request(
        app,
        method="OPTIONS",
        headers={
            "host": "192.168.1.40:8000",
            "origin": "http://192.168.1.40:5173",
            "access-control-request-method": "POST",
            "access-control-request-headers": "Authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://192.168.1.40:5173"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_privacy_map_uses_framework_method_handling_for_non_get_requests():
    response = await _request(
        _create_app(_valid_response),
        method="POST",
        headers={
            "host": "192.168.1.40:8000",
            "origin": "http://192.168.1.40:5173",
        },
    )

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_privacy_map_response_policy_does_not_change_unrelated_api_methods():
    app = _create_app(_valid_response)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://untrusted.example",
    ) as client:
        response = await client.post("/api/unrelated")

    assert response.status_code == 200
    assert response.json() == {"status": "untouched"}
    assert "cache-control" not in response.headers


@pytest.mark.asyncio
async def test_main_app_preserves_existing_cors_origins_for_unrelated_routes():
    origin = "https://forwarded-development.example"
    async with AsyncClient(
        transport=ASGITransport(app=main.app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.options(
            "/health",
            headers={
                "origin": origin,
                "access-control-request-method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.asyncio
async def test_main_app_preserves_existing_cors_for_unrelated_actual_requests():
    origin = "https://forwarded-development.example"
    async with AsyncClient(
        transport=ASGITransport(app=main.app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.get(
            "/health",
            headers={"origin": origin},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.asyncio
async def test_main_app_wires_the_privacy_map_to_the_current_workspace(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(main, "WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(main, "graph_db", GraphDatabase(str(workspace)))

    async with AsyncClient(
        transport=ASGITransport(app=main.app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.get(
            "/api/privacy-data-map",
            headers={
                "host": "localhost:8000",
                "origin": "http://localhost:5173",
            },
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["nodes"] == [
        {
            "id": "platform:ambient-agent",
            "kind": "platform",
            "label": "Ambient Agent",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status_code", "error_code"),
    [
        (
            AuditProjectionSourceError(),
            500,
            "privacy_map_audit_source_unreadable",
        ),
        (
            AppDeclarationSourceError(),
            500,
            "privacy_map_app_source_unreadable",
        ),
        (
            GraphSchemaSourceError(),
            500,
            "privacy_map_schema_source_unreadable",
        ),
        (
            PrivacyDataMapIdCollisionError(),
            500,
            "privacy_map_id_collision",
        ),
        (
            PrivacyDataMapProjectionError(),
            500,
            "privacy_map_projection_failed",
        ),
        (
            PrivacyDataMapResourceLimitError(),
            503,
            "privacy_map_resource_limit_exceeded",
        ),
        (
            RuntimeError("raw-exception-canary"),
            500,
            "privacy_map_projection_failed",
        ),
    ],
)
async def test_privacy_map_returns_stable_sanitized_failure_contract(
    failure,
    status_code,
    error_code,
):
    def failing_builder():
        raise failure

    response = await _request(
        _create_app(failing_builder),
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:5173",
        },
    )

    assert response.status_code == status_code
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contract_version": 1,
        "error": {
            "code": error_code,
            "message": "Privacy Map is temporarily unavailable.",
        },
    }
    assert "raw-exception-canary" not in response.text
    assert "nodes" not in response.json()


@pytest.mark.asyncio
async def test_privacy_map_discards_partial_topology_when_a_real_projection_hits_a_resource_limit():
    def builder():
        return PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
            ResourceLimitedAfterFirstRecordStream(),
            AppDeclarationSnapshot(declarations=(), invalid_app_count=0),
            SchemaIdSnapshot(schema_ids=(), unsafe_schema_id_count=0),
        )

    response = await _request(
        _create_app(builder),
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:5173",
        },
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contract_version": 1,
        "error": {
            "code": "privacy_map_resource_limit_exceeded",
            "message": "Privacy Map is temporarily unavailable.",
        },
    }
    assert "partial-topology-canary" not in response.text
    assert "nodes" not in response.json()


@pytest.mark.asyncio
async def test_privacy_map_sanitizes_an_invalid_builder_result():
    response = await _request(
        _create_app(lambda: object()),
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:5173",
        },
    )

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contract_version": 1,
        "error": {
            "code": "privacy_map_projection_failed",
            "message": "Privacy Map is temporarily unavailable.",
        },
    }


@pytest.mark.asyncio
async def test_privacy_map_rejects_response_subclasses_without_serializing_extra_fields():
    class ExtendedPrivacyDataMapResponse(PrivacyDataMapResponse):
        model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

        debug_payload: str

    base_response = _valid_response()

    def extended_builder():
        return ExtendedPrivacyDataMapResponse(
            **base_response.model_dump(),
            debug_payload="response-subclass-canary",
        )

    response = await _request(
        _create_app(extended_builder),
        headers={
            "host": "localhost:8000",
            "origin": "http://localhost:5173",
        },
    )

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "privacy_map_projection_failed"
    assert "response-subclass-canary" not in response.text
    assert "debug_payload" not in response.text


@pytest.mark.asyncio
async def test_privacy_map_rejects_nonempty_query_without_invoking_builder():
    builder_called = False

    def builder():
        nonlocal builder_called
        builder_called = True
        return _valid_response()

    app = _create_app(builder)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
    ) as client:
        response = await client.get(
            "/api/privacy-data-map?unexpected=1",
            headers={
                "host": "localhost:8000",
                "origin": "http://localhost:5173",
            },
        )

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contract_version": 1,
        "error": {
            "code": "privacy_map_invalid_request",
            "message": "Privacy Map is temporarily unavailable.",
        },
    }
    assert builder_called is False


@pytest.mark.asyncio
async def test_privacy_map_trailing_slash_is_not_redirected_or_built():
    builder_called = False

    def builder():
        nonlocal builder_called
        builder_called = True
        return _valid_response()

    app = _create_app(builder)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/api/privacy-data-map/",
            headers={
                "host": "localhost:8000",
                "origin": "http://localhost:5173",
            },
        )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert builder_called is False


@pytest.mark.asyncio
async def test_privacy_map_builder_does_not_block_unrelated_requests():
    unrelated_reached = threading.Event()

    def slow_builder():
        if not unrelated_reached.wait(timeout=0.5):
            raise RuntimeError("event loop remained blocked")
        return _valid_response()

    app = _create_app(
        slow_builder,
        on_unrelated=unrelated_reached.set,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
    ) as client:
        privacy_request = asyncio.create_task(
            client.get(
                "/api/privacy-data-map",
                headers={
                    "host": "localhost:8000",
                    "origin": "http://localhost:5173",
                },
            )
        )
        await asyncio.sleep(0)
        unrelated_response = await client.post("/api/unrelated")
        privacy_response = await privacy_request

    assert unrelated_response.status_code == 200
    assert privacy_response.status_code == 200


@pytest.mark.asyncio
async def test_many_followers_do_not_starve_an_unrelated_sync_endpoint():
    build_started = threading.Event()
    release_build = threading.Event()
    unrelated_reached = threading.Event()
    build_count = 0
    count_lock = threading.Lock()

    def blocking_builder():
        nonlocal build_count
        with count_lock:
            build_count += 1
        build_started.set()
        if not release_build.wait(timeout=5):
            raise RuntimeError("test did not release the in-flight build")
        return _valid_response()

    app = _create_app(blocking_builder)

    @app.get("/api/unrelated-sync")
    def unrelated_sync():
        unrelated_reached.set()
        return {"status": "scheduled"}

    headers = {
        "host": "localhost:8000",
        "origin": "http://localhost:5173",
    }
    limiter = to_thread.current_default_thread_limiter()
    original_total_tokens = limiter.total_tokens
    limiter.total_tokens = 2
    request_tasks = []
    unrelated_completed_while_build_blocked = False

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://localhost:8000",
        ) as client:
            leader = asyncio.create_task(client.get("/api/privacy-data-map", headers=headers))
            request_tasks.append(leader)
            assert await asyncio.to_thread(build_started.wait, 1)

            followers = [asyncio.create_task(client.get("/api/privacy-data-map", headers=headers)) for _ in range(8)]
            request_tasks.extend(followers)
            for _ in range(20):
                await asyncio.sleep(0)

            unrelated = asyncio.create_task(client.get("/api/unrelated-sync"))
            request_tasks.append(unrelated)
            unrelated_completed_while_build_blocked = await asyncio.to_thread(
                unrelated_reached.wait,
                1,
            )

            release_build.set()
            responses = await asyncio.gather(*request_tasks)
    finally:
        release_build.set()
        if request_tasks:
            await asyncio.gather(*request_tasks, return_exceptions=True)
        limiter.total_tokens = original_total_tokens

    assert unrelated_completed_while_build_blocked, (
        "waiting Privacy Map followers consumed every synchronous worker token"
    )
    assert responses[-1].status_code == 200
    assert responses[-1].json() == {"status": "scheduled"}
    assert all(response.status_code == 200 for response in responses[:-1])
    assert build_count == 1


@pytest.mark.asyncio
async def test_concurrent_privacy_map_gets_share_one_exact_build_then_rebuild():
    build_started = threading.Event()
    duplicate_build_started = threading.Event()
    release_build = threading.Event()
    build_count = 0
    count_lock = threading.Lock()

    def builder():
        nonlocal build_count
        with count_lock:
            build_count += 1
            build_number = build_count
        if build_number == 1:
            build_started.set()
        else:
            duplicate_build_started.set()
        if not release_build.wait(timeout=2):
            raise RuntimeError("test did not release the in-flight build")
        return _valid_response(datetime(2026, 7, 19, 0, 0, build_number, tzinfo=UTC))

    app = _create_app(builder)
    headers = {
        "host": "localhost:8000",
        "origin": "http://localhost:5173",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
    ) as client:
        first_request = asyncio.create_task(client.get("/api/privacy-data-map", headers=headers))
        assert await asyncio.to_thread(build_started.wait, 2)

        second_request = asyncio.create_task(client.get("/api/privacy-data-map", headers=headers))
        duplicate_was_invoked = await asyncio.to_thread(
            duplicate_build_started.wait,
            0.5,
        )

        unrelated_response = await client.post("/api/unrelated")
        release_build.set()
        first_response, second_response = await asyncio.gather(
            first_request,
            second_request,
        )
        later_response = await client.get("/api/privacy-data-map", headers=headers)

    assert duplicate_was_invoked is False
    assert unrelated_response.status_code == 200
    assert "cache-control" not in unrelated_response.headers
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.headers["cache-control"] == "no-store"
    assert second_response.headers["cache-control"] == "no-store"
    assert first_response.json() == second_response.json()
    assert first_response.json()["generated_at"] == "2026-07-19T00:00:01Z"
    assert later_response.status_code == 200
    assert later_response.headers["cache-control"] == "no-store"
    assert later_response.json()["generated_at"] == "2026-07-19T00:00:02Z"
    assert build_count == 2


@pytest.mark.asyncio
async def test_concurrent_privacy_map_gets_share_one_sanitized_error_classification():
    build_started = threading.Event()
    duplicate_build_started = threading.Event()
    release_build = threading.Event()
    build_count = 0
    count_lock = threading.Lock()

    def failing_builder():
        nonlocal build_count
        with count_lock:
            build_count += 1
            build_number = build_count
        if build_number == 1:
            build_started.set()
        else:
            duplicate_build_started.set()
        if not release_build.wait(timeout=2):
            raise RuntimeError("test did not release the in-flight build")
        if build_number == 1:
            raise AuditProjectionSourceError("first-raw-error-canary")
        raise PrivacyDataMapResourceLimitError("duplicate-raw-error-canary")

    app = _create_app(failing_builder)
    headers = {
        "host": "localhost:8000",
        "origin": "http://localhost:5173",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://localhost:8000",
    ) as client:
        first_request = asyncio.create_task(client.get("/api/privacy-data-map", headers=headers))
        assert await asyncio.to_thread(build_started.wait, 2)

        second_request = asyncio.create_task(client.get("/api/privacy-data-map", headers=headers))
        duplicate_was_invoked = await asyncio.to_thread(
            duplicate_build_started.wait,
            0.5,
        )

        release_build.set()
        responses = await asyncio.gather(first_request, second_request)

    expected_body = {
        "contract_version": 1,
        "error": {
            "code": "privacy_map_audit_source_unreadable",
            "message": "Privacy Map is temporarily unavailable.",
        },
    }
    assert duplicate_was_invoked is False
    assert build_count == 1
    assert [response.status_code for response in responses] == [500, 500]
    assert [response.json() for response in responses] == [
        expected_body,
        expected_body,
    ]
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    assert all("raw-error-canary" not in response.text for response in responses)


@pytest.mark.asyncio
async def test_cancelling_first_caller_does_not_cancel_or_duplicate_active_build():
    build_started = threading.Event()
    duplicate_build_started = threading.Event()
    release_build = threading.Event()
    build_count = 0
    count_lock = threading.Lock()

    def builder():
        nonlocal build_count
        with count_lock:
            build_count += 1
            build_number = build_count
        if build_number == 1:
            build_started.set()
            assert release_build.wait(timeout=2)
        else:
            duplicate_build_started.set()
        return _valid_response(datetime(2026, 7, 19, 0, 0, build_number, tzinfo=UTC))

    single_flight = privacy_data_map_api._PrivacyDataMapSingleFlight(builder)
    first_caller = asyncio.create_task(single_flight.run())
    assert await asyncio.to_thread(build_started.wait, 2)

    first_caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_caller

    later_caller = asyncio.create_task(single_flight.run())
    duplicate_was_invoked = await asyncio.to_thread(
        duplicate_build_started.wait,
        0.5,
    )

    assert duplicate_was_invoked is False
    assert build_count == 1

    release_build.set()
    result = await later_caller

    assert result.generated_at == datetime(2026, 7, 19, 0, 0, 1, tzinfo=UTC)
    assert (await single_flight.run()).generated_at == datetime(
        2026,
        7,
        19,
        0,
        0,
        2,
        tzinfo=UTC,
    )


@pytest.mark.asyncio
async def test_single_flight_sanitizes_base_exception_for_every_waiter_and_clears_state():
    class FatalBuilderSignal(BaseException):
        pass

    build_started = threading.Event()
    release_build = threading.Event()
    build_count = 0
    outcomes = []
    outcomes_lock = threading.Lock()

    def builder():
        nonlocal build_count
        build_count += 1
        if build_count == 1:
            build_started.set()
            assert release_build.wait(timeout=1)
            raise FatalBuilderSignal
        return _valid_response(datetime(2026, 7, 19, 0, 0, build_count, tzinfo=UTC))

    single_flight = privacy_data_map_api._PrivacyDataMapSingleFlight(builder)

    async def call_single_flight(label):
        try:
            result = await single_flight.run()
        except BaseException as error:
            outcome = (label, type(error))
        else:
            outcome = (label, result.generated_at)
        with outcomes_lock:
            outcomes.append(outcome)

    leader = asyncio.create_task(call_single_flight("leader"))
    assert await asyncio.to_thread(build_started.wait, 1)
    follower = asyncio.create_task(call_single_flight("follower"))
    await asyncio.sleep(0)
    release_build.set()
    await asyncio.gather(leader, follower)

    assert sorted(outcomes, key=lambda item: item[0]) == [
        ("follower", PrivacyDataMapProjectionError),
        ("leader", PrivacyDataMapProjectionError),
    ]
    assert (await single_flight.run()).generated_at == datetime(
        2026,
        7,
        19,
        0,
        0,
        2,
        tzinfo=UTC,
    )
