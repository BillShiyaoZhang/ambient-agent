import os
import shutil
import pytest
import json
from backend.app_manager import AppManager

@pytest.fixture
def temp_apps_dir(tmp_path, monkeypatch):
    # Set APPS_DIR to a temporary path for the duration of the test
    apps_path = tmp_path / "apps"
    apps_path.mkdir()
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
    
    metadata = json.loads((app_dir / "metadata.json").read_text())
    assert metadata["id"] == app_id
    assert metadata["title"] == title
    
    # data.json should be initialized as empty dict {}
    assert (app_dir / "data.json").read_text() == "{}"

    # 2. Get app files
    app_files = manager.get_app_files(app_id)
    assert app_files is not None
    assert app_files["id"] == app_id
    assert app_files["title"] == title
    assert app_files["html"] == html
    assert app_files["css"] == css
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

def test_get_and_save_data(temp_apps_dir):
    manager = AppManager()
    
    manager.create_or_update_app("data-app", "Data App", "<div></div>", "", "")
    
    # Default data
    data = manager.get_app_data("data-app")
    assert data == {}
    
    # Save new data
    new_data = {"tasks": ["task1", "task2"], "settings": {"theme": "dark"}}
    manager.save_app_data("data-app", new_data)
    
    # Read back
    retrieved = manager.get_app_data("data-app")
    assert retrieved == new_data
    
    # Non-existent app data should return empty dict
    assert manager.get_app_data("non-existent") == {}
