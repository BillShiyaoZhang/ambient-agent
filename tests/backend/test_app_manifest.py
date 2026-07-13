import json

import pytest

from backend.app_manifest import (
    APP_MANIFEST_VERSION,
    AppManifest,
    ManifestValidationError,
)


def valid_manifest(**overrides):
    data = {
        "manifest_version": APP_MANIFEST_VERSION,
        "id": "morning-planner",
        "title": "Morning Planner",
        "description": "Helps organize daily priorities.",
        "app_version": "0.1.0",
        "intents": ["plan my morning"],
        "schema_refs": ["Task", "Event"],
    }
    data.update(overrides)
    return data


def test_valid_manifest_round_trips():
    manifest = AppManifest.from_dict(valid_manifest(), expected_app_id="morning-planner")

    assert manifest.to_dict() == valid_manifest()


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_manifest_version_must_be_supported_integer(version):
    with pytest.raises(ManifestValidationError, match="manifest_version"):
        AppManifest.from_dict(valid_manifest(manifest_version=version), expected_app_id="morning-planner")


@pytest.mark.parametrize(
    "app_id",
    ["Morning-Planner", "morning_planner", "-morning", "morning-", "a/b", "CON", "a" * 65],
)
def test_app_id_must_be_safe(app_id):
    with pytest.raises(ManifestValidationError, match="id"):
        AppManifest.from_dict(valid_manifest(id=app_id), expected_app_id=app_id)


def test_manifest_id_must_match_directory():
    with pytest.raises(ManifestValidationError, match="directory"):
        AppManifest.from_dict(valid_manifest(), expected_app_id="another-app")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("intents", "plan my morning"),
        ("intents", [""]),
        ("intents", ["plan", "plan"]),
        ("intents", [1]),
        ("schema_refs", "Task"),
        ("schema_refs", [""]),
        ("schema_refs", ["Task", "Task"]),
        ("schema_refs", [1]),
    ],
)
def test_list_fields_reject_wrong_types_empty_items_and_duplicates(field, value):
    with pytest.raises(ManifestValidationError, match=field):
        AppManifest.from_dict(valid_manifest(**{field: value}), expected_app_id="morning-planner")


def test_unknown_fields_are_rejected():
    with pytest.raises(ManifestValidationError, match="unknown"):
        AppManifest.from_dict(valid_manifest(capabilities=[]), expected_app_id="morning-planner")


@pytest.mark.parametrize("missing_field", valid_manifest())
def test_all_manifest_fields_are_required(missing_field):
    data = valid_manifest()
    del data[missing_field]

    with pytest.raises(ManifestValidationError, match="missing required"):
        AppManifest.from_dict(data, expected_app_id="morning-planner")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("title", " title"),
        ("title", "x" * 201),
        ("description", " description"),
        ("description", "x" * 2001),
        ("app_version", ""),
        ("app_version", "x" * 65),
        ("intents", ["x"] * 101),
        ("intents", ["x" * 201]),
        ("schema_refs", ["x"] * 101),
        ("schema_refs", ["x" * 201]),
    ],
)
def test_manifest_field_bounds_are_enforced(field, value):
    with pytest.raises(ManifestValidationError, match=field):
        AppManifest.from_dict(valid_manifest(**{field: value}), expected_app_id="morning-planner")


@pytest.mark.parametrize("app_id", ["aux", "nul", "prn", "com9", "lpt1", "clock$"])
def test_windows_reserved_app_ids_are_rejected(app_id):
    with pytest.raises(ManifestValidationError, match="id"):
        AppManifest.from_dict(valid_manifest(id=app_id), expected_app_id=app_id)


def test_invalid_json_is_reported(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="valid JSON"):
        AppManifest.read(path, expected_app_id="morning-planner")


def test_oversized_manifest_is_rejected_before_json_parsing(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_bytes(b" " * (64 * 1024 + 1))

    with pytest.raises(ManifestValidationError, match="maximum size"):
        AppManifest.read(path, expected_app_id="morning-planner")


def test_atomic_write_does_not_replace_existing_manifest_when_serialization_fails(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(valid_manifest()), encoding="utf-8")
    manifest = AppManifest.from_dict(valid_manifest(title="Updated"), expected_app_id="morning-planner")

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", fail_dump)

    with pytest.raises(OSError, match="disk full"):
        manifest.write_atomic(path)

    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Morning Planner"
    assert not list(tmp_path.glob(".manifest.json.*.tmp"))
