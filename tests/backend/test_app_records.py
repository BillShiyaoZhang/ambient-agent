import sqlite3

import pytest

from backend.app_manager import AppManager
from backend.app_records import AppRecordStore


def test_compensation_attempts_all_actions_and_preserves_original_error(tmp_path, caplog):
    store = AppRecordStore(tmp_path)
    actions = []

    with pytest.raises(sqlite3.OperationalError, match="original transaction failure"):
        with store.serialized() as transaction:
            transaction.add_rollback(lambda: actions.append("last"))

            def fail_compensation():
                actions.append("failed")
                raise PermissionError("compensation failed")

            transaction.add_rollback(fail_compensation)
            transaction.add_rollback(lambda: actions.append("first"))
            raise sqlite3.OperationalError("original transaction failure")

    assert actions == ["first", "failed", "last"]
    assert "Filesystem transaction compensation failed" in caplog.text


def test_app_record_store_is_scoped_to_apps_root(tmp_path, monkeypatch):
    apps_dir = tmp_path / "custom-root" / "generated-apps"
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "another-workspace"))
    monkeypatch.setenv("APPS_DIR", str(apps_dir))

    manager = AppManager()

    assert manager._record_store.db_path == apps_dir / ".ambient" / "app_records.db"


def test_sibling_apps_roots_use_independent_record_databases(tmp_path, monkeypatch):
    first_apps_dir = tmp_path / "apps-a"
    second_apps_dir = tmp_path / "apps-b"

    monkeypatch.setenv("APPS_DIR", str(first_apps_dir))
    first = AppManager()
    monkeypatch.setenv("APPS_DIR", str(second_apps_dir))
    second = AppManager()

    assert first._record_store.db_path != second._record_store.db_path


def test_record_store_rejects_symlinked_state_directory(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    state_dir = tmp_path / "apps" / ".ambient"
    state_dir.parent.mkdir()
    try:
        state_dir.symlink_to(external, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are not available in this environment")

    with pytest.raises(OSError, match="real directory"):
        AppRecordStore(state_dir)

    assert not (external / "app_records.db").exists()


def test_record_store_rejects_symlinked_database_file(tmp_path):
    state_dir = tmp_path / ".ambient"
    state_dir.mkdir()
    external_db = tmp_path / "external.db"
    external_db.write_bytes(b"do not modify")
    db_path = state_dir / "app_records.db"
    try:
        db_path.symlink_to(external_db)
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are not available in this environment")

    with pytest.raises(OSError, match="regular file"):
        AppRecordStore(state_dir)

    assert external_db.read_bytes() == b"do not modify"
