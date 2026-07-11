import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from backend.models import ChatMessage, ChatSession, LLMAuditLog


def migrate_old_data(workspace_dir: str) -> None:
    """
    Checks if legacy db.sqlite3 or backend/apps directory exist.
    If so, migrates their contents to the workspace and renames the legacy paths to *.backup.
    """
    # 1. Migrate apps
    old_apps_dir = os.path.join("backend", "apps")
    new_apps_dir = os.path.join(workspace_dir, "apps")
    if os.path.exists(old_apps_dir):
        print(f"[Migration] Migrating apps from {old_apps_dir} to {new_apps_dir}...")
        try:
            os.makedirs(new_apps_dir, exist_ok=True)
            for item in os.listdir(old_apps_dir):
                old_item_path = os.path.join(old_apps_dir, item)
                new_item_path = os.path.join(new_apps_dir, item)
                if os.path.isdir(old_item_path):
                    if os.path.exists(new_item_path):
                        shutil.rmtree(new_item_path)
                    shutil.copytree(old_item_path, new_item_path)
                else:
                    shutil.copy2(old_item_path, new_item_path)
            # Rename old dir to backup
            shutil.move(old_apps_dir, old_apps_dir + ".backup")
            print("[Migration] Apps migration completed.")
        except Exception as e:
            print(f"[Migration] Error migrating apps: {e}")

    # 2. Migrate database
    old_db_path = "db.sqlite3"
    if os.path.exists(old_db_path):
        print(f"[Migration] Migrating database from {old_db_path} to workspace...")
        try:
            conn = sqlite3.connect(old_db_path)
            cursor = conn.cursor()

            # Fetch sessions
            cursor.execute("SELECT id, title, created_at, updated_at FROM chatsession")
            sessions = cursor.fetchall()

            os.makedirs(os.path.join(workspace_dir, "sessions"), exist_ok=True)

            for s_id, s_title, s_created, s_updated in sessions:
                # Fetch messages for this session
                cursor.execute(
                    "SELECT id, role, sender, content, timestamp FROM chatmessage WHERE session_id = ? ORDER BY timestamp ASC",
                    (s_id,),
                )
                msgs = cursor.fetchall()
                messages_list = []
                for m_id, m_role, m_sender, m_content, m_timestamp in msgs:
                    messages_list.append(
                        {
                            "id": m_id,
                            "role": m_role or "user",
                            "sender": m_sender or "user",
                            "content": m_content,
                            "timestamp": m_timestamp,
                        }
                    )

                session_data = {
                    "id": s_id,
                    "title": s_title,
                    "created_at": s_created,
                    "updated_at": s_updated,
                    "messages": messages_list,
                }
                session_file = os.path.join(workspace_dir, "sessions", f"{s_id}.json")
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session_data, f, indent=2, ensure_ascii=False)

            # Fetch LLMAuditLogs
            cursor.execute(
                "SELECT id, timestamp, provider, model, prompt, response FROM llmauditlog ORDER BY timestamp ASC"
            )
            audit_logs = cursor.fetchall()
            audit_file = os.path.join(workspace_dir, "audit_logs.jsonl")
            os.makedirs(os.path.dirname(audit_file), exist_ok=True)
            with open(audit_file, "a", encoding="utf-8") as f:
                for a_id, a_timestamp, a_provider, a_model, a_prompt, a_response in audit_logs:
                    log_entry = {
                        "id": a_id,
                        "timestamp": a_timestamp,
                        "provider": a_provider,
                        "model": a_model,
                        "prompt": a_prompt,
                        "response": a_response,
                    }
                    f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

            conn.close()
            # Backup database file
            shutil.move(old_db_path, old_db_path + ".backup")
            print("[Migration] Database migration completed successfully.")
        except Exception as e:
            print(f"[Migration] Error migrating database: {e}")


class WorkspaceStorage:
    def __init__(self, workspace_dir: str | None = None):
        if not workspace_dir:
            workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.workspace_dir = workspace_dir
        self.sessions_dir = os.path.join(self.workspace_dir, "sessions")
        self.apps_dir = os.path.join(self.workspace_dir, "apps")

        os.makedirs(self.sessions_dir, exist_ok=True)
        os.makedirs(self.apps_dir, exist_ok=True)

        self._pending_adds = []

    def get(self, model_class: type[BaseModel], obj_id: str) -> BaseModel | None:
        """
        Emulates SQLAlchemy Session.get() for backward compatibility.
        """
        if model_class == ChatSession:
            session_file = os.path.join(self.sessions_dir, f"{obj_id}.json")
            if os.path.exists(session_file):
                try:
                    with open(session_file, encoding="utf-8") as f:
                        data = json.load(f)

                    # Parse timestamp strings
                    created_at = data.get("created_at")
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at)
                    updated_at = data.get("updated_at")
                    if isinstance(updated_at, str):
                        updated_at = datetime.fromisoformat(updated_at)

                    return ChatSession(
                        id=data["id"],
                        title=data["title"],
                        created_at=created_at or datetime.now(UTC),
                        updated_at=updated_at or datetime.now(UTC),
                    )
                except Exception:
                    return None
        return None

    def add(self, obj: Any) -> None:
        """
        Emulates SQLAlchemy Session.add(). Stashes changes for commit.
        """
        self._pending_adds.append(obj)

    def commit(self) -> None:
        """
        Emulates SQLAlchemy Session.commit(). Writes stashed changes to disk.
        """
        for obj in self._pending_adds:
            if isinstance(obj, ChatSession):
                self._save_session_meta(obj)
            elif isinstance(obj, ChatMessage):
                self._save_message(obj)
            elif isinstance(obj, LLMAuditLog):
                self._save_audit_log(obj)
        self._pending_adds.clear()

    def refresh(self, obj: Any) -> None:
        """
        Emulates SQLAlchemy Session.refresh(). Populates missing primary key IDs.
        """
        if isinstance(obj, ChatMessage) and obj.id is None:
            session_file = os.path.join(self.sessions_dir, f"{obj.session_id}.json")
            msg_count = 0
            if os.path.exists(session_file):
                try:
                    with open(session_file, encoding="utf-8") as f:
                        data = json.load(f)
                        msg_count = len(data.get("messages", []))
                except Exception:
                    pass
            obj.id = msg_count + 1
        elif isinstance(obj, LLMAuditLog) and obj.id is None:
            obj.id = int(datetime.now(UTC).timestamp() * 1000)

    # --- Domain Specific Storage Accessors ---

    def get_sessions(self) -> list[ChatSession]:
        sessions = []
        if not os.path.exists(self.sessions_dir):
            return []
        for item in os.listdir(self.sessions_dir):
            if item.endswith(".json"):
                sess_id = item[:-5]
                sess = self.get(ChatSession, sess_id)
                if sess:
                    sessions.append(sess)
        # Sort by updated_at desc
        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions

    def get_messages(self, session_id: str) -> list[ChatMessage]:
        session_file = os.path.join(self.sessions_dir, f"{session_id}.json")
        if not os.path.exists(session_file):
            return []
        try:
            with open(session_file, encoding="utf-8") as f:
                data = json.load(f)
            messages = []
            for m in data.get("messages", []):
                t_val = m.get("timestamp")
                if isinstance(t_val, str):
                    t_val = datetime.fromisoformat(t_val)
                messages.append(
                    ChatMessage(
                        id=m.get("id"),
                        session_id=session_id,
                        role=m.get("role", "user"),
                        sender=m.get("sender", "user"),
                        content=m.get("content", ""),
                        timestamp=t_val or datetime.now(UTC),
                    )
                )
            return messages
        except Exception:
            return []

    def get_audit_logs(self) -> list[LLMAuditLog]:
        audit_file = os.path.join(self.workspace_dir, "audit_logs.jsonl")
        logs = []
        if os.path.exists(audit_file):
            try:
                with open(audit_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            data = json.loads(line)
                            t_val = data.get("timestamp")
                            if isinstance(t_val, str):
                                t_val = datetime.fromisoformat(t_val)
                            logs.append(
                                LLMAuditLog(
                                    id=data.get("id"),
                                    timestamp=t_val or datetime.now(UTC),
                                    provider=data.get("provider"),
                                    model=data.get("model"),
                                    prompt=data.get("prompt"),
                                    response=data.get("response"),
                                )
                            )
            except Exception:
                pass
        # Sort by timestamp desc
        logs.sort(key=lambda l: l.timestamp or datetime.min, reverse=True)
        return logs

    def delete_session(self, session_id: str) -> bool:
        session_file = os.path.join(self.sessions_dir, f"{session_id}.json")
        if os.path.exists(session_file):
            try:
                os.remove(session_file)
                return True
            except Exception:
                return False
        return False

    def get_canvas_config(self) -> dict[str, Any]:
        canvas_file = os.path.join(self.workspace_dir, "canvas.json")
        if os.path.exists(canvas_file):
            try:
                with open(canvas_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"pinned_ids": [], "widget_spans": {}}

    def save_canvas_config(self, config: dict[str, Any]) -> None:
        canvas_file = os.path.join(self.workspace_dir, "canvas.json")
        try:
            with open(canvas_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # --- Internal Helpers ---

    def _save_session_meta(self, session: ChatSession) -> None:
        session_file = os.path.join(self.sessions_dir, f"{session.id}.json")
        data = {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at.isoformat()
            if isinstance(session.created_at, datetime)
            else session.created_at,
            "updated_at": session.updated_at.isoformat()
            if isinstance(session.updated_at, datetime)
            else session.updated_at,
            "messages": [],
        }
        if os.path.exists(session_file):
            try:
                with open(session_file, encoding="utf-8") as f:
                    existing = json.load(f)
                    data["messages"] = existing.get("messages", [])
                    data["created_at"] = existing.get("created_at", data["created_at"])
            except Exception:
                pass
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_message(self, message: ChatMessage) -> None:
        session_file = os.path.join(self.sessions_dir, f"{message.session_id}.json")
        data = {
            "id": message.session_id,
            "title": "Active Chat",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "messages": [],
        }
        if os.path.exists(session_file):
            try:
                with open(session_file, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        if message.id is None:
            self.refresh(message)

        msg_dict = {
            "id": message.id,
            "role": message.role,
            "sender": message.sender,
            "content": message.content,
            "timestamp": message.timestamp.isoformat()
            if isinstance(message.timestamp, datetime)
            else message.timestamp,
        }

        replaced = False
        for i, m in enumerate(data["messages"]):
            if m.get("id") == message.id:
                data["messages"][i] = msg_dict
                replaced = True
                break
        if not replaced:
            data["messages"].append(msg_dict)

        data["updated_at"] = datetime.now(UTC).isoformat()
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_audit_log(self, log: LLMAuditLog) -> None:
        audit_file = os.path.join(self.workspace_dir, "audit_logs.jsonl")
        if log.id is None:
            self.refresh(log)
        log_dict = {
            "id": log.id,
            "timestamp": log.timestamp.isoformat() if isinstance(log.timestamp, datetime) else log.timestamp,
            "provider": log.provider,
            "model": log.model,
            "prompt": log.prompt,
            "response": log.response,
        }
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_dict, ensure_ascii=False) + "\n")
