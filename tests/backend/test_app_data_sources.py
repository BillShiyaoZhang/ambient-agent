import json

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.main as main_module
from backend.app_data_sources import AppDataSourceError, AppDataSourceGateway
from backend.app_manager import AppManager


def _manager_with_source(tmp_path, monkeypatch) -> AppManager:
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("APPS_DIR", str(apps_dir))
    manager = AppManager()
    manager.create_or_update_app(
        "weather-app",
        "Weather",
        js="export default function App() { return null; }",
    )
    manifest_path = apps_dir / "weather-app" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["data_sources"] = {
        "forecast": {
            "type": "http",
            "base_url": "https://api.open-meteo.com",
            "allowed_paths": ["/v1/forecast"],
            "methods": ["GET"],
            "response_format": "json",
            "response_limit": 4096,
        }
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manager


@pytest.mark.asyncio
async def test_gateway_uses_manifest_source_and_returns_json(tmp_path, monkeypatch):
    manager = _manager_with_source(tmp_path, monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.open-meteo.com"
        assert request.url.path == "/v1/forecast"
        assert request.url.params["latitude"] == "31.23"
        return httpx.Response(200, json={"temperature": 28})

    async def public_host(_hostname: str) -> None:
        return None

    gateway = AppDataSourceGateway(
        manager,
        tmp_path,
        transport=httpx.MockTransport(handler),
        public_host_resolver=public_host,
    )

    result = await gateway.request(
        "weather-app",
        "forecast",
        {"path": "/v1/forecast", "method": "GET", "query": {"latitude": 31.23}},
    )

    assert result == {"temperature": 28}


@pytest.mark.asyncio
async def test_gateway_failure_is_actionable_and_available_to_the_next_agent_run(tmp_path, monkeypatch):
    manager = _manager_with_source(tmp_path, monkeypatch)
    gateway = AppDataSourceGateway(manager, tmp_path)

    with pytest.raises(AppDataSourceError) as exc_info:
        await gateway.request(
            "weather-app",
            "missing-source",
            {"path": "/v1/forecast", "method": "GET"},
        )

    assert exc_info.value.code == "data_source_not_declared"
    assert "manifest.json" in exc_info.value.hint
    diagnostics = gateway.recent_diagnostics("weather-app")
    assert diagnostics[-1]["code"] == "data_source_not_declared"
    assert diagnostics[-1]["source_id"] == "missing-source"
    assert "manifest.json" in diagnostics[-1]["hint"]


@pytest.mark.asyncio
async def test_gateway_rejects_oversized_query_before_network_access(tmp_path, monkeypatch):
    manager = _manager_with_source(tmp_path, monkeypatch)
    gateway = AppDataSourceGateway(manager, tmp_path)

    with pytest.raises(AppDataSourceError) as exc_info:
        await gateway.request(
            "weather-app",
            "forecast",
            {"path": "/v1/forecast", "method": "GET", "query": {"latitude": "1" * 20_000}},
        )

    assert exc_info.value.code == "data_source_query_too_large"
    assert "16 KiB" in exc_info.value.message


def test_data_source_api_returns_data_and_structured_errors(tmp_path, monkeypatch):
    manager = _manager_with_source(tmp_path, monkeypatch)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"temperature": 28})

    async def public_host(_hostname: str) -> None:
        return None

    gateway = AppDataSourceGateway(
        manager,
        tmp_path,
        transport=httpx.MockTransport(handler),
        public_host_resolver=public_host,
    )
    monkeypatch.setattr(main_module, "app_data_source_gateway", gateway)

    with TestClient(main_module.app) as client:
        success = client.post(
            "/api/apps/weather-app/data-sources/forecast/request",
            json={"path": "/v1/forecast", "method": "GET", "query": {"latitude": 31.23}},
        )
        failure = client.post(
            "/api/apps/weather-app/data-sources/missing/request",
            json={"path": "/v1/forecast", "method": "GET"},
        )

    assert success.status_code == 200
    assert success.json() == {"data": {"temperature": 28}}
    assert failure.status_code == 422
    assert failure.json()["detail"]["code"] == "data_source_not_declared"
    assert "hint" in failure.json()["detail"]
