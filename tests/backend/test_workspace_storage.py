import os
import json
import sqlite3
import shutil
import pytest
from datetime import datetime, timezone

from backend.workspace_storage import WorkspaceStorage, migrate_old_data
from backend.models import ChatSession, ChatMessage, LLMAuditLog

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
    with open(session_file, "r", encoding="utf-8") as f:
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
    assert canvas_data["pinned_ids"] == ["app1", "app2"]
    assert canvas_data["widget_spans"]["app1"]["cols"] == 2
    
    # 5. Delete Session
    success = storage.delete_session(session_id)
    assert success is True
    assert not os.path.exists(session_file)
    assert len(storage.get_sessions()) == 0


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
    cursor.execute("CREATE TABLE chatsession (id VARCHAR PRIMARY KEY, title VARCHAR, created_at DATETIME, updated_at DATETIME)")
    cursor.execute("CREATE TABLE chatmessage (id INTEGER PRIMARY KEY, session_id VARCHAR, role VARCHAR, sender VARCHAR, content VARCHAR, timestamp DATETIME)")
    cursor.execute("CREATE TABLE llmauditlog (id INTEGER PRIMARY KEY, timestamp DATETIME, provider VARCHAR, model VARCHAR, prompt VARCHAR, response VARCHAR)")
    
    # Insert session
    cursor.execute("INSERT INTO chatsession VALUES ('sess-100', 'Old Chat', '2026-07-10T12:00:00', '2026-07-10T12:05:00')")
    # Insert message
    cursor.execute("INSERT INTO chatmessage VALUES (10, 'sess-100', 'user', 'user', 'Legacy Hello', '2026-07-10T12:01:00')")
    # Insert audit log
    cursor.execute("INSERT INTO llmauditlog VALUES (50, '2026-07-10T12:01:05', 'openai', 'gpt-4', 'Legacy Prompt', 'Legacy Response')")
    
    conn.commit()
    conn.close()
    
    # Run Migration
    migrate_old_data(workspace_dir)
    
    # Verify migration results
    # 1. Apps migrated
    new_app_meta = os.path.join(workspace_dir, "apps", app_id, "metadata.json")
    assert os.path.exists(new_app_meta)
    with open(new_app_meta, "r", encoding="utf-8") as f:
        app_data = json.load(f)
        assert app_data["title"] == "Legacy App"
        
    # 2. Session migrated
    new_sess_file = os.path.join(workspace_dir, "sessions", "sess-100.json")
    assert os.path.exists(new_sess_file)
    with open(new_sess_file, "r", encoding="utf-8") as f:
        sess_data = json.load(f)
        assert sess_data["title"] == "Old Chat"
        assert len(sess_data["messages"]) == 1
        assert sess_data["messages"][0]["content"] == "Legacy Hello"
        
    # 3. Audit log migrated
    new_audit_file = os.path.join(workspace_dir, "audit_logs.jsonl")
    assert os.path.exists(new_audit_file)
    with open(new_audit_file, "r", encoding="utf-8") as f:
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
