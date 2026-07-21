import os

import pytest

from backend.capabilities.files import AppFileError, AppFileGateway
from backend.app_manager import AppManager


@pytest.fixture
def file_app(tmp_path, monkeypatch):
    apps = tmp_path / "apps"
    apps.mkdir()
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("APPS_DIR", str(apps))
    manager = AppManager()
    manager.create_or_update_app(
        "notes-app",
        "Notes",
        js="export default function App() { return null; }",
        capabilities=[
            {"id": "file.read", "scope": {"paths": ["drafts/**"]}},
            {"id": "file.write", "scope": {"paths": ["drafts/**"], "max_bytes": 64}},
            {"id": "file.delete", "scope": {"paths": ["drafts/**"]}},
        ],
    )
    return manager, AppFileGateway(manager), apps / "notes-app"


def test_app_file_gateway_round_trips_only_inside_private_data_root(file_app):
    _manager, gateway, app_dir = file_app

    gateway.write_text("notes-app", "drafts/today.md", "hello")
    assert gateway.read_text("notes-app", "drafts/today.md") == "hello"
    assert gateway.list_files("notes-app", "drafts") == ["drafts/today.md"]
    assert not any(path.name.endswith(".tmp") for path in (app_dir / "data" / "drafts").iterdir())

    gateway.delete("notes-app", "drafts/today.md")
    with pytest.raises(AppFileError, match="not found"):
        gateway.read_text("notes-app", "drafts/today.md")


@pytest.mark.parametrize("path", ["../manifest.json", "/etc/passwd", "drafts/../../controller.js", ""])
def test_app_file_gateway_rejects_path_escape(file_app, path):
    _manager, gateway, _app_dir = file_app
    with pytest.raises(AppFileError):
        gateway.read_text("notes-app", path)


def test_app_file_gateway_rejects_scope_size_and_symlinks(file_app, tmp_path):
    _manager, gateway, app_dir = file_app
    with pytest.raises(AppFileError, match="capability"):
        gateway.write_text("notes-app", "settings.json", "x")
    with pytest.raises(AppFileError, match="64"):
        gateway.write_text("notes-app", "drafts/large.md", "x" * 65)

    data_root = app_dir / "data"
    data_root.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, data_root / "drafts")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(AppFileError, match="link"):
        gateway.write_text("notes-app", "drafts/escape.md", "x")
