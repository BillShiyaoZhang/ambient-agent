import pytest
from fastapi.testclient import TestClient

import backend.main as main_module
from backend.main import app
from backend.app_manager import AppManager
from backend.app_store import AppStoreService, CapabilityManifest, LayoutConflictError


@pytest.fixture
def app_store(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    apps = workspace / "apps"
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("APPS_DIR", str(apps))
    manager = AppManager()
    return AppStoreService(str(workspace), manager), manager


def capability(**overrides):
    data = {
        "id": "calendar-tools",
        "kind": "mcp",
        "provider": "Acme",
        "title": "Calendar Tools",
        "description": "Manage events.",
        "tags": ["calendar", "events"],
        "input_schema": {"type": "object"},
        "invocation": {"type": "mcp_tool", "app_id": "calendar-backend", "tool_name": "events"},
    }
    data.update(overrides)
    return CapabilityManifest.model_validate(data)


def test_catalog_aggregates_apps_and_registered_capabilities(app_store):
    service, manager = app_store
    manager.create_or_update_app("daily-planner", "Daily Planner", js="export default function App() {}")
    item = service.register_capability(capability())

    state = service.get_state()

    assert item["catalog_id"] == "mcp:acme:calendar-tools"
    assert {entry["catalog_id"] for entry in state["items"]} == {
        "app:daily-planner",
        "mcp:acme:calendar-tools",
    }
    assert set(state["root"]) == {"app:daily-planner", "mcp:acme:calendar-tools"}
    assert state["items"][1]["status"] == "needs_ui"


def test_binding_hides_generated_ui_duplicate_and_unbinding_restores_capability(app_store):
    service, manager = app_store
    catalog_id = service.catalog_id(capability())
    service.register_capability(capability())
    manager.create_or_update_app("calendar-ui", "Calendar UI", js="export default function App() {}")

    service.bind_ui(catalog_id, "calendar-ui")
    state = service.get_state()

    assert [item["catalog_id"] for item in state["items"]] == [catalog_id]
    assert state["items"][0]["ui_app_id"] == "calendar-ui"
    assert state["items"][0]["status"] == "ready"

    assert service.unbind_ui(catalog_id) == "calendar-ui"
    assert service.get_catalog_item(catalog_id)["status"] == "needs_ui"


def test_layout_revision_conflict_returns_current_state(app_store):
    service, manager = app_store
    manager.create_or_update_app("one", "One", js="export default function App() {}")
    manager.create_or_update_app("two", "Two", js="export default function App() {}")
    current = service.get_state()

    saved = service.save_layout(current["revision"], ["app:two", "app:one"], [])
    assert saved["revision"] == current["revision"] + 1
    assert saved["root"] == ["app:two", "app:one"]

    with pytest.raises(LayoutConflictError) as conflict:
        service.save_layout(current["revision"], ["app:one", "app:two"], [])
    assert conflict.value.current["revision"] == saved["revision"]


def test_folder_validation_dissolves_single_item_and_rejects_duplicates(app_store):
    service, manager = app_store
    for app_id in ("one", "two", "three"):
        manager.create_or_update_app(app_id, app_id.title(), js="export default function App() {}")
    state = service.get_state()

    saved = service.save_layout(
        state["revision"],
        ["folder:first", "app:two", "app:three"],
        [{"id": "first", "name": "Only", "items": ["app:one"]}],
    )
    assert saved["folders"] == []
    assert saved["root"][0] == "app:one"

    with pytest.raises(ValueError, match="only once"):
        service.save_layout(
            saved["revision"],
            ["folder:first"],
            [
                {"id": "first", "name": "One", "items": ["app:one", "app:two"]},
                {"id": "second", "name": "Two", "items": ["app:two", "app:three"]},
            ],
        )


def test_generated_ui_id_is_stable_and_safe(app_store):
    service, _ = app_store
    first = service.generated_ui_app_id("skill:Acme:Calendar.Tools")
    second = service.generated_ui_app_id("skill:Acme:Calendar.Tools")
    assert first == second
    assert first.endswith("-ui-adfb4ac8")
    assert first == first.lower()


def test_app_store_api_registration_and_revision_conflict(app_store, monkeypatch):
    service, manager = app_store
    manager.create_or_update_app("one", "One", js="export default function App() {}")
    monkeypatch.setattr(main_module, "app_store", service)
    manifest = capability().model_dump(mode="json")
    catalog_id = service.catalog_id(capability())

    with TestClient(app) as client:
        registered = client.put(f"/api/capabilities/{catalog_id}", json=manifest)
        initial = client.get("/api/app-store")
        first_save = client.put(
            "/api/app-store/layout",
            json={"revision": initial.json()["revision"], "root": ["app:one", catalog_id], "folders": []},
        )
        conflict = client.put(
            "/api/app-store/layout",
            json={"revision": initial.json()["revision"], "root": [catalog_id, "app:one"], "folders": []},
        )

    assert registered.status_code == 200
    assert initial.status_code == 200
    assert first_save.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["state"]["revision"] == first_save.json()["revision"]


def test_capability_manifest_rejects_remote_icons_and_unknown_runtime_fields():
    with pytest.raises(ValueError, match="short local glyph"):
        capability(icon="https://example.com/icon.png")
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        CapabilityManifest.model_validate({**capability().model_dump(), "command": ["secret"]})


def test_v2_capability_exposes_headless_actions_as_ready(app_store):
    service, _ = app_store
    manifest = CapabilityManifest.model_validate(
        {
            "manifest_version": 2,
            "id": "calendar-tools",
            "kind": "mcp",
            "provider": "Acme",
            "title": "Calendar Tools",
            "actions": [
                {
                    "id": "create-event",
                    "title": "Create Event",
                    "input_schema": {"type": "object", "required": ["title"]},
                    "result_schema": {"type": "object"},
                    "invocation": {"type": "mcp_tool", "app_id": "calendar-backend", "tool_name": "create_event"},
                    "recovery": "manual",
                }
            ],
        }
    )

    item = service.register_capability(manifest)

    assert item["status"] == "ready"
    assert item["launch_mode"] == "actions"
    assert item["actions"][0]["id"] == "create-event"
    assert service.get_action(item["catalog_id"], "create-event").invocation.tool_name == "create_event"


def test_v1_capability_normalizes_to_default_run_action():
    manifest = capability()
    action = manifest.normalized_actions()[0]
    assert action.id == "run"
    assert action.input_schema == {"type": "object"}
    assert action.invocation.tool_name == "events"
