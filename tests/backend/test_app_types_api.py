import pytest
from fastapi.testclient import TestClient

from backend.agent.tools import ToolEffect, ToolPolicyError, registry
from backend.main import app, app_manager


@pytest.fixture(autouse=True)
def isolated_app_store(tmp_path, monkeypatch):
    import backend.main as main
    from backend.app_store import AppStoreService

    monkeypatch.setattr(main, "app_store", AppStoreService(str(tmp_path / "workspace"), app_manager))


def spec():
    return {
        "spec_version": 1,
        "types": ["calendar", "tasks"],
        "features": [
            {"id": "calendar.events", "status": "implemented", "surfaces": ["data", "ui"]},
            {"id": "calendar.reminders", "status": "planned", "surfaces": []},
        ],
    }


def test_app_type_catalog_endpoint():
    with TestClient(app) as client:
        response = client.get("/api/app-types")
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["spec_version"] == 1
    calendar = next(item for item in catalog["types"] if item["id"] == "calendar")
    assert calendar["title"]["zh"]
    assert calendar["title"]["en"]
    assert "calendar.events" in {feature["id"] for feature in calendar["features"]}


def test_api_roundtrip_spec_and_clear_without_changing_authority():
    app_manager.create_or_update_app("planner", "Planner", js="original", schema_refs=["Event"])
    before = app_manager.get_manifest("planner")
    with TestClient(app, client=("127.0.0.1", 50_000)) as client:
        response = client.patch("/api/apps/planner", json={"app_spec": spec()})
        assert response.status_code == 200
        assert response.json()["app_spec"] == spec()
        assert client.get("/api/apps/planner").json()["app_spec"] == spec()
        assert client.get("/api/apps").json()[0]["app_spec"] == spec()
        store_response = client.get("/api/app-store")
        assert store_response.status_code == 200
        state = store_response.json()
        assert state["app_type_catalog"]["spec_version"] == 1
        assert next(item for item in state["items"] if item["catalog_id"] == "app:planner")["app_spec"] == spec()
        assert client.patch("/api/apps/planner", json={"app_spec": None}).status_code == 200
        assert client.get("/api/apps/planner").json().get("app_spec") is None
        assert client.patch("/api/apps/missing", json={"app_spec": spec()}).status_code == 404
    after = app_manager.get_manifest("planner")
    assert after.grants_digest == before.grants_digest
    assert after.schema_refs == before.schema_refs
    assert app_manager.get_app_files("planner")["js"] == "original"


@pytest.mark.parametrize(
    "invalid",
    [
        {"spec_version": 2, "types": ["calendar"], "features": []},
        {"spec_version": 1, "types": ["calender"], "features": []},
        {"spec_version": 1, "types": ["tasks"], "features": spec()["features"]},
        {
            "spec_version": 1,
            "types": ["calendar"],
            "features": [
                {"id": "calendar.events", "status": "planned", "surfaces": ["ui"]},
            ],
        },
    ],
)
def test_invalid_app_spec_patch_is_atomic(invalid):
    app_manager.create_or_update_app("planner", "Planner", js="original")
    with TestClient(app) as client:
        response = client.patch("/api/apps/planner", json={"title": "Must not change", "app_spec": invalid})
    assert response.status_code == 422
    assert app_manager.get_manifest("planner").title == "Planner"


@pytest.mark.asyncio
async def test_agent_can_read_catalog_and_specs_only_with_workspace_scope():
    # This module intentionally exercises the public gateway, not helper internals.
    from backend.app_types import get_app_type_catalog

    app_manager.create_or_update_app("planner", "Planner", js="original", app_spec=spec())
    catalog = await registry.execute("list_app_types", {}, {"scopes": ["workspace:read"]})
    assert catalog == get_app_type_catalog()
    apps = await registry.execute("list_app_specs", {}, {"scopes": ["workspace:read"]})
    assert next(item for item in apps if item["id"] == "planner")["app_spec"] == spec()
    assert registry.gateway.spec("list_app_types").effect == ToolEffect.READ
    with pytest.raises(ToolPolicyError):
        await registry.execute("list_app_types", {}, {})
