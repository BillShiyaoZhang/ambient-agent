import contextvars
import json
import logging
import os
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
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
MAX_SESSION_ID_BYTES = 240

logger = logging.getLogger(__name__)

_WORKSPACE_LOCKS_GUARD = threading.Lock()
_WORKSPACE_LOCKS: dict[str, threading.RLock] = {}
_WORKSPACE_LAST_AUDIT_IDS: dict[str, int] = {}


class WorkspaceStorageCorruptionError(RuntimeError):
    """A persisted workspace artifact exists but cannot be read safely."""


class WorkspaceMigrationError(RuntimeError):
    """Legacy data could not be migrated without risking silent data loss."""


def validate_session_id(value: Any) -> str:
    """Validate the opaque identifier used as a direct session filename."""

    if not isinstance(value, str) or not value:
        raise ValueError("Session id must be a non-empty string")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("Session id must not contain path separators")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Session id must not contain control characters")
    if len(value.encode("utf-8")) > MAX_SESSION_ID_BYTES:
        raise ValueError(f"Session id must not exceed {MAX_SESSION_ID_BYTES} UTF-8 bytes")
    return value


def _workspace_lock(workspace_dir: str) -> threading.RLock:
    key = os.path.realpath(os.path.abspath(workspace_dir))
    with _WORKSPACE_LOCKS_GUARD:
        return _WORKSPACE_LOCKS.setdefault(key, threading.RLock())


def _next_audit_id(workspace_dir: str) -> int:
    """Return a process-unique, JavaScript-safe timestamp identifier."""

    key = os.path.realpath(os.path.abspath(workspace_dir))
    candidate = time.time_ns() // 1_000
    with _WORKSPACE_LOCKS_GUARD:
        value = max(candidate, _WORKSPACE_LAST_AUDIT_IDS.get(key, 0) + 1)
        _WORKSPACE_LAST_AUDIT_IDS[key] = value
        return value


def _write_json_atomic(path: str, value: Any) -> None:
    """Durably replace one JSON artifact without exposing a partial file."""

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _available_backup_path(path: str) -> str:
    candidate = f"{path}.backup"
    suffix = 1
    while os.path.lexists(candidate):
        candidate = f"{path}.backup-{suffix}"
        suffix += 1
    return candidate


def _tree_contains_symlink(root: str) -> bool:
    """Inspect a legacy tree without following links outside its boundary."""

    for current, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            try:
                mode = os.lstat(os.path.join(current, name)).st_mode
            except OSError as exc:
                raise WorkspaceMigrationError(f"Unable to inspect legacy App entry: {name}") from exc
            if stat.S_ISLNK(mode):
                return True
    return False


def _next_message_id(messages: list[dict[str, Any]]) -> int:
    used_ids: set[int] = set()
    for message in messages:
        raw_id = message.get("id")
        if isinstance(raw_id, bool):
            continue
        try:
            normalized = int(raw_id)
        except (TypeError, ValueError):
            continue
        if normalized > 0:
            used_ids.add(normalized)
    return max(used_ids, default=0) + 1


def _require_session_identity(data: dict[str, Any], session_id: str) -> None:
    if data.get("id") != session_id:
        raise WorkspaceStorageCorruptionError("Persisted session identity does not match its filename")


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
    if os.path.lexists(old_apps_dir):
        if os.path.islink(old_apps_dir) or not os.path.isdir(old_apps_dir):
            raise WorkspaceMigrationError("Legacy Apps source must be a real directory, not a link")
        print(f"[Migration] Migrating apps from {old_apps_dir} to {new_apps_dir}...")
        try:
            os.makedirs(new_apps_dir, exist_ok=True)
            for item in os.listdir(old_apps_dir):
                old_item_path = os.path.join(old_apps_dir, item)
                new_item_path = os.path.join(new_apps_dir, item)
                if os.path.lexists(new_item_path):
                    logger.warning(
                        "Keeping existing workspace App %s; legacy copy remains in the migration backup",
                        item,
                    )
                    continue
                if os.path.islink(old_item_path):
                    logger.warning("Skipping unsafe legacy App symlink %s", old_item_path)
                    continue
                if os.path.isdir(old_item_path):
                    if _tree_contains_symlink(old_item_path):
                        logger.warning("Skipping legacy App with an unsafe nested symlink: %s", old_item_path)
                        continue
                    # Preserve, rather than dereference, any link introduced
                    # during the copy. A post-copy scan rejects the result
                    # before App discovery can read it.
                    shutil.copytree(old_item_path, new_item_path, symlinks=True)
                    if _tree_contains_symlink(new_item_path):
                        shutil.rmtree(new_item_path)
                        logger.warning("Skipping legacy App changed to contain a symlink: %s", old_item_path)
                        continue
                else:
                    shutil.copy2(old_item_path, new_item_path)
            # Rename old dir to backup
            shutil.move(old_apps_dir, _available_backup_path(old_apps_dir))
            print("[Migration] Apps migration completed.")
        except Exception as e:
            raise WorkspaceMigrationError(f"Unable to migrate legacy Apps: {e}") from e

    # 2. Migrate database
    old_db_path = "db.sqlite3"
    if os.path.lexists(old_db_path):
        if os.path.islink(old_db_path) or not os.path.isfile(old_db_path):
            raise WorkspaceMigrationError("Legacy database source must be a regular file, not a link")
        print(f"[Migration] Migrating database from {old_db_path} to workspace...")
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(old_db_path)
            cursor = conn.cursor()

            # Fetch sessions
            cursor.execute("SELECT id, title, created_at, updated_at FROM chatsession")
            sessions = cursor.fetchall()

            os.makedirs(os.path.join(workspace_dir, "sessions"), exist_ok=True)

            for s_id, s_title, s_created, s_updated in sessions:
                try:
                    safe_session_id = validate_session_id(s_id)
                except ValueError:
                    logger.warning("Skipping unsafe legacy session id %r", s_id)
                    continue
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
                    "id": safe_session_id,
                    "title": s_title,
                    "language": "zh",
                    "model_selection": None,
                    "created_at": s_created,
                    "updated_at": s_updated,
                    "messages": messages_list,
                }
                session_file = os.path.join(workspace_dir, "sessions", f"{safe_session_id}.json")
                if os.path.exists(session_file):
                    logger.warning(
                        "Keeping existing workspace session %r; legacy copy remains in the database backup",
                        safe_session_id,
                    )
                    continue
                with _workspace_lock(workspace_dir):
                    _write_json_atomic(session_file, session_data)

            # Fetch LLMAuditLogs
            cursor.execute(
                "SELECT id, timestamp, provider, model, prompt, response FROM llmauditlog ORDER BY timestamp ASC"
            )
            audit_logs = cursor.fetchall()
            audit_file = os.path.join(workspace_dir, "audit_logs.jsonl")
            os.makedirs(os.path.dirname(audit_file), exist_ok=True)
            with _workspace_lock(workspace_dir):
                existing_audit_ids: set[str] = set()
                if os.path.isfile(audit_file):
                    with open(audit_file, encoding="utf-8") as existing_audit:
                        for line in existing_audit:
                            try:
                                existing_audit_ids.add(str(json.loads(line).get("id")))
                            except (AttributeError, json.JSONDecodeError):
                                continue
                with open(audit_file, "a", encoding="utf-8") as f:
                    for a_id, a_timestamp, a_provider, a_model, a_prompt, a_response in audit_logs:
                        if str(a_id) in existing_audit_ids:
                            continue
                        log_entry = {
                            "id": a_id,
                            "timestamp": a_timestamp,
                            "provider": a_provider,
                            "model": a_model,
                            "prompt": a_prompt,
                            "response": a_response,
                        }
                        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())

            # Backup database file
            conn.close()
            conn = None
            shutil.move(old_db_path, _available_backup_path(old_db_path))
            print("[Migration] Database migration completed successfully.")
        except Exception as e:
            raise WorkspaceMigrationError(f"Unable to migrate legacy database: {e}") from e
        finally:
            if conn is not None:
                conn.close()


class WorkspaceStorage:
    def __init__(self, workspace_dir: str | None = None):
        if not workspace_dir:
            workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.workspace_dir = workspace_dir
        self.sessions_dir = os.path.join(self.workspace_dir, "sessions")
        self.apps_dir = os.path.join(self.workspace_dir, "apps")

        os.makedirs(self.sessions_dir, exist_ok=True)
        os.makedirs(self.apps_dir, exist_ok=True)

        self._lock = _workspace_lock(self.workspace_dir)
        self._pending_adds: contextvars.ContextVar[tuple[Any, ...]] = contextvars.ContextVar(
            f"workspace_pending_adds_{id(self)}",
            default=(),
        )

    def _session_path(self, session_id: Any) -> str:
        return os.path.join(self.sessions_dir, f"{validate_session_id(session_id)}.json")

    @staticmethod
    def _load_json_object(path: str, *, artifact: str) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkspaceStorageCorruptionError(f"Unable to read persisted {artifact}") from exc
        if not isinstance(value, dict):
            raise WorkspaceStorageCorruptionError(f"Persisted {artifact} must be a JSON object")
        return value

    def get(self, model_class: type[BaseModel], obj_id: str) -> BaseModel | None:
        """
        Emulates SQLAlchemy Session.get() for backward compatibility.
        """
        if model_class == ChatSession:
            session_file = self._session_path(obj_id)
            with self._lock:
                if os.path.exists(session_file):
                    try:
                        data = self._load_json_object(session_file, artifact=f"session {obj_id!r}")
                        _require_session_identity(data, obj_id)

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
                    except (KeyError, TypeError, ValueError, WorkspaceStorageCorruptionError):
                        logger.warning("Unable to load session %r", obj_id, exc_info=True)
                        return None
        return None

    def add(self, obj: Any) -> None:
        """
        Emulates SQLAlchemy Session.add(). Stashes changes for commit.
        """
        self._pending_adds.set((*self._pending_adds.get(), obj))

    def commit(self) -> None:
        """
        Emulates SQLAlchemy Session.commit(). Writes stashed changes to disk.
        """
        pending = self._pending_adds.get()
        if not pending:
            return
        with self._lock:
            for index, obj in enumerate(pending):
                if isinstance(obj, ChatSession):
                    self._save_session_meta(obj)
                elif isinstance(obj, ChatMessage):
                    self._save_message(obj)
                elif isinstance(obj, LLMAuditLog):
                    self._save_audit_log(obj)
                # A later object can fail after this one is already durable.
                # Acknowledge successful items one at a time so retrying the
                # unit of work cannot duplicate append-only audit records.
                self._pending_adds.set(pending[index + 1 :])

    def refresh(self, obj: Any) -> None:
        """
        Emulates SQLAlchemy Session.refresh(). Populates missing primary key IDs.
        """
        if isinstance(obj, ChatMessage) and obj.id is None:
            session_file = self._session_path(obj.session_id)
            with self._lock:
                next_id = 1
                if os.path.exists(session_file):
                    data = self._load_json_object(session_file, artifact=f"session {obj.session_id!r}")
                    _require_session_identity(data, obj.session_id)
                    messages = data.get("messages", [])
                    if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
                        raise WorkspaceStorageCorruptionError("Persisted session messages must be a JSON object array")
                    next_id = _next_message_id(messages)
                obj.id = next_id
        elif isinstance(obj, LLMAuditLog) and obj.id is None:
            obj.id = _next_audit_id(self.workspace_dir)

    # --- Domain Specific Storage Accessors ---

    def get_sessions(self) -> list[ChatSession]:
        sessions = []
        with self._lock:
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
        session_file = self._session_path(session_id)
        with self._lock:
            if not os.path.exists(session_file):
                return []
            try:
                data = self._load_json_object(session_file, artifact=f"session {session_id!r}")
                _require_session_identity(data, session_id)
                raw_messages = data.get("messages", [])
                if not isinstance(raw_messages, list):
                    raise WorkspaceStorageCorruptionError("Persisted session messages must be a JSON array")
                messages = []
                for m in raw_messages:
                    if not isinstance(m, dict):
                        raise WorkspaceStorageCorruptionError("Persisted session message must be a JSON object")
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
                            context_policy=m.get("context_policy", "reusable"),
                            provenance=m.get("provenance"),
                            timestamp=t_val or datetime.now(UTC),
                        )
                    )
                return messages
            except (TypeError, ValueError, WorkspaceStorageCorruptionError):
                logger.warning("Unable to load messages for session %r", session_id, exc_info=True)
                return []

    def get_audit_logs(self) -> list[LLMAuditLog]:
        audit_file = os.path.join(self.workspace_dir, "audit_logs.jsonl")
        logs = []
        with self._lock:
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
        with self._lock:
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
                temporary = f"{audit_file}.compact-{os.getpid()}-{threading.get_ident()}"
                with open(temporary, "w", encoding="utf-8") as destination:
                    for line in retained:
                        destination.write(line + "\n")
                    destination.flush()
                    os.fsync(destination.fileno())
                os.replace(temporary, audit_file)
            except OSError:
                return 0
            finally:
                if "temporary" in locals() and os.path.exists(temporary):
                    os.unlink(temporary)
            return removed

    def delete_audit_logs_for_session(
        self,
        session_id: str,
        *,
        run_ids: set[str] | None = None,
    ) -> int:
        """Remove identifiable audit records for one deleted conversation."""

        session_id = validate_session_id(session_id)
        associated_run_ids = run_ids or set()
        audit_file = os.path.join(self.workspace_dir, "audit_logs.jsonl")
        with self._lock:
            if not os.path.isfile(audit_file):
                return 0
            with open(audit_file, encoding="utf-8") as source:
                lines = source.readlines()
            retained: list[str] = []
            removed = 0
            for line in lines:
                try:
                    data = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    # Ownership is unknowable, so preserve corrupt evidence.
                    retained.append(line)
                    continue
                if not isinstance(data, dict):
                    retained.append(line)
                    continue
                if data.get("session_id") == session_id or str(data.get("run_id") or "") in associated_run_ids:
                    removed += 1
                else:
                    retained.append(line)
            if not removed:
                return 0

            fd, temporary = tempfile.mkstemp(prefix=".audit-delete-", suffix=".tmp", dir=self.workspace_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as destination:
                    destination.writelines(retained)
                    destination.flush()
                    os.fsync(destination.fileno())
                os.replace(temporary, audit_file)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return removed

    def delete_session(self, session_id: str) -> bool:
        session_file = self._session_path(session_id)
        with self._lock:
            if os.path.exists(session_file):
                os.remove(session_file)
                return True
            return False

    def get_canvas_config(self) -> dict[str, Any]:
        canvas_file = os.path.join(self.workspace_dir, "canvas.json")
        with self._lock:
            if not os.path.exists(canvas_file):
                return normalize_canvas_config({})
            data = self._load_json_object(canvas_file, artifact="Canvas configuration")
            return normalize_canvas_config(data)

    def save_canvas_config(self, config: dict[str, Any]) -> None:
        canvas_file = os.path.join(self.workspace_dir, "canvas.json")
        with self._lock:
            if os.path.exists(canvas_file):
                # Do not turn a recoverable malformed file into a valid but
                # empty layout merely because a client saved its default view.
                self._load_json_object(canvas_file, artifact="Canvas configuration")
            _write_json_atomic(canvas_file, normalize_canvas_config(config))

    # --- Internal Helpers ---

    def _save_session_meta(self, session: ChatSession) -> None:
        session_file = self._session_path(session.id)
        with self._lock:
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
                existing = self._load_json_object(session_file, artifact=f"session {session.id!r}")
                _require_session_identity(existing, session.id)
                messages = existing.get("messages", [])
                if not isinstance(messages, list):
                    raise WorkspaceStorageCorruptionError("Persisted session messages must be a JSON array")
                data["messages"] = messages
                data["created_at"] = existing.get("created_at", data["created_at"])
            _write_json_atomic(session_file, data)

    def _save_message(self, message: ChatMessage) -> None:
        session_file = self._session_path(message.session_id)
        with self._lock:
            data = {
                "id": message.session_id,
                "title": "Active Chat",
                "language": "zh",
                "model_selection": None,
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
                "messages": [],
            }
            if os.path.exists(session_file):
                data = self._load_json_object(session_file, artifact=f"session {message.session_id!r}")
                _require_session_identity(data, message.session_id)
            messages = data.get("messages", [])
            if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
                raise WorkspaceStorageCorruptionError("Persisted session messages must be a JSON object array")

            # Durable runs can replay a chat projection after a crash. Reuse the
            # role-specific projection for that run instead of appending a duplicate.
            if message.id is None and message.run_id:
                for existing in messages:
                    if (
                        existing.get("run_id") == message.run_id
                        and existing.get("role", "user") == message.role
                        and existing.get("sender", "user") == message.sender
                    ):
                        message.id = existing.get("id")
                        break
            if message.id is None:
                message.id = _next_message_id(messages)

            msg_dict = {
                "id": message.id,
                "run_id": message.run_id,
                "role": message.role,
                "sender": message.sender,
                "content": message.content,
                "context_policy": message.context_policy,
                "provenance": message.provenance,
                "timestamp": message.timestamp.isoformat()
                if isinstance(message.timestamp, datetime)
                else message.timestamp,
            }

            replaced = False
            for i, existing in enumerate(messages):
                if existing.get("id") == message.id:
                    messages[i] = msg_dict
                    replaced = True
                    break
            if not replaced:
                messages.append(msg_dict)

            data["messages"] = messages
            data["updated_at"] = datetime.now(UTC).isoformat()
            _write_json_atomic(session_file, data)

    def _save_audit_log(self, log: LLMAuditLog) -> None:
        audit_file = os.path.join(self.workspace_dir, "audit_logs.jsonl")
        with self._lock:
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
            needs_separator = False
            if os.path.isfile(audit_file) and os.path.getsize(audit_file) > 0:
                with open(audit_file, "rb") as existing:
                    existing.seek(-1, os.SEEK_END)
                    needs_separator = existing.read(1) != b"\n"
            with open(audit_file, "a", encoding="utf-8") as f:
                if needs_separator:
                    # Preserve a crash-truncated tail as its own unreadable
                    # record instead of concatenating it with the next entry.
                    f.write("\n")
                f.write(json.dumps(log_dict, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
