import json
import os
import sqlite3
import threading
from datetime import UTC, datetime

import pytest

from backend.app_manager import AppManager


@pytest.fixture
def temp_apps_dir(tmp_path, monkeypatch):
    # Set APPS_DIR to a temporary path for the duration of the test
    apps_path = tmp_path / "apps"
    apps_path.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("APPS_DIR", str(apps_path))
    return apps_path


def test_create_and_get_app(temp_apps_dir):
    manager = AppManager()

    app_id = "test-todo"
    title = "Test Todo App"
    html = "<div>Todo List</div>"
    css = "div { color: red; }"
    js = "console.log('init');"

    # 1. Create app
    manager.create_or_update_app(app_id, title, html, css, js)

    # Verify directory and files exist
    app_dir = temp_apps_dir / app_id
    assert app_dir.exists()
    assert (app_dir / "index.html").read_text() == html
    assert (app_dir / "style.css").read_text() == css
    assert (app_dir / "controller.js").read_text() == js

    manifest = json.loads((app_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == app_id
    assert manifest["title"] == title
    assert manifest["manifest_version"] == 1
    assert manifest["description"] == ""
    assert manifest["intents"] == []
    assert manifest["schema_refs"] == []
    assert not (app_dir / "metadata.json").exists()

    # 2. Get app files
    app_files = manager.get_app_files(app_id)
    assert app_files is not None
    assert app_files["id"] == app_id
    assert app_files["title"] == title
    assert app_files["html"] == html
    assert app_files["css"] == css
    assert app_files["js"] == js
    assert app_files["description"] == ""
    assert app_files["intents"] == []
    assert app_files["schema_refs"] == []


def test_create_and_get_a2ui_app(temp_apps_dir):
    manager = AppManager()

    app_id = "test-a2ui-todo"
    title = "Test A2UI Todo App"
    layout = '[{"id": "root", "type": "Column"}]'
    js = "ambient.state.set('/title', 'Tasks');"

    # 1. Create app
    manager.create_or_update_app(app_id, title, js=js, layout=layout)

    # Verify directory and files exist
    app_dir = temp_apps_dir / app_id
    assert app_dir.exists()
    assert (app_dir / "layout.json").read_text() == layout
    assert (app_dir / "controller.js").read_text() == js
    assert not (app_dir / "index.html").exists()
    assert not (app_dir / "style.css").exists()

    # 2. Get app files
    app_files = manager.get_app_files(app_id)
    assert app_files is not None
    assert app_files["id"] == app_id
    assert app_files["title"] == title
    assert app_files["layout"] == layout
    assert app_files["js"] == js


def test_create_and_get_react_app(temp_apps_dir):
    manager = AppManager()

    app_id = "test-react-todo"
    title = "Test React Todo App"
    jsx = "export default function Widget() { return <div>React</div>; }"
    js = "export function useController() { return {}; }"

    # 1. Create app
    manager.create_or_update_app(app_id, title, js=js, jsx=jsx)

    # Verify directory and files exist
    app_dir = temp_apps_dir / app_id
    assert app_dir.exists()
    assert (app_dir / "index.jsx").read_text() == jsx
    assert (app_dir / "controller.js").read_text() == js
    assert not (app_dir / "layout.json").exists()
    assert not (app_dir / "index.html").exists()
    assert not (app_dir / "style.css").exists()

    # 2. Get app files
    app_files = manager.get_app_files(app_id)
    assert app_files is not None
    assert app_files["id"] == app_id
    assert app_files["title"] == title
    assert app_files["jsx"] == jsx
    assert app_files["js"] == js


def test_list_apps(temp_apps_dir):
    manager = AppManager()

    manager.create_or_update_app("app1", "App One", "<h1>1</h1>", "", "")
    manager.create_or_update_app("app2", "App Two", "<h1>2</h1>", "", "")

    apps = manager.list_apps()
    assert len(apps) == 2
    ids = [a["id"] for a in apps]
    assert "app1" in ids
    assert "app2" in ids

    titles = [a["title"] for a in apps]
    assert "App One" in titles
    assert "App Two" in titles
    assert all(app["manifest_version"] == 1 for app in apps)
    assert all("created_at" in app and "updated_at" in app for app in apps)


def test_delete_app(temp_apps_dir):
    manager = AppManager()

    manager.create_or_update_app("app-to-delete", "Delete Me", "<div></div>", "", "")
    assert (temp_apps_dir / "app-to-delete").exists()

    # Delete
    success = manager.delete_app("app-to-delete")
    assert success is True
    assert not (temp_apps_dir / "app-to-delete").exists()

    # Try deleting non-existent
    success = manager.delete_app("non-existent")
    assert success is False


def test_auto_heal_missing_manifest(temp_apps_dir):
    manager = AppManager()

    app_id = "manual-weather"
    app_dir = temp_apps_dir / app_id
    app_dir.mkdir()

    # Write only source files, without a manifest.
    html_content = "<html><head><title>My Awesome Weather</title></head><body></body></html>"
    (app_dir / "index.html").write_text(html_content, encoding="utf-8")
    (app_dir / "style.css").write_text("body {}", encoding="utf-8")
    (app_dir / "controller.js").write_text("console.log('weather')", encoding="utf-8")

    assert not (app_dir / "manifest.json").exists()

    # get_app_files should heal it and return the correct dict
    app_files = manager.get_app_files(app_id)
    assert app_files is not None
    assert app_files["id"] == app_id
    assert app_files["title"] == "My Awesome Weather"
    assert app_files["html"] == html_content

    assert (app_dir / "manifest.json").exists()

    (app_dir / "manifest.json").unlink()
    assert not (app_dir / "manifest.json").exists()

    apps = manager.list_apps()
    # Find manual-weather in apps
    weather_app = next((a for a in apps if a["id"] == app_id), None)
    assert weather_app is not None
    assert weather_app["title"] == "My Awesome Weather"
    assert (app_dir / "manifest.json").exists()


def test_source_update_preserves_manifest_declarations(temp_apps_dir):
    manager = AppManager()
    manager.create_or_update_app("planner", "Planner", "<h1>one</h1>", "", "")
    manifest_path = temp_apps_dir / "planner" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "description": "Plans the day.",
            "app_version": "2.3.4",
            "intents": ["plan my day"],
            "schema_refs": ["Task", "Event"],
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    manager.create_or_update_app("planner", "Updated Planner", "<h1>two</h1>", "body {}", "init()")

    updated = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated["title"] == "Updated Planner"
    assert updated["description"] == "Plans the day."
    assert updated["app_version"] == "2.3.4"
    assert updated["intents"] == ["plan my day"]
    assert updated["schema_refs"] == ["Task", "Event"]


def test_metadata_is_migrated_once_and_removed_after_verified_write(temp_apps_dir):
    app_dir = temp_apps_dir / "legacy"
    app_dir.mkdir()
    (app_dir / "metadata.json").write_text(
        json.dumps(
            {
                "id": "legacy",
                "title": "Legacy",
                "created_at": "2025-01-02T03:04:05+00:00",
                "updated_at": "2025-02-03T04:05:06+00:00",
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "index.html").write_text("<h1>Legacy</h1>", encoding="utf-8")
    (app_dir / "style.css").write_text("", encoding="utf-8")
    (app_dir / "controller.js").write_text("", encoding="utf-8")

    apps = AppManager().list_apps()

    assert [app["id"] for app in apps] == ["legacy"]
    assert apps[0]["created_at"] == "2025-01-02T03:04:05+00:00"
    assert apps[0]["updated_at"] == "2025-02-03T04:05:06+00:00"
    assert (app_dir / "manifest.json").exists()
    assert not (app_dir / "metadata.json").exists()


def test_invalid_manifest_is_not_repaired_or_hidden_by_metadata(temp_apps_dir, caplog):
    valid_dir = temp_apps_dir / "valid"
    invalid_dir = temp_apps_dir / "invalid"
    valid_dir.mkdir()
    invalid_dir.mkdir()
    for app_dir in (valid_dir, invalid_dir):
        (app_dir / "index.html").write_text("<h1>App</h1>", encoding="utf-8")
        (app_dir / "style.css").write_text("", encoding="utf-8")
        (app_dir / "controller.js").write_text("", encoding="utf-8")
    AppManager().create_or_update_app("valid", "Valid", "<h1>App</h1>", "", "")
    (invalid_dir / "manifest.json").write_text("{", encoding="utf-8")
    (invalid_dir / "metadata.json").write_text(json.dumps({"id": "invalid", "title": "Legacy"}), encoding="utf-8")

    apps = AppManager().list_apps()

    assert [app["id"] for app in apps] == ["valid"]
    assert (invalid_dir / "metadata.json").exists()
    assert "invalid" in caplog.text


def test_listing_is_deterministic(temp_apps_dir):
    manager = AppManager()
    manager.create_or_update_app("z-last", "Z", "", "", "")
    manager.create_or_update_app("a-first", "A", "", "", "")

    assert [app["id"] for app in manager.list_apps()] == ["a-first", "z-last"]


def test_path_traversal_ids_are_rejected(temp_apps_dir):
    manager = AppManager()

    with pytest.raises(ValueError, match="app_id"):
        manager.create_or_update_app("../outside", "Unsafe", "", "", "")

    assert not (temp_apps_dir.parent / "outside").exists()


def test_write_failure_is_not_reported_as_success(temp_apps_dir, monkeypatch):
    manager = AppManager()

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("backend.app_manager.AppManifest.write_atomic", fail_write)

    with pytest.raises(OSError, match="disk full"):
        manager.create_or_update_app("write-fails", "Failure", "", "", "")

    assert not (temp_apps_dir / "write-fails").exists()
    assert manager.list_apps() == []


def test_validation_failure_does_not_leave_new_app_directory(temp_apps_dir):
    manager = AppManager()

    with pytest.raises(ValueError, match="title"):
        manager.create_or_update_app("invalid-title", "", "", "", "")

    assert not (temp_apps_dir / "invalid-title").exists()


def test_timestamps_are_utc_iso_8601_strings(temp_apps_dir):
    manager = AppManager()
    manager.create_or_update_app("clock", "Clock", "", "", "")

    app = manager.list_apps()[0]

    assert datetime.fromisoformat(app["created_at"]).tzinfo == UTC
    assert datetime.fromisoformat(app["updated_at"]).tzinfo == UTC


def test_missing_lifecycle_record_is_initialized_with_observable_discovery_semantics(temp_apps_dir, caplog):
    manager = AppManager()
    manager.create_or_update_app("clock", "Clock", "", "", "")
    manager._record_store.db_path.unlink()

    repaired_manager = AppManager()
    app = repaired_manager.get_app_files("clock")

    assert app["created_at"] == app["updated_at"]
    assert "Initializing missing lifecycle record" in caplog.text


def test_listing_does_not_follow_app_directory_symlinks(temp_apps_dir, tmp_path):
    external_app = tmp_path / "external-app"
    external_app.mkdir()
    (external_app / "index.html").write_text("<h1>External</h1>", encoding="utf-8")
    link = temp_apps_dir / "linked-app"
    try:
        os.symlink(external_app, link, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are not available in this environment")

    assert AppManager().list_apps() == []
    assert not (external_app / "manifest.json").exists()


def test_concurrent_manifest_migration_produces_one_valid_result(temp_apps_dir):
    app_dir = temp_apps_dir / "legacy"
    app_dir.mkdir()
    (app_dir / "metadata.json").write_text(
        json.dumps(
            {
                "id": "legacy",
                "title": "Legacy",
                "created_at": "2025-01-02T03:04:05+00:00",
                "updated_at": "2025-02-03T04:05:06+00:00",
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "index.html").write_text("<h1>Legacy</h1>", encoding="utf-8")
    (app_dir / "style.css").write_text("", encoding="utf-8")
    (app_dir / "controller.js").write_text("", encoding="utf-8")
    barrier = threading.Barrier(3)
    results = []
    errors = []

    def migrate():
        try:
            manager = AppManager()
            barrier.wait(timeout=5)
            results.append(manager.get_app_files("legacy"))
        except Exception as exc:
            errors.append(exc)
            barrier.abort()

    threads = [threading.Thread(target=migrate) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert [result["id"] for result in results] == ["legacy", "legacy"]
    assert json.loads((app_dir / "manifest.json").read_text(encoding="utf-8"))["title"] == "Legacy"
    assert not (app_dir / "metadata.json").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        "{",
        json.dumps([]),
        json.dumps(
            {
                "id": "another-app",
                "title": "Legacy",
                "created_at": "2025-01-02T03:04:05+00:00",
                "updated_at": "2025-02-03T04:05:06+00:00",
            }
        ),
    ],
)
def test_invalid_legacy_metadata_is_isolated_without_deletion(temp_apps_dir, metadata):
    app_dir = temp_apps_dir / "legacy"
    app_dir.mkdir()
    metadata_path = app_dir / "metadata.json"
    metadata_path.write_text(metadata, encoding="utf-8")
    (app_dir / "index.html").write_text("<h1>Legacy</h1>", encoding="utf-8")

    assert AppManager().list_apps() == []
    assert metadata_path.exists()
    assert not (app_dir / "manifest.json").exists()


def test_post_commit_metadata_cleanup_failure_keeps_migrated_app_valid(temp_apps_dir, monkeypatch, caplog):
    app_dir = temp_apps_dir / "legacy"
    app_dir.mkdir()
    metadata_path = app_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "id": "legacy",
                "title": "Legacy",
                "created_at": "2025-01-02T03:04:05+00:00",
                "updated_at": "2025-02-03T04:05:06+00:00",
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "index.html").write_text("<h1>Legacy</h1>", encoding="utf-8")

    original_unlink = type(metadata_path).unlink

    def fail_delete(path, *args, **kwargs):
        if path == metadata_path:
            raise PermissionError("metadata is busy")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.unlink", fail_delete)

    apps = AppManager().list_apps()

    assert [app["id"] for app in apps] == ["legacy"]
    assert metadata_path.exists()
    assert (app_dir / "manifest.json").exists()
    assert "Post-commit filesystem cleanup failed" in caplog.text


def test_interrupted_migration_recovers_timestamps_without_overwriting_manifest(temp_apps_dir):
    app_dir = temp_apps_dir / "legacy"
    app_dir.mkdir()
    metadata_path = app_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "id": "legacy",
                "title": "Old Metadata Title",
                "created_at": "2025-01-02T03:04:05+00:00",
                "updated_at": "2025-02-03T04:05:06+00:00",
            }
        ),
        encoding="utf-8",
    )
    manifest = AppManager._new_manifest("legacy", "Manifest Title")
    manifest.write_atomic(app_dir / "manifest.json")
    (app_dir / "index.html").write_text("<h1>Legacy</h1>", encoding="utf-8")

    app = AppManager().get_app_files("legacy")

    assert app["title"] == "Manifest Title"
    assert app["created_at"] == "2025-01-02T03:04:05+00:00"
    assert app["updated_at"] == "2025-02-03T04:05:06+00:00"
    assert not metadata_path.exists()


def test_metadata_residue_is_removed_without_changing_manifest_declarations(temp_apps_dir):
    manager = AppManager()
    manager.create_or_update_app(
        "planner",
        "Manifest Title",
        "",
        "",
        "",
        description="Manifest description",
        intents=["plan my day"],
        schema_refs=["Task"],
    )
    app_dir = temp_apps_dir / "planner"
    metadata_path = app_dir / "metadata.json"
    metadata_path.write_text("{not valid metadata", encoding="utf-8")

    app = manager.get_app_files("planner")

    assert app["title"] == "Manifest Title"
    assert app["description"] == "Manifest description"
    assert app["intents"] == ["plan my day"]
    assert app["schema_refs"] == ["Task"]
    assert not metadata_path.exists()


def test_explicit_update_recovers_created_at_after_interrupted_migration(temp_apps_dir):
    app_dir = temp_apps_dir / "legacy"
    app_dir.mkdir()
    metadata_path = app_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "id": "legacy",
                "title": "Old Metadata Title",
                "created_at": "2025-01-02T03:04:05+00:00",
                "updated_at": "2025-02-03T04:05:06+00:00",
            }
        ),
        encoding="utf-8",
    )
    manifest = AppManager._new_manifest("legacy", "Manifest Title")
    manifest_data = manifest.to_dict()
    manifest_data["description"] = "Manifest remains authoritative."
    manifest_data["intents"] = ["keep this declaration"]
    manifest = type(manifest).from_dict(manifest_data, expected_app_id="legacy")
    manifest.write_atomic(app_dir / "manifest.json")

    manager = AppManager()
    manager.create_or_update_app("legacy", "Updated Title", "", "", "")
    app = manager.get_app_files("legacy")

    assert app["title"] == "Updated Title"
    assert app["description"] == "Manifest remains authoritative."
    assert app["intents"] == ["keep this declaration"]
    assert app["created_at"] == "2025-01-02T03:04:05+00:00"
    assert datetime.fromisoformat(app["updated_at"]) > datetime.fromisoformat("2025-02-03T04:05:06+00:00")
    assert not metadata_path.exists()


def test_interrupted_delete_with_record_restores_app(temp_apps_dir):
    manager = AppManager()
    manager.create_or_update_app("stable", "Stable", "", "", "")
    app_path = temp_apps_dir / "stable"
    tombstone = temp_apps_dir / f".stable.deleting-{'a' * 32}"
    app_path.replace(tombstone)

    app = AppManager().get_app_files("stable")

    assert app["id"] == "stable"
    assert app_path.exists()
    assert not tombstone.exists()


def test_committed_delete_tombstone_is_cleaned_on_next_operation(temp_apps_dir):
    manager = AppManager()
    tombstone = temp_apps_dir / f".removed.deleting-{'b' * 32}"
    tombstone.mkdir()
    (tombstone / "manifest.json").write_text("{}", encoding="utf-8")

    assert manager.list_apps() == []
    assert not tombstone.exists()


def test_ambiguous_delete_recovery_preserves_canonical_and_tombstone(temp_apps_dir, caplog):
    manager = AppManager()
    manager.create_or_update_app("stable", "Stable", "", "", "")
    tombstone = temp_apps_dir / f".stable.deleting-{'c' * 32}"
    tombstone.mkdir()
    (tombstone / "marker").write_text("do not remove", encoding="utf-8")

    app = manager.get_app_files("stable")

    assert app["id"] == "stable"
    assert tombstone.exists()
    assert (tombstone / "marker").exists()
    assert "canonical path and tombstone both exist" in caplog.text


def test_unrecognized_hidden_directory_is_not_treated_as_delete_tombstone(temp_apps_dir):
    hidden = temp_apps_dir / ".stable.deleting-not-a-uuid"
    hidden.mkdir()

    assert AppManager().list_apps() == []
    assert hidden.exists()


def test_tombstone_shaped_file_is_not_restored_or_removed(temp_apps_dir, caplog):
    suspicious = temp_apps_dir / f".stable.deleting-{'d' * 32}"
    suspicious.write_text("not a platform tombstone", encoding="utf-8")

    assert AppManager().list_apps() == []
    assert suspicious.exists()
    assert "Ignoring unsafe pending-deletion path" in caplog.text


def test_failed_update_restores_existing_app(temp_apps_dir, monkeypatch):
    manager = AppManager()
    manager.create_or_update_app("stable", "Stable", "old html", "old css", "old js")
    app_dir = temp_apps_dir / "stable"
    before = {path.name: path.read_bytes() for path in app_dir.iterdir()}

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("backend.app_manager.AppManifest.write_atomic", fail_write)

    with pytest.raises(OSError, match="disk full"):
        manager.create_or_update_app("stable", "Changed", "new html", "new css", "new js")

    assert {path.name: path.read_bytes() for path in app_dir.iterdir()} == before


def test_commit_failure_restores_existing_app_and_record(temp_apps_dir, monkeypatch):
    manager = AppManager()
    manager.create_or_update_app("stable", "Stable", "old html", "old css", "old js")
    app_dir = temp_apps_dir / "stable"
    before_files = {path.name: path.read_bytes() for path in app_dir.iterdir()}
    before_record = manager.list_apps()[0]
    real_connect = manager._record_store._connect

    class CommitFailingConnection:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def commit(self):
            raise sqlite3.OperationalError("simulated commit failure")

    monkeypatch.setattr(
        manager._record_store,
        "_connect",
        lambda: CommitFailingConnection(real_connect()),
    )

    with pytest.raises(sqlite3.OperationalError, match="simulated commit failure"):
        manager.create_or_update_app("stable", "Changed", "new html", "new css", "new js")

    monkeypatch.setattr(manager._record_store, "_connect", real_connect)
    assert {path.name: path.read_bytes() for path in app_dir.iterdir()} == before_files
    assert manager.list_apps()[0] == before_record


def test_commit_failure_restores_deleted_app(temp_apps_dir, monkeypatch):
    manager = AppManager()
    manager.create_or_update_app("stable", "Stable", "old html", "old css", "old js")
    app_dir = temp_apps_dir / "stable"
    before_files = {path.name: path.read_bytes() for path in app_dir.iterdir()}
    real_connect = manager._record_store._connect

    class CommitFailingConnection:
        def __init__(self, connection):
            self._connection = connection

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def commit(self):
            raise sqlite3.OperationalError("simulated commit failure")

    monkeypatch.setattr(
        manager._record_store,
        "_connect",
        lambda: CommitFailingConnection(real_connect()),
    )

    with pytest.raises(sqlite3.OperationalError, match="simulated commit failure"):
        manager.delete_app("stable")

    monkeypatch.setattr(manager._record_store, "_connect", real_connect)
    assert {path.name: path.read_bytes() for path in app_dir.iterdir()} == before_files
    assert manager.get_app_files("stable")["id"] == "stable"


def test_post_commit_delete_cleanup_failure_does_not_reverse_success(temp_apps_dir, monkeypatch, caplog):
    manager = AppManager()
    manager.create_or_update_app("stable", "Stable", "", "", "")
    real_rmtree = __import__("shutil").rmtree

    def fail_tombstone_cleanup(path, *args, **kwargs):
        if ".deleting-" in str(path):
            raise PermissionError("simulated cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("backend.app_manager.shutil.rmtree", fail_tombstone_cleanup)

    assert manager.delete_app("stable") is True
    assert manager.get_app_files("stable") is None
    assert manager.list_apps() == []
    assert "Post-commit filesystem cleanup failed" in caplog.text


def test_explicit_legacy_update_preserves_created_at_and_refreshes_updated_at(temp_apps_dir):
    app_dir = temp_apps_dir / "legacy"
    app_dir.mkdir()
    (app_dir / "metadata.json").write_text(
        json.dumps(
            {
                "id": "legacy",
                "title": "Legacy",
                "created_at": "2025-01-02T03:04:05+00:00",
                "updated_at": "2025-02-03T04:05:06+00:00",
            }
        ),
        encoding="utf-8",
    )

    manager = AppManager()
    manager.create_or_update_app("legacy", "Updated", "", "", "")
    app = manager.get_app_files("legacy")

    assert app["created_at"] == "2025-01-02T03:04:05+00:00"
    assert datetime.fromisoformat(app["updated_at"]) > datetime.fromisoformat("2025-02-03T04:05:06+00:00")
    assert not (app_dir / "metadata.json").exists()


def test_explicit_manifest_replacements_distinguish_omitted_and_empty(temp_apps_dir):
    manager = AppManager()
    manager.create_or_update_app(
        "planner",
        "Planner",
        "",
        "",
        "",
        description="Plans a day.",
        app_version="1.2.3",
        intents=["plan my day"],
        schema_refs=["Task"],
    )
    manager.create_or_update_app("planner", "Planner", "", "", "", intents=[], schema_refs=[])

    app = manager.get_app_files("planner")

    assert app["description"] == "Plans a day."
    assert app["app_version"] == "1.2.3"
    assert app["intents"] == []
    assert app["schema_refs"] == []
