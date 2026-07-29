import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from backend.models import ChatMessage, ChatSession, LLMAuditLog


CANVAS_VERSION = 3
AUDIT_TEXT_MAX_BYTES = 32 * 1024
AUDIT_METADATA_MAX_BYTES = 8 * 1024
DEFAULT_WINDOW_BOUNDS = {"x": 0.16, "y": 0.12, "width": 0.68, "height": 0.72}
WINDOW_MODES = {"maximized", "floating", "snapped"}
SNAP_ZONES = {"left", "right", "top-left", "top-right", "bottom-left", "bottom-right"}


def _number(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _bounded_text(value: Any, max_bytes: int = AUDIT_TEXT_MAX_BYTES) -> str:
    """Keep audit previews useful without allowing an entry to grow without bound."""
    text = value if isinstance(value, str) else str(value or "")
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    marker = f"\n...[truncated; original_bytes={len(encoded)}]"
    prefix_limit = max(0, max_bytes - len(marker.encode("utf-8")))
    prefix = encoded[:prefix_limit].decode("utf-8", errors="ignore")
    return prefix + marker


def _bounded_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {"value": str(value)}
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    except Exception:
        return {"value": "[unserializable]"}
    if len(encoded) <= AUDIT_METADATA_MAX_BYTES:
        return value
    return {"truncated": True, "original_bytes": len(encoded)}


def _normalize_bounds(value: Any) -> dict[str, float]:
    raw = value if isinstance(value, dict) else {}
    width = min(1.0, max(0.3, _number(raw.get("width"), DEFAULT_WINDOW_BOUNDS["width"])))
    height = min(1.0, max(0.3, _number(raw.get("height"), DEFAULT_WINDOW_BOUNDS["height"])))
    x = min(1.0 - width, max(0.0, _number(raw.get("x"), DEFAULT_WINDOW_BOUNDS["x"])))
    y = min(1.0 - height, max(0.0, _number(raw.get("y"), DEFAULT_WINDOW_BOUNDS["y"])))
    return {"x": x, "y": y, "width": width, "height": height}


def _default_window(index: int, mode: str = "maximized") -> dict[str, Any]:
    offset = min(index * 0.025, 0.16)
    bounds = _normalize_bounds(
        {
            "x": DEFAULT_WINDOW_BOUNDS["x"] + offset,
            "y": DEFAULT_WINDOW_BOUNDS["y"] + offset,
            "width": DEFAULT_WINDOW_BOUNDS["width"],
            "height": DEFAULT_WINDOW_BOUNDS["height"],
        }
    )
    return {"mode": mode, "bounds": bounds}


def normalize_canvas_config(config: Any) -> dict[str, Any]:
    """Return the global workspace configuration in the Canvas V3 wire shape."""
    raw = config if isinstance(config, dict) else {}
    is_v3 = raw.get("version") == CANVAS_VERSION or "open_app_ids" in raw
    source_ids = raw.get("open_app_ids", []) if is_v3 else raw.get("pinned_ids", [])
    open_app_ids: list[str] = []
    for app_id in source_ids if isinstance(source_ids, list) else []:
        if isinstance(app_id, str) and app_id and app_id not in open_app_ids:
            open_app_ids.append(app_id)

    source_windows = raw.get("windows", {}) if is_v3 else {}
    legacy_spans = raw.get("widget_spans", {}) if not is_v3 else {}
    windows: dict[str, Any] = {}
    for index, app_id in enumerate(open_app_ids):
        candidate = source_windows.get(app_id) if isinstance(source_windows, dict) else None
        if isinstance(candidate, dict):
            mode = candidate.get("mode") if candidate.get("mode") in WINDOW_MODES else "maximized"
            window = {"mode": mode, "bounds": _normalize_bounds(candidate.get("bounds"))}
            if isinstance(candidate.get("restoreBounds"), dict):
                window["restoreBounds"] = _normalize_bounds(candidate["restoreBounds"])
            if candidate.get("snapZone") in SNAP_ZONES:
                window["snapZone"] = candidate["snapZone"]
            windows[app_id] = window
            continue

        if isinstance(legacy_spans, dict) and isinstance(legacy_spans.get(app_id), dict):
            span = legacy_spans[app_id]
            cols = min(12.0, max(4.0, _number(span.get("cols"), 8.0)))
            rows = min(12.0, max(4.0, _number(span.get("rows"), 8.0)))
            offset = min(index * 0.025, 0.16)
            windows[app_id] = {
                "mode": "floating",
                "bounds": _normalize_bounds(
                    {"x": 0.12 + offset, "y": 0.1 + offset, "width": cols / 12.0, "height": rows / 12.0}
                ),
            }
        else:
            windows[app_id] = _default_window(index, "floating" if not is_v3 else "maximized")

    active_app_id = raw.get("active_app_id")
    if active_app_id not in open_app_ids:
        active_app_id = open_app_ids[-1] if open_app_ids else None

    return {
        "version": CANVAS_VERSION,
        "open_app_ids": open_app_ids,
        "active_app_id": active_app_id,
        "windows": windows,
    }


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
                        language=data.get("language", "zh"),
                        model_selection=data.get("model_selection"),
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
                        run_id=m.get("run_id"),
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
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            t_val = data.get("timestamp")
                            if isinstance(t_val, str):
                                t_val = datetime.fromisoformat(t_val)
                            if isinstance(t_val, datetime) and t_val.tzinfo is None:
                                t_val = t_val.replace(tzinfo=UTC)
                            logs.append(
                                LLMAuditLog(
                                    id=data.get("id"),
                                    timestamp=t_val or datetime.now(UTC),
                                    provider=data.get("provider"),
                                    model=data.get("model"),
                                    prompt=data.get("prompt"),
                                    response=data.get("response"),
                                    stage=data.get("stage", "chat"),
                                    run_id=data.get("run_id"),
                                    session_id=data.get("session_id"),
                                    step_id=data.get("step_id"),
                                    attempt=data.get("attempt"),
                                    trace_id=data.get("trace_id"),
                                    latency_ms=data.get("latency_ms"),
                                    usage=data.get("usage"),
                                    finish_reason=data.get("finish_reason"),
                                    error=data.get("error"),
                                    prompt_hash=data.get("prompt_hash"),
                                    tool_schema_hash=data.get("tool_schema_hash"),
                                    artifact_hashes=data.get("artifact_hashes") or {},
                                )
                            )
                        except Exception:
                            # Preserve readable entries around a corrupt or
                            # partially-written JSONL line.
                            continue
            except Exception:
                pass
        # Sort by timestamp desc
        logs.sort(key=lambda l: l.timestamp or datetime.min, reverse=True)
        return logs

    def cleanup_audit_logs(self, days: int | None = None) -> int:
        """Apply the configured retention period to the workspace JSONL audit."""

        if days is None:
            try:
                days = int(os.getenv("AGENT_AUDIT_RETENTION_DAYS", "30"))
            except ValueError:
                days = 30
        days = max(1, days)
        audit_file = os.path.join(self.workspace_dir, "audit_logs.jsonl")
        if not os.path.isfile(audit_file):
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=days)
        retained: list[str] = []
        removed = 0
        try:
            with open(audit_file, encoding="utf-8") as source:
                for raw_line in source:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        timestamp = datetime.fromisoformat(str(data["timestamp"]))
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=UTC)
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        # Corrupt entries cannot be assigned a safe retention
                        # age and are discarded instead of being kept forever.
                        removed += 1
                        continue
                    if timestamp < cutoff:
                        removed += 1
                    else:
                        retained.append(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
            temporary = f"{audit_file}.compact-{os.getpid()}"
            with open(temporary, "w", encoding="utf-8") as destination:
                for line in retained:
                    destination.write(line + "\n")
            os.replace(temporary, audit_file)
        except OSError:
            return 0
        return removed

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
                    return normalize_canvas_config(json.load(f))
            except Exception:
                pass
        return normalize_canvas_config({})

    def save_canvas_config(self, config: dict[str, Any]) -> None:
        canvas_file = os.path.join(self.workspace_dir, "canvas.json")
        try:
            with open(canvas_file, "w", encoding="utf-8") as f:
                json.dump(normalize_canvas_config(config), f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # --- Internal Helpers ---

    def _save_session_meta(self, session: ChatSession) -> None:
        session_file = os.path.join(self.sessions_dir, f"{session.id}.json")
        data = {
            "id": session.id,
            "title": session.title,
            "language": session.language,
            "model_selection": session.model_selection.model_dump(mode="json") if session.model_selection else None,
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

        # Durable runs can replay a chat projection after a crash. Reuse the
        # role-specific projection for that run instead of appending a duplicate.
        if message.id is None and message.run_id:
            for existing in data.get("messages", []):
                if (
                    existing.get("run_id") == message.run_id
                    and existing.get("role", "user") == message.role
                    and existing.get("sender", "user") == message.sender
                ):
                    message.id = existing.get("id")
                    break
        if message.id is None:
            self.refresh(message)

        msg_dict = {
            "id": message.id,
            "run_id": message.run_id,
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
            "prompt": _bounded_text(log.prompt),
            "response": _bounded_text(log.response),
            "stage": getattr(log, "stage", "chat"),
            "run_id": log.run_id,
            "session_id": log.session_id,
            "step_id": log.step_id,
            "attempt": log.attempt,
            "trace_id": log.trace_id,
            "latency_ms": log.latency_ms,
            "usage": _bounded_metadata(log.usage),
            "finish_reason": log.finish_reason,
            "error": _bounded_text(log.error, AUDIT_METADATA_MAX_BYTES) if log.error is not None else None,
            "prompt_hash": log.prompt_hash,
            "tool_schema_hash": log.tool_schema_hash,
            "artifact_hashes": _bounded_metadata(log.artifact_hashes) or {},
        }
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_dict, ensure_ascii=False) + "\n")
