import os
from types import SimpleNamespace

import pytest

from backend.capabilities.files import AppFileError, AppFileGateway
from backend.app_manager import AppManager


def scoped_gateway(root, capabilities):
    manifest = SimpleNamespace(capabilities=capabilities, revision="revision", grants_digest="digest")
    manager = SimpleNamespace(app_path=lambda _app_id: root, get_manifest=lambda _app_id: manifest)
    return AppFileGateway(manager)


@pytest.mark.parametrize("operation", ["read", "write", "delete"])
def test_file_exact_grant_rejects_same_suffix_under_another_directory(tmp_path, operation):
    target = tmp_path / "data" / "private" / "exports" / "report.txt"
    target.parent.mkdir(parents=True)
    target.write_text("protected", encoding="utf-8")
    scope = {"paths": ["exports/report.txt"]}
    if operation == "write":
        scope["max_bytes"] = 100
    gateway = scoped_gateway(tmp_path, [{"id": f"file.{operation}", "scope": scope}])
    with pytest.raises(AppFileError, match="capability"):
        if operation == "write":
            gateway.write_text("audit", "private/exports/report.txt", "changed")
        elif operation == "read":
            gateway.read_text("audit", "private/exports/report.txt")
        else:
            gateway.delete("audit", "private/exports/report.txt")
    assert target.read_text(encoding="utf-8") == "protected"


def test_file_listing_filters_out_descendants_outside_single_level_read_grant(tmp_path):
    directory = tmp_path / "data" / "exports"
    (directory / "private").mkdir(parents=True)
    (directory / "report.txt").write_text("allowed", encoding="utf-8")
    (directory / "private" / "secret.txt").write_text("protected", encoding="utf-8")
    gateway = scoped_gateway(tmp_path, [{"id": "file.read", "scope": {"paths": ["exports/*"]}}])
    assert gateway.list_files("audit", "exports", manifest_revision="revision", grants_digest="digest") == [
        "exports/report.txt"
    ]
    with pytest.raises(AppFileError, match="stale"):
        gateway.list_files("audit", "exports", manifest_revision="old")


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


@pytest.mark.parametrize("operation", ["read_text", "delete"])
def test_missing_private_file_has_distinct_code_only_after_authorization(file_app, operation):
    from backend.main import _app_file_error

    _manager, gateway, _app_dir = file_app
    with pytest.raises(AppFileError) as missing:
        getattr(gateway, operation)("notes-app", "drafts/missing.md")
    assert missing.value.to_dict() == {"code": "file_not_found", "message": "App data file not found"}
    response = _app_file_error(missing.value)
    assert response.status_code == 404
    assert response.detail == missing.value.to_dict()

    with pytest.raises(AppFileError) as denied:
        getattr(gateway, operation)("notes-app", "private/missing.md")
    assert denied.value.code == "file_capability_denied"
    assert _app_file_error(denied.value).status_code == 403


@pytest.mark.asyncio
async def test_native_file_rpc_preserves_missing_file_code(file_app):
    from backend.widget_runtime import build_widget_runtime_rpc_response

    _manager, gateway, _app_dir = file_app
    binding = SimpleNamespace(app_id="notes-app")
    response = await build_widget_runtime_rpc_response(
        binding,
        {
            "type": "rpc_request",
            "request_id": "read-first-use",
            "method": "files.read",
            "params": {"path": "drafts/missing.md"},
        },
        lambda bound, _method, params: gateway.read_text(bound.app_id, params["path"]),
        include_session_id=False,
    )
    assert response["error"] == {"code": "file_not_found", "message": "App data file not found"}


@pytest.mark.parametrize("path", ["../manifest.json", "/etc/passwd", "drafts/../../controller.js", ""])
def test_app_file_gateway_rejects_path_escape(file_app, path):
    _manager, gateway, _app_dir = file_app
    with pytest.raises(AppFileError):
        gateway.read_text("notes-app", path)


def test_app_file_gateway_rejects_scope_size_and_symlinks(file_app, tmp_path, monkeypatch):
    _manager, gateway, app_dir = file_app
    with pytest.raises(AppFileError, match="capability"):
        gateway.write_text("notes-app", "settings.json", "x")
    with pytest.raises(AppFileError, match="64"):
        gateway.write_text("notes-app", "drafts/large.md", "x" * 65)

    data_root = app_dir / "data"
    data_root.mkdir(exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    if os.name == "nt":
        # Model the unsafe directory boundary without requiring Windows symlink privilege.
        linked = data_root / "drafts"
        linked.mkdir()
        original_is_symlink = type(linked).is_symlink
        monkeypatch.setattr(type(linked), "is_symlink", lambda path: path == linked or original_is_symlink(path))
    else:
        os.symlink(outside, data_root / "drafts")
    with pytest.raises(AppFileError, match="link"):
        gateway.write_text("notes-app", "drafts/escape.md", "x")
