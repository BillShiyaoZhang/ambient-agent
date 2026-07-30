import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from backend.models import ChatMessage, ChatSession, LLMAuditLog
from backend.workspace_storage import (
    WorkspaceMigrationError,
    WorkspaceStorage,
    WorkspaceStorageCorruptionError,
    migrate_old_data,
)


def test_workspace_storage_crud(tmp_path):
    workspace_dir = str(tmp_path / "workspace")
    storage = WorkspaceStorage(workspace_dir)

    # 1. Test Session Creation
    session_id = "test-sess-1"
    session_obj = ChatSession(id=session_id, title="Test Chat Session")
    storage.add(session_obj)
    storage.commit()

    # Check that file was created
    session_file = os.path.join(workspace_dir, "sessions", f"{session_id}.json")
    assert os.path.exists(session_file)
    with open(session_file, encoding="utf-8") as f:
        data = json.load(f)
        assert data["id"] == session_id
        assert data["title"] == "Test Chat Session"

    # Get session back
    retrieved_session = storage.get(ChatSession, session_id)
    assert retrieved_session is not None
    assert retrieved_session.id == session_id
    assert retrieved_session.title == "Test Chat Session"

    # 2. Test Message Creation & Retrieval
    msg1 = ChatMessage(session_id=session_id, role="user", sender="user", content="Hello!")
    storage.add(msg1)
    storage.commit()
    storage.refresh(msg1)

    assert msg1.id == 1

    msg2 = ChatMessage(session_id=session_id, role="agent", sender="agent", content="Hi there!")
    storage.add(msg2)
    storage.commit()
    storage.refresh(msg2)

    assert msg2.id == 2

    # Retrieve messages
    messages = storage.get_messages(session_id)
    assert len(messages) == 2
    assert messages[0].content == "Hello!"
    assert messages[1].content == "Hi there!"

    # List sessions
    sessions = storage.get_sessions()
    assert len(sessions) == 1
    assert sessions[0].id == session_id

    # 3. Test Audit Log
    log = LLMAuditLog(provider="ollama", model="llama3", prompt="Hi", response="Hello")
    storage.add(log)
    storage.commit()

    logs = storage.get_audit_logs()
    assert len(logs) == 1
    assert logs[0].provider == "ollama"
    assert logs[0].model == "llama3"
    assert logs[0].prompt == "Hi"
    assert logs[0].response == "Hello"

    # 4. Test Canvas Config
    config = {"pinned_ids": ["app1", "app2"], "widget_spans": {"app1": {"cols": 2, "rows": 2}}}
    storage.save_canvas_config(config)

    canvas_data = storage.get_canvas_config()
    assert canvas_data["version"] == 3
    assert canvas_data["open_app_ids"] == ["app1", "app2"]
    assert canvas_data["active_app_id"] == "app2"
    assert canvas_data["windows"]["app1"]["mode"] == "floating"

    # 5. Delete Session
    success = storage.delete_session(session_id)
    assert success is True
    assert not os.path.exists(session_file)
    assert len(storage.get_sessions()) == 0


def test_session_ids_cannot_escape_the_sessions_directory(tmp_path):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    outside = workspace / "escaped.json"

    storage.add(ChatSession(id="../escaped", title="Unsafe"))

    with pytest.raises(ValueError, match="path separators"):
        storage.commit()

    assert not outside.exists()


def test_concurrent_storage_instances_do_not_drop_messages(tmp_path):
    workspace = tmp_path / "workspace"
    bootstrap = WorkspaceStorage(str(workspace))
    bootstrap.add(ChatSession(id="parallel", title="Parallel"))
    bootstrap.commit()

    worker_count = 16
    ready = Barrier(worker_count)

    def persist(index: int) -> None:
        storage = WorkspaceStorage(str(workspace))
        storage.add(ChatMessage(session_id="parallel", content=f"message-{index}"))
        ready.wait()
        storage.commit()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(persist, range(worker_count)))

    messages = WorkspaceStorage(str(workspace)).get_messages("parallel")
    assert len(messages) == worker_count
    assert {message.content for message in messages} == {f"message-{index}" for index in range(worker_count)}
    assert len({message.id for message in messages}) == worker_count


def test_failed_atomic_session_replace_preserves_previous_file(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    session = ChatSession(id="atomic", title="Before")
    storage.add(session)
    storage.commit()
    session_file = workspace / "sessions" / "atomic.json"
    original = session_file.read_bytes()

    session.title = "After"
    storage.add(session)

    def fail_replace(_source, _destination):
        raise OSError("simulated interruption")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        storage.commit()

    assert session_file.read_bytes() == original
    assert json.loads(session_file.read_text(encoding="utf-8"))["title"] == "Before"


def test_failed_session_delete_is_reported_and_preserves_file(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    storage.add(ChatSession(id="keep-on-error", title="Keep"))
    storage.commit()
    session_file = workspace / "sessions" / "keep-on-error.json"

    def fail_remove(_path):
        raise OSError("simulated delete failure")

    monkeypatch.setattr(os, "remove", fail_remove)
    with pytest.raises(OSError, match="delete failure"):
        storage.delete_session("keep-on-error")
    assert session_file.exists()


def test_corrupt_session_is_not_silently_overwritten(tmp_path):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    session_file = workspace / "sessions" / "corrupt.json"
    session_file.write_text('{"id":"corrupt","messages":[', encoding="utf-8")

    assert storage.get(ChatSession, "corrupt") is None
    storage.add(ChatSession(id="corrupt", title="Replacement"))

    with pytest.raises(WorkspaceStorageCorruptionError):
        storage.commit()

    assert session_file.read_text(encoding="utf-8") == '{"id":"corrupt","messages":['


def test_corrupt_canvas_is_not_silently_reset(tmp_path):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    canvas_file = workspace / "canvas.json"
    original = '{"version":3,"open_app_ids":['
    canvas_file.write_text(original, encoding="utf-8")

    with pytest.raises(WorkspaceStorageCorruptionError):
        storage.get_canvas_config()
    with pytest.raises(WorkspaceStorageCorruptionError):
        storage.save_canvas_config({"version": 3, "open_app_ids": []})

    assert canvas_file.read_text(encoding="utf-8") == original


def test_commit_retry_does_not_duplicate_already_persisted_audit_log(tmp_path, monkeypatch):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    first = LLMAuditLog(provider="test", model="first", prompt="one", response="one")
    second = LLMAuditLog(provider="test", model="second", prompt="two", response="two")
    storage.add(first)
    storage.add(second)
    original_save = storage._save_audit_log
    calls = 0

    def fail_second(log):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second write failure")
        original_save(log)

    monkeypatch.setattr(storage, "_save_audit_log", fail_second)
    with pytest.raises(OSError, match="second write"):
        storage.commit()

    monkeypatch.setattr(storage, "_save_audit_log", original_save)
    storage.commit()

    assert [log.model for log in storage.get_audit_logs()] == ["second", "first"]


def test_audit_append_is_readable_after_a_crash_truncated_tail(tmp_path):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    audit_file = workspace / "audit_logs.jsonl"
    audit_file.write_text('{"id":1,"provider":"broken"', encoding="utf-8")

    storage.add(LLMAuditLog(provider="test", model="after-crash", prompt="hello", response="world"))
    storage.commit()

    assert [log.model for log in storage.get_audit_logs()] == ["after-crash"]
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"id":1,"provider":"broken"'
    assert json.loads(lines[1])["model"] == "after-crash"


def test_audit_ids_remain_unique_within_one_clock_tick(tmp_path, monkeypatch):
    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    monkeypatch.setattr("backend.workspace_storage.time.time_ns", lambda: 1_800_000_000_000_000_000)
    logs = [
        LLMAuditLog(provider="test", model=f"model-{index}", prompt="hello", response="world") for index in range(8)
    ]
    for log in logs:
        storage.add(log)
    storage.commit()

    ids = [log.id for log in logs]
    assert len(set(ids)) == len(ids)
    assert all(identifier is not None and identifier < 2**53 for identifier in ids)


def test_delete_session_audit_logs_filters_session_and_run_without_dropping_corrupt_lines(tmp_path):
    workspace = tmp_path / "workspace"
    storage = WorkspaceStorage(str(workspace))
    audit_file = workspace / "audit_logs.jsonl"
    audit_file.write_text(
        "\n".join(
            [
                json.dumps({"id": 1, "session_id": "delete-me", "run_id": "run-1"}),
                json.dumps({"id": 2, "session_id": "keep-me", "run_id": "run-2"}),
                json.dumps({"id": 3, "session_id": None, "run_id": "run-child"}),
                '{"id":4,"session_id":',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert storage.delete_audit_logs_for_session("delete-me", run_ids={"run-1", "run-child"}) == 2
    remaining = audit_file.read_text(encoding="utf-8")
    assert '"id": 2' in remaining
    assert '"id":4,"session_id":' in remaining
    assert '"id": 1' not in remaining
    assert '"id": 3' not in remaining


def test_legacy_app_migration_preserves_existing_workspace_copy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy_app = tmp_path / "backend" / "apps" / "same-app"
    current_app = tmp_path / "workspace" / "apps" / "same-app"
    legacy_app.mkdir(parents=True)
    current_app.mkdir(parents=True)
    (legacy_app / "controller.js").write_text("legacy", encoding="utf-8")
    (current_app / "controller.js").write_text("current", encoding="utf-8")

    migrate_old_data("workspace")

    assert (current_app / "controller.js").read_text(encoding="utf-8") == "current"
    assert (tmp_path / "backend" / "apps.backup" / "same-app" / "controller.js").read_text(encoding="utf-8") == "legacy"


def test_legacy_app_migration_never_dereferences_nested_symlinks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy_apps = tmp_path / "backend" / "apps"
    outside_file = tmp_path / "outside-secret.txt"
    outside_file.write_text("do not copy", encoding="utf-8")
    outside_dir = tmp_path / "outside-directory"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("do not copy directory", encoding="utf-8")
    file_link_app = legacy_apps / "file-link-app" / "nested"
    directory_link_app = legacy_apps / "directory-link-app" / "nested"
    safe_app = legacy_apps / "safe-app"
    file_link_app.mkdir(parents=True)
    directory_link_app.mkdir(parents=True)
    safe_app.mkdir(parents=True)
    try:
        (file_link_app / "secret.txt").symlink_to(outside_file)
        (directory_link_app / "external").symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")
    (safe_app / "controller.js").write_text("safe", encoding="utf-8")

    migrate_old_data("workspace")

    migrated_apps = tmp_path / "workspace" / "apps"
    assert not (migrated_apps / "file-link-app").exists()
    assert not (migrated_apps / "directory-link-app").exists()
    assert (migrated_apps / "safe-app" / "controller.js").read_text(encoding="utf-8") == "safe"
    assert (tmp_path / "backend" / "apps.backup" / "file-link-app" / "nested" / "secret.txt").is_symlink()
    assert (tmp_path / "backend" / "apps.backup" / "directory-link-app" / "nested" / "external").is_symlink()
    assert outside_file.read_text(encoding="utf-8") == "do not copy"
    assert (outside_dir / "secret.txt").read_text(encoding="utf-8") == "do not copy directory"


def test_legacy_migration_rejects_linked_source_roots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside_apps = tmp_path / "outside-apps"
    outside_apps.mkdir()
    apps_link = tmp_path / "backend" / "apps"
    apps_link.parent.mkdir()
    try:
        apps_link.symlink_to(outside_apps, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")

    with pytest.raises(WorkspaceMigrationError, match="real directory"):
        migrate_old_data("workspace")
    assert apps_link.is_symlink()

    apps_link.unlink()
    outside_database = tmp_path / "outside.sqlite3"
    sqlite3.connect(outside_database).close()
    database_link = tmp_path / "db.sqlite3"
    database_link.symlink_to(outside_database)
    with pytest.raises(WorkspaceMigrationError, match="regular file"):
        migrate_old_data("workspace")
    assert database_link.is_symlink()
    assert outside_database.exists()


def test_invalid_legacy_database_fails_startup_without_hiding_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    connection = sqlite3.connect("db.sqlite3")
    connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    connection.close()

    with pytest.raises(WorkspaceMigrationError, match="legacy database"):
        migrate_old_data("workspace")

    assert (tmp_path / "db.sqlite3").exists()
    assert not (tmp_path / "db.sqlite3.backup").exists()


def test_automatic_migration(tmp_path, monkeypatch):
    # Setup paths inside tmp_path
    monkeypatch.chdir(tmp_path)

    workspace_dir = "workspace"
    old_apps_dir = os.path.join("backend", "apps")
    os.makedirs(old_apps_dir, exist_ok=True)

    # Create a mock legacy app
    app_id = "legacy-widget"
    app_path = os.path.join(old_apps_dir, app_id)
    os.makedirs(app_path, exist_ok=True)
    with open(os.path.join(app_path, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({"id": app_id, "title": "Legacy App"}, f)
    with open(os.path.join(app_path, "index.html"), "w", encoding="utf-8") as f:
        f.write("<h1>Legacy HTML</h1>")

    # Create mock legacy db.sqlite3
    db_path = "db.sqlite3"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE chatsession (id VARCHAR PRIMARY KEY, title VARCHAR, created_at DATETIME, updated_at DATETIME)"
    )
    cursor.execute(
        "CREATE TABLE chatmessage (id INTEGER PRIMARY KEY, session_id VARCHAR, role VARCHAR, sender VARCHAR, content VARCHAR, timestamp DATETIME)"
    )
    cursor.execute(
        "CREATE TABLE llmauditlog (id INTEGER PRIMARY KEY, timestamp DATETIME, provider VARCHAR, model VARCHAR, prompt VARCHAR, response VARCHAR)"
    )

    # Insert session
    cursor.execute(
        "INSERT INTO chatsession VALUES ('sess-100', 'Old Chat', '2026-07-10T12:00:00', '2026-07-10T12:05:00')"
    )
    # Insert message
    cursor.execute(
        "INSERT INTO chatmessage VALUES (10, 'sess-100', 'user', 'user', 'Legacy Hello', '2026-07-10T12:01:00')"
    )
    # Insert audit log
    cursor.execute(
        "INSERT INTO llmauditlog VALUES (50, '2026-07-10T12:01:05', 'openai', 'gpt-4', 'Legacy Prompt', 'Legacy Response')"
    )

    conn.commit()
    conn.close()

    # Run Migration
    migrate_old_data(workspace_dir)

    # Verify migration results
    # 1. Apps migrated
    new_app_meta = os.path.join(workspace_dir, "apps", app_id, "metadata.json")
    assert os.path.exists(new_app_meta)
    with open(new_app_meta, encoding="utf-8") as f:
        app_data = json.load(f)
        assert app_data["title"] == "Legacy App"

    # 2. Session migrated
    new_sess_file = os.path.join(workspace_dir, "sessions", "sess-100.json")
    assert os.path.exists(new_sess_file)
    with open(new_sess_file, encoding="utf-8") as f:
        sess_data = json.load(f)
        assert sess_data["title"] == "Old Chat"
        assert len(sess_data["messages"]) == 1
        assert sess_data["messages"][0]["content"] == "Legacy Hello"

    migrated_storage = WorkspaceStorage(workspace_dir)
    next_message = ChatMessage(session_id="sess-100", content="After migration")
    migrated_storage.add(next_message)
    migrated_storage.commit()
    assert next_message.id == 11
    assert [message.content for message in migrated_storage.get_messages("sess-100")] == [
        "Legacy Hello",
        "After migration",
    ]

    # 3. Audit log migrated
    new_audit_file = os.path.join(workspace_dir, "audit_logs.jsonl")
    assert os.path.exists(new_audit_file)
    with open(new_audit_file, encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) == 1
        log_data = json.loads(lines[0])
        assert log_data["provider"] == "openai"
        assert log_data["prompt"] == "Legacy Prompt"

    # 4. Old paths renamed to backup
    assert os.path.exists(db_path + ".backup")
    assert os.path.exists(old_apps_dir + ".backup")
    assert not os.path.exists(db_path)
    assert not os.path.exists(old_apps_dir)
