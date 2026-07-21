import json
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
    js = "console.log('init');"

    # 1. Create app
    manager.create_or_update_app(app_id, title, js=js)

    # Verify directory and files exist
    app_dir = temp_apps_dir / app_id
    assert app_dir.exists()
    assert (app_dir / "controller.js").read_text() == js
    assert not (app_dir / "index.html").exists()
    assert not (app_dir / "style.css").exists()

    manifest = json.loads((app_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == app_id
    assert manifest["title"] == title
    assert manifest["manifest_version"] == 2
    assert manifest["description"] == ""
    assert manifest["intents"] == []
    assert manifest["schema_refs"] == []
    assert manifest["capabilities"] == []

    # 2. Get app files
    app_files = manager.get_app_files(app_id)
    assert app_files is not None
    assert app_files["id"] == app_id
    assert app_files["title"] == title
    assert app_files["js"] == js
    assert app_files["description"] == ""
    assert app_files["intents"] == []
    assert app_files["schema_refs"] == []
    assert app_files["capabilities"] == []


def test_list_apps(temp_apps_dir):
    manager = AppManager()

    manager.create_or_update_app("app1", "App One", js="console.log(1)")
    manager.create_or_update_app("app2", "App Two", js="console.log(2)")

    apps = manager.list_apps()
    assert len(apps) == 2
    ids = [a["id"] for a in apps]
    assert "app1" in ids
    assert "app2" in ids

    titles = [a["title"] for a in apps]
    assert "App One" in titles
    assert "App Two" in titles
    assert all(app["manifest_version"] == 2 for app in apps)
    assert all("created_at" in app and "updated_at" in app for app in apps)


def test_delete_app(temp_apps_dir):
    manager = AppManager()

    manager.create_or_update_app("app-to-delete", "Delete Me", js="console.log(3)")
    assert (temp_apps_dir / "app-to-delete").exists()

    # Delete
    success = manager.delete_app("app-to-delete")
    assert success is True
    assert not (temp_apps_dir / "app-to-delete").exists()

    # Try deleting non-existent
    success2 = manager.delete_app("app-to-delete")
    assert success2 is False
