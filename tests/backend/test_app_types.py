from copy import deepcopy

import pytest

from backend.app_manifest import AppManifest, ManifestValidationError
from backend.app_manager import AppManager
from backend.app_store import AppStoreService, CapabilityManifest
from backend.app_types import AppSpecificationError, validate_app_spec


def app_spec(**overrides):
    result = {
        "spec_version": 1,
        "types": ["calendar", "tasks"],
        "features": [
            {"id": "calendar.events", "status": "implemented", "surfaces": ["data", "ui"]},
            {"id": "calendar.reminders", "status": "planned", "surfaces": [], "notes": "Later"},
            {"id": "tasks.items", "status": "partial", "surfaces": ["ui"]},
        ],
    }
    result.update(overrides)
    return result


def manifest_data(**overrides):
    result = {
        "manifest_version": 2,
        "id": "planner",
        "title": "Planner",
        "description": "",
        "app_version": "0.1.0",
        "intents": [],
        "schema_refs": [],
        "capabilities": [],
    }
    result.update(overrides)
    return result


def test_app_spec_round_trips_multitype_statuses_and_surfaces():
    data = manifest_data(app_spec=app_spec())

    manifest = AppManifest.from_dict(data, expected_app_id="planner")

    assert manifest.to_dict() == data
    assert manifest.grants_digest == AppManifest.from_dict(manifest_data(), expected_app_id="planner").grants_digest


def test_custom_types_and_features_are_extensible_with_declared_namespace():
    spec = app_spec(
        types=["tasks", "custom:lab-book"],
        features=[{"id": "custom:lab-book.samples", "status": "implemented", "surfaces": ["tools"]}],
    )
    manifest = AppManifest.from_dict(manifest_data(app_spec=spec), expected_app_id="planner")

    assert manifest.to_dict()["app_spec"] == spec


def test_legacy_manifest_and_explicit_null_remain_unclassified():
    for data in (manifest_data(), manifest_data(app_spec=None)):
        manifest = AppManifest.from_dict(data, expected_app_id="planner")
        assert manifest.app_spec is None
        assert manifest.to_dict() == manifest_data()


@pytest.mark.parametrize(
    "spec",
    [
        [],
        {},
        app_spec(spec_version=True),
        app_spec(spec_version=2),
        app_spec(extra=True),
        app_spec(types=[]),
        app_spec(types="calendar"),
        app_spec(types=["calendar", "calendar"]),
        app_spec(types=["calender"]),
        app_spec(types=["custom:Lab"]),
        app_spec(types=["custom:lab.book"]),
        app_spec(types=[1]),
        app_spec(types=[[]]),
        app_spec(types=[f"custom:group-{index}" for index in range(21)]),
        app_spec(types=["custom:" + "x" * 201]),
        app_spec(features="calendar.events"),
        app_spec(features=[None]),
        app_spec(
            types=["custom:lab"],
            features=[
                {"id": f"custom:lab.sample-{index}", "status": "planned", "surfaces": []} for index in range(101)
            ],
        ),
        app_spec(features=[{"id": "calendar.unknown", "status": "implemented", "surfaces": ["ui"]}]),
        app_spec(types=["tasks"]),
        app_spec(features=[{"id": "custom:lab.samples", "status": "implemented", "surfaces": ["ui"]}]),
        app_spec(types=["custom:lab"], features=[{"id": "custom:lab.Sample", "status": "partial", "surfaces": ["ui"]}]),
        app_spec(features=[{"id": "calendar.events", "status": "verified", "surfaces": ["ui"]}]),
        app_spec(features=[{"id": "calendar.events", "status": [], "surfaces": ["ui"]}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": []}]),
        app_spec(features=[{"id": "calendar.events", "status": "partial", "surfaces": []}]),
        app_spec(features=[{"id": "calendar.events", "status": "planned", "surfaces": ["ui"]}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": ["ui", "ui"]}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": ["network"]}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": [[]]}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": "ui"}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented"}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": ["ui"], "verified": True}]),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": ["ui"], "notes": 1}]),
        app_spec(
            features=[{"id": "calendar.events", "status": "implemented", "surfaces": ["ui"], "notes": "x" * 2001}]
        ),
        app_spec(features=[{"id": "calendar.events", "status": "implemented", "surfaces": ["ui"]}] * 2),
    ],
)
def test_invalid_app_specs_are_rejected(spec):
    with pytest.raises(ManifestValidationError, match="app_spec"):
        AppManifest.from_dict(manifest_data(app_spec=spec), expected_app_id="planner")


def test_catalog_type_objects_are_rejected_with_exact_path_without_copying_contents():
    spec = app_spec(
        types=[
            {"id": "custom:weather-app", "title": {"en": "Weather", "zh": "天气"}, "secret": "private-metadata-value"}
        ],
        features=[{"id": "custom:weather-app.display", "status": "partial", "surfaces": ["ui"]}],
    )
    original = deepcopy(spec)
    with pytest.raises(AppSpecificationError) as failure:
        validate_app_spec(spec)
    error = failure.value
    assert error.path == "app_spec.types[0]"
    assert error.expected == "non-empty type ID string (max 200 characters)"
    assert error.observed == "object"
    assert "app_spec.types[0]" in str(error)
    assert "use the ID string, not a catalog object" in str(error)
    assert "private-metadata-value" not in str(error)
    assert "custom:weather-app" not in str(error)
    assert spec == original


@pytest.mark.parametrize(
    ("spec", "path", "observed"),
    [
        (app_spec(types=["calendar", {"id": "tasks"}]), "app_spec.types[1]", "object"),
        (app_spec(types=[[]]), "app_spec.types[0]", "array"),
        (app_spec(types=[None]), "app_spec.types[0]", "null"),
        (app_spec(types=[True]), "app_spec.types[0]", "boolean"),
        (app_spec(types=[1]), "app_spec.types[0]", "integer"),
        (app_spec(types=[1.5]), "app_spec.types[0]", "number"),
        (app_spec(types=[""]), "app_spec.types[0]", "string"),
        (app_spec(types=["x" * 201]), "app_spec.types[0]", "string"),
        (app_spec(types=["calendar", "calendar"]), "app_spec.types[1]", "duplicate string"),
        (app_spec(spec_version=True), "app_spec.spec_version", "boolean"),
        (app_spec(types="calendar"), "app_spec.types", "string"),
        (app_spec(features={}), "app_spec.features", "object"),
        (app_spec(features=[[]]), "app_spec.features[0]", "array"),
        (
            app_spec(features=[{"id": {"id": "calendar.events"}, "status": "partial", "surfaces": ["ui"]}]),
            "app_spec.features[0].id",
            "object",
        ),
        (
            app_spec(features=[{"id": "calendar.events", "status": [], "surfaces": ["ui"]}]),
            "app_spec.features[0].status",
            "array",
        ),
        (
            app_spec(features=[{"id": "calendar.events", "status": "partial", "surfaces": "ui"}]),
            "app_spec.features[0].surfaces",
            "string",
        ),
        (
            app_spec(features=[{"id": "calendar.events", "status": "partial", "surfaces": ["ui", {}]}]),
            "app_spec.features[0].surfaces[1]",
            "object",
        ),
        (
            app_spec(features=[{"id": "calendar.events", "status": "planned", "surfaces": ["ui"]}]),
            "app_spec.features[0].surfaces",
            "non-empty array",
        ),
        (
            app_spec(features=[{"id": "calendar.events", "status": "partial", "surfaces": []}]),
            "app_spec.features[0].surfaces",
            "empty array",
        ),
        (
            app_spec(
                features=[
                    {
                        "id": "calendar.events",
                        "status": "partial",
                        "surfaces": ["ui"],
                        "notes": {"secret": "private-value"},
                    }
                ]
            ),
            "app_spec.features[0].notes",
            "object",
        ),
    ],
)
def test_app_spec_shape_diagnostics_report_precise_paths_and_safe_observed_types(spec, path, observed):
    with pytest.raises(AppSpecificationError) as failure:
        validate_app_spec(spec)
    error = failure.value
    assert error.path == path
    assert error.observed == observed
    assert error.expected
    assert path in str(error)
    assert "private-value" not in str(error)


def test_custom_weather_type_id_string_with_declared_features_remains_valid():
    spec = app_spec(
        types=["custom:weather-app"],
        features=[
            {"id": "custom:weather-app.display", "status": "partial", "surfaces": ["ui"]},
            {"id": "custom:weather-app.retry", "status": "partial", "surfaces": ["ui"]},
        ],
    )
    assert validate_app_spec(spec).to_dict() == spec


def test_type_catalog_is_bilingual_complete_and_returns_independent_copies():
    from backend.app_types import get_app_type_catalog

    catalog = get_app_type_catalog()
    assert catalog["spec_version"] == 1
    assert [item["id"] for item in catalog["types"]] == [
        "calendar",
        "tasks",
        "notes",
        "contacts",
        "documents",
        "messaging",
        "finance",
        "media",
        "dashboard",
        "utility",
    ]
    ids = []
    for app_type in catalog["types"]:
        assert set(app_type["title"]) == {"zh", "en"}
        assert set(app_type["description"]) == {"zh", "en"}
        assert len(app_type["features"]) >= 2
        for feature in app_type["features"]:
            assert feature["id"].startswith(f"{app_type['id']}.")
            assert set(feature["title"]) == {"zh", "en"}
            ids.append(feature["id"])
    assert len(ids) == len(set(ids))
    assert {
        "calendar.events",
        "calendar.reminders",
        "calendar.views",
        "calendar.recurrence",
        "tasks.items",
        "tasks.complete",
    } <= set(ids)
    expected = deepcopy(catalog)
    catalog["types"][0]["title"]["zh"] = "changed"
    assert get_app_type_catalog() == expected


def test_app_spec_at_declared_limits_preserves_order_and_owns_its_values():
    custom_types = [f"custom:group-{index}" for index in range(20)]
    features = [{"id": f"custom:group-0.sample-{index}", "status": "planned", "surfaces": []} for index in range(100)]
    features[0]["notes"] = "x" * 2000
    spec = app_spec(types=custom_types, features=features)
    expected = deepcopy(spec)
    manifest = AppManifest.from_dict(manifest_data(app_spec=spec), expected_app_id="planner")

    spec["types"].clear()
    features[0]["surfaces"].append("ui")
    assert manifest.to_dict()["app_spec"] == expected
    result = manifest.to_dict()
    result["app_spec"]["features"].clear()
    assert manifest.to_dict()["app_spec"] == expected


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("APPS_DIR", str(tmp_path / "apps"))
    return AppManager()


def test_manager_create_update_clear_and_failed_update_preserve_artifacts(manager):
    spec = app_spec()
    manager.create_or_update_app("planner", "Planner", js="original", app_spec=spec)
    before = manager.get_app_files("planner")
    assert before["app_spec"] == spec
    assert manager.list_apps()[0]["app_spec"] == spec
    manifest_path = manager.app_path("planner") / "manifest.json"
    original = manifest_path.read_bytes()

    with pytest.raises(ManifestValidationError, match="app_spec"):
        manager.update_app_properties("planner", app_spec=app_spec(types=["tasks"]))
    assert manifest_path.read_bytes() == original
    assert manager.get_app_files("planner") == before

    updated_spec = app_spec(types=["tasks"], features=[])
    updated = manager.update_app_properties("planner", app_spec=updated_spec)
    assert updated["app_spec"] == updated_spec
    assert updated["grants_digest"] == before["grants_digest"]
    assert updated["schema_refs"] == before["schema_refs"]
    assert manager.get_app_files("planner")["js"] == "original"

    manager.create_or_update_app("planner", "Planner Updated", js="new")
    assert manager.get_app_files("planner")["app_spec"] == updated_spec
    cleared = manager.update_app_properties("planner", app_spec=None)
    assert "app_spec" not in cleared
    assert "app_spec" not in manager.get_app_files("planner")


def test_store_projects_generated_and_bound_ui_specs_without_layout_changes(manager, tmp_path):
    from backend.app_types import get_app_type_catalog

    service = AppStoreService(str(tmp_path), manager)
    manager.create_or_update_app("planner", "Planner", js="original", app_spec=app_spec())
    initial = service.get_state()
    assert initial["app_type_catalog"] == get_app_type_catalog()
    assert initial["items"][0]["app_spec"] == app_spec()

    manifest = CapabilityManifest.model_validate(
        {
            "id": "calendar",
            "kind": "mcp",
            "provider": "test",
            "title": "Calendar",
            "invocation": {"type": "agent_message", "app_id": "calendar"},
        }
    )
    catalog_id = service.register_capability(manifest)["catalog_id"]
    service.bind_ui(catalog_id, "planner")
    state = service.get_state()
    assert [item["catalog_id"] for item in state["items"]] == [catalog_id]
    assert state["items"][0]["app_spec"] == app_spec()
    layout_before = service.layout_path.read_bytes()
    manager.update_app_properties("planner", app_spec=app_spec(types=["tasks"], features=[]))
    current = service.get_state()
    assert current["items"][0]["app_spec"]["types"] == ["tasks"]
    assert current["root"] == state["root"]
    assert service.layout_path.read_bytes() == layout_before

    manager.update_app_properties("planner", app_spec=None)
    assert service.get_catalog_item(catalog_id).get("app_spec") is None
