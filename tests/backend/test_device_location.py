import asyncio

import pytest

from backend.device_location import DeviceLocationError, normalize_location_options, normalize_location_result
from backend.client_widget_runtime import LockedClientWidgetRuntimeConnection
from backend.capabilities.models import CapabilityGrant
from backend.capabilities.policy import CapabilityAuthorizer, CapabilityDenied


def test_location_grant_and_current_manifest_authorization():
    grant = CapabilityGrant.from_dict({"id": "device.location", "scope": {"operations": ["current"]}})
    manifest = type("Manifest", (), {"capabilities": [grant.to_dict()], "revision": "v1", "grants_digest": "g1"})()
    authorizer = CapabilityAuthorizer(manifest_loader=lambda _: manifest)
    authorizer.authorize_location("weather", "current", "v1", "g1")
    with pytest.raises(CapabilityDenied, match="stale"):
        authorizer.authorize_location("weather", "current", "old", "g1")
    manifest.capabilities = []
    with pytest.raises(CapabilityDenied):
        authorizer.authorize_location("weather", "current")


@pytest.mark.parametrize("operations", [[], ["watch"], ["current", "watch"], "current"])
def test_location_grant_rejects_tracking(operations):
    with pytest.raises(ValueError):
        CapabilityGrant.from_dict({"id": "device.location", "scope": {"operations": operations}})


def test_location_payloads_are_bounded_and_minimal():
    assert normalize_location_options(None) == {"timeout": 10000, "maximumAge": 0}
    assert normalize_location_options({"timeout": 1000, "maximumAge": 300000}) == {
        "timeout": 1000,
        "maximumAge": 300000,
    }
    result = {"latitude": 31.2, "longitude": 121.5, "accuracy": 25.0, "timestamp": 1720000000000}
    assert normalize_location_result(result) == result


@pytest.mark.parametrize(
    "options",
    [{"timeout": True}, {"timeout": 0}, {"timeout": 30001}, {"maximumAge": -1}, {"enableHighAccuracy": True}, []],
)
def test_location_options_reject_unbounded_or_unknown_values(options):
    with pytest.raises(DeviceLocationError):
        normalize_location_options(options)


@pytest.mark.parametrize(
    "change",
    [
        {"latitude": 91},
        {"longitude": -181},
        {"accuracy": -1},
        {"timestamp": float("nan")},
        {"altitude": 2},
        {"latitude": True},
    ],
)
def test_location_results_reject_invalid_coordinates_and_extra_fields(change):
    result = {"latitude": 31, "longitude": 121, "accuracy": 10, "timestamp": 1720000000000, **change}
    with pytest.raises(DeviceLocationError):
        normalize_location_result(result)


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)

    async def close(self, code=1000):
        pass


@pytest.mark.asyncio
async def test_location_broker_correlates_responses_and_rejects_unsolicited_or_duplicate():
    socket = Socket()
    connection = LockedClientWidgetRuntimeConnection(socket)
    task = asyncio.create_task(connection.request_location("rpc-1", {"timeout": 1000, "maximumAge": 0}))
    await asyncio.sleep(0)
    request = socket.messages[0]
    assert request["type"] == "device_request"
    assert request["rpc_request_id"] == "rpc-1"
    assert connection.resolve_device_response({"type": "device_response", "request_id": "other", "result": {}}) is False
    result = {"latitude": 31, "longitude": 121, "accuracy": 10, "timestamp": 1720000000000}
    assert (
        connection.resolve_device_response(
            {"type": "device_response", "request_id": request["request_id"], "result": result}
        )
        is True
    )
    assert await task == result
    assert (
        connection.resolve_device_response(
            {"type": "device_response", "request_id": request["request_id"], "result": result}
        )
        is False
    )


@pytest.mark.asyncio
async def test_location_broker_disconnect_cancels_pending_request():
    connection = LockedClientWidgetRuntimeConnection(Socket())
    task = asyncio.create_task(connection.request_location("rpc-1", {"timeout": 1000, "maximumAge": 0}))
    await asyncio.sleep(0)
    connection.cancel_device_requests()
    with pytest.raises(DeviceLocationError) as error:
        await task
    assert error.value.code == "device_location_unavailable"


@pytest.mark.asyncio
async def test_location_broker_bounds_concurrency_and_timeout(monkeypatch):
    connection = LockedClientWidgetRuntimeConnection(Socket())
    pending = asyncio.create_task(connection.request_location("rpc-1", {"timeout": 1000, "maximumAge": 0}))
    await asyncio.sleep(0)
    with pytest.raises(DeviceLocationError) as busy:
        await connection.request_location("rpc-2", {"timeout": 1000, "maximumAge": 0})
    assert busy.value.code == "device_location_busy"
    connection.cancel_device_requests()
    with pytest.raises(DeviceLocationError):
        await pending

    async def immediate_timeout(future, timeout):
        future.cancel()
        assert timeout == 2
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)
    with pytest.raises(DeviceLocationError) as timeout:
        await connection.request_location("rpc-3", {"timeout": 1000, "maximumAge": 0})
    assert timeout.value.code == "device_location_timeout"
    assert connection._device_requests == {}


@pytest.mark.asyncio
async def test_headless_location_cannot_use_a_server_device(monkeypatch):
    from backend import main
    from backend.widget_runtime import WidgetRuntimeBinding

    grant = {"id": "device.location", "scope": {"operations": ["current"]}}
    manifest = type("Manifest", (), {"capabilities": [grant], "revision": "v1", "grants_digest": "g1"})()
    monkeypatch.setattr(main, "capability_authorizer", CapabilityAuthorizer(manifest_loader=lambda _: manifest))
    binding = WidgetRuntimeBinding("headless", "weather", "v1", "g1", "artifact", Socket())
    with pytest.raises(DeviceLocationError) as error:
        await main._handle_widget_runtime_rpc(binding, "location.getCurrentPosition", {"options": {}})
    assert error.value.code == "device_location_unavailable"


@pytest.mark.asyncio
async def test_location_permission_is_rechecked_after_browser_response(monkeypatch):
    from backend import main
    from backend.client_widget_runtime import ClientWidgetRuntimeBinding

    manifest = type(
        "Manifest",
        (),
        {
            "capabilities": [{"id": "device.location", "scope": {"operations": ["current"]}}],
            "revision": "v1",
            "grants_digest": "g1",
        },
    )()
    monkeypatch.setattr(main, "capability_authorizer", CapabilityAuthorizer(manifest_loader=lambda _: manifest))
    result = asyncio.get_running_loop().create_future()

    class LocationConnection:
        async def request_location(self, _request_id, _options):
            return await result

    binding = ClientWidgetRuntimeBinding("browser", "weather", "v1", "g1", "artifact", LocationConnection())
    monkeypatch.setattr(
        main.client_widget_runtime_sessions, "binding", lambda session_id: binding if session_id == "browser" else None
    )
    task = asyncio.create_task(
        main._handle_widget_runtime_rpc(
            binding, "location.getCurrentPosition", {"options": {}, "_runtime_request_id": "rpc-1"}
        )
    )
    await asyncio.sleep(0)
    manifest.capabilities = []
    result.set_result({"latitude": 31, "longitude": 121, "accuracy": 10, "timestamp": 1720000000000})
    with pytest.raises(CapabilityDenied) as error:
        await task
    assert error.value.code == "capability_not_granted"
