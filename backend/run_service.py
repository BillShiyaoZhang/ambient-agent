"""Persistent workspace-scoped background run service.

The run service deliberately owns execution state instead of a WebSocket.  A
browser may disconnect, switch chat sessions, or close an App window while a
Run continues.  SQLite leases make queue ownership explicit and provide safe
recovery after a backend restart.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app_store import AppStoreService, CapabilityAction


ACTIVE_STATUSES = {"queued", "running", "waiting_user", "cancel_requested", "needs_attention"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
CURRENT_AGENT_WORKFLOW_VERSION = 2
_JSON_COLUMNS = {
    "input",
    "result",
    "error",
    "checkpoint",
    "artifacts",
    "payload",
    "response",
    "output",
    "state",
}
_TRANSITIONS = {
    "queued": {"running", "failed", "cancelled", "needs_attention"},
    "running": {"waiting_user", "cancel_requested", "succeeded", "failed", "cancelled", "needs_attention"},
    "waiting_user": {"queued", "running", "cancelled", "failed", "needs_attention"},
    "cancel_requested": {"cancelled", "failed", "needs_attention"},
    "needs_attention": {"failed"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}


class RunBudget(BaseModel):
    """Serializable limits and counters carried by a durable agent Run."""

    model_config = ConfigDict(extra="forbid")

    max_model_turns: int = Field(default=8, ge=1)
    max_wall_seconds: float = Field(default=300.0, gt=0)
    max_tokens: int | None = Field(default=64_000, ge=1)
    max_cost_usd: float | None = Field(default=5.0, ge=0)
    model_turns: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)


class AgentRunState(BaseModel):
    """Versioned workflow checkpoint; every field must remain JSON serializable."""

    model_config = ConfigDict(extra="forbid")

    workflow_type: str = "converse"
    workflow_version: int = Field(default=1, ge=1)
    session_id: str | None = None
    phase: str = "route"
    attempt: int = Field(default=1, ge=1)
    intent: dict[str, Any] | None = None
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    budget: RunBudget = Field(default_factory=RunBudget)
    artifact_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    pending_interaction_id: str | None = None
    context_summary_ref: str | None = None
    last_error: dict[str, Any] | None = None


class PendingRunEvent(BaseModel):
    """Event payload committed with the reducer checkpoint, then projected."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    payload: Any
    project_to_chat: bool = True


class StepOutcome(BaseModel):
    """Base class for the tagged result of one reducer step."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    summary: str = ""
    output: Any = None
    events: list[PendingRunEvent] = Field(default_factory=list)


class Continue(StepOutcome):
    kind: Literal["continue"] = "continue"
    next_phase: str | None = None


class Wait(StepOutcome):
    kind: Literal["wait"] = "wait"
    interaction_id: str
    interaction_type: str | None = None
    interaction_prompt: str | None = None
    interaction_payload: Any = None


class Succeeded(StepOutcome):
    kind: Literal["succeeded"] = "succeeded"
    result: Any = None
    artifacts: list[Any] = Field(default_factory=list)


class Failed(StepOutcome):
    kind: Literal["failed"] = "failed"
    error_code: str
    message: str
    retryable: bool = False
    effect_state: Literal["none", "committed", "unknown"] = "none"


class Cancelled(StepOutcome):
    kind: Literal["cancelled"] = "cancelled"
    effect_state: Literal["none", "committed", "unknown"] = "none"


StepOutcomeValue = Continue | Wait | Succeeded | Failed | Cancelled


class StaleLeaseError(RuntimeError):
    """Raised when an old worker attempts to commit after losing its lease."""


class RunVersionConflict(ValueError):
    """Raised when a command targets an obsolete durable Run version."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


_EVENT_MAX_STRING_BYTES = 64 * 1024
_EVENT_MAX_COLLECTION_ITEMS = 256
_EVENT_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def _sanitize_event_value(value: Any, *, key: str | None = None, depth: int = 0) -> tuple[Any, bool]:
    """Bound event payloads and redact conventional secret-bearing fields."""

    normalized_key = (key or "").lower().replace("-", "_")
    if normalized_key in _EVENT_SENSITIVE_KEYS or normalized_key.endswith(("_password", "_secret", "_token")):
        return "[REDACTED]", True
    if depth >= 12:
        return "[TRUNCATED: maximum event depth]", True
    if isinstance(value, dict):
        items = list(value.items())
        truncated = len(items) > _EVENT_MAX_COLLECTION_ITEMS
        sanitized: dict[str, Any] = {}
        for raw_key, item in items[:_EVENT_MAX_COLLECTION_ITEMS]:
            item_key = str(raw_key)
            sanitized_value, changed = _sanitize_event_value(item, key=item_key, depth=depth + 1)
            sanitized[item_key] = sanitized_value
            truncated = truncated or changed
        if len(items) > _EVENT_MAX_COLLECTION_ITEMS:
            sanitized["_truncated_items"] = len(items) - _EVENT_MAX_COLLECTION_ITEMS
        return sanitized, truncated
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        truncated = len(items) > _EVENT_MAX_COLLECTION_ITEMS
        sanitized_items: list[Any] = []
        for item in items[:_EVENT_MAX_COLLECTION_ITEMS]:
            sanitized, changed = _sanitize_event_value(item, depth=depth + 1)
            sanitized_items.append(sanitized)
            truncated = truncated or changed
        if len(items) > _EVENT_MAX_COLLECTION_ITEMS:
            sanitized_items.append({"_truncated_items": len(items) - _EVENT_MAX_COLLECTION_ITEMS})
        return sanitized_items, truncated
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) <= _EVENT_MAX_STRING_BYTES:
            return value, False
        suffix = f"\n...[TRUNCATED: original_bytes={len(encoded)}]"
        limit = max(0, _EVENT_MAX_STRING_BYTES - len(suffix.encode("utf-8")))
        return encoded[:limit].decode("utf-8", errors="ignore") + suffix, True
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    return str(value), True


def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in list(data):
        if key.endswith("_json"):
            plain = key[:-5]
            raw = data.pop(key)
            try:
                data[plain] = json.loads(raw) if raw is not None else None
            except (TypeError, json.JSONDecodeError):
                data[plain] = None
    return data


def _discard_staged_app_from_state(state: dict[str, Any]) -> str | None:
    """Delete one retained staging directory and remove its durable handle.

    The OpenCode boundary validates that the directory is a recognized sibling
    of the declared live App before deleting it.  A legacy-promoted handle is
    deliberately rejected: it may already represent a live external effect
    and therefore requires reconciliation rather than staging cleanup.
    """

    data = state.get("data")
    if not isinstance(data, dict) or not data.get("staged_app"):
        return None
    staged = data["staged_app"]
    if not isinstance(staged, dict):
        raise ValueError("staged_app checkpoint must be an object")
    if staged.get("legacy_promoted"):
        raise ValueError("legacy-promoted App cannot be discarded as staging")

    app_id = str(staged.get("app_id") or "")
    staging_dir = Path(str(staged.get("staging_dir") or ""))
    live_dir = Path(str(staged.get("live_dir") or ""))
    if staging_dir.exists() or staging_dir.is_symlink():
        from backend.opencode_service import OpenCodeStagedResult, discard_opencode_staging

        discard_opencode_staging(
            OpenCodeStagedResult(
                output=str(staged.get("output") or ""),
                app_id=app_id,
                staging_dir=staging_dir,
                live_dir=live_dir,
            )
        )
    if staging_dir.exists() or staging_dir.is_symlink():
        raise OSError("staged App still exists after discard")
    data.pop("staged_app", None)
    return app_id


def _queue_staged_app_cleanup(state: dict[str, Any]) -> str | None:
    """Replace a live staging handle with a durable cleanup tombstone."""

    data = state.get("data")
    if not isinstance(data, dict) or not data.get("staged_app"):
        return None
    staged = data.get("staged_app")
    if not isinstance(staged, dict):
        raise ValueError("staged_app checkpoint must be an object")
    if staged.get("legacy_promoted"):
        raise ValueError("legacy-promoted App cannot be discarded as staging")
    app_id = str(staged.get("app_id") or "")
    data["staged_app_cleanup_pending"] = data.pop("staged_app")
    return app_id


def _discard_pending_staged_app(state: dict[str, Any]) -> str | None:
    """Idempotently execute a previously persisted staging cleanup tombstone."""

    data = state.get("data")
    if not isinstance(data, dict) or not data.get("staged_app_cleanup_pending"):
        return None
    pending = data.get("staged_app_cleanup_pending")
    temporary = {"data": {"staged_app": pending}}
    app_id = _discard_staged_app_from_state(temporary)
    data.pop("staged_app_cleanup_pending", None)
    return app_id


def _queue_staged_app_cleanup_in_checkpoint(raw_checkpoint: str | None) -> str | None:
    """Persist the same cleanup tombstone in the serialized checkpoint."""

    if not raw_checkpoint:
        return raw_checkpoint
    try:
        checkpoint = json.loads(raw_checkpoint)
    except (TypeError, json.JSONDecodeError):
        return raw_checkpoint
    if not isinstance(checkpoint, dict):
        return raw_checkpoint
    state = checkpoint.get("state")
    if isinstance(state, dict):
        _queue_staged_app_cleanup(state)
    return _json(checkpoint)


def _clear_staged_app_cleanup_from_checkpoint(raw_checkpoint: str | None) -> str | None:
    """Remove staging and cleanup handles after idempotent deletion succeeds."""

    if not raw_checkpoint:
        return raw_checkpoint
    try:
        checkpoint = json.loads(raw_checkpoint)
    except (TypeError, json.JSONDecodeError):
        return raw_checkpoint
    if not isinstance(checkpoint, dict):
        return raw_checkpoint
    state = checkpoint.get("state")
    if isinstance(state, dict) and isinstance(state.get("data"), dict):
        state["data"].pop("staged_app", None)
        state["data"].pop("staged_app_cleanup_pending", None)
    return _json(checkpoint)


def _state_has_unresolved_effect(data: dict[str, Any]) -> bool:
    return bool(
        data.get("effect_state_unreadable")
        or data.get("non_compensable_effect")
        or data.get("effects_committed")
        or data.get("effect_in_flight")
        or data.get("graph_compensations")
    )


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "input") -> None:
    """Validate the useful JSON-Schema subset used by generated action forms."""

    if not schema:
        return
    expected = schema.get("type")
    types = expected if isinstance(expected, list) else [expected] if expected else []
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if types and not any(checks.get(kind, lambda _item: True)(value) for kind in types):
        raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                raise ValueError(f"{path}.{required} is required")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate_json_schema(item, properties[key], f"{path}.{key}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate_json_schema(item, schema["items"], f"{path}[{index}]")


class RunStore:
    def __init__(self, workspace_dir: str):
        self.workspace_dir = str(Path(workspace_dir).resolve())
        state_dir = Path(self.workspace_dir) / ".ambient"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = state_dir / "runs.db"
        self._initialize()
        # A process may have stopped after committing a cleanup tombstone but
        # before deleting/finalizing its staging artifact. Recovery is
        # idempotent and runs before any scheduler can claim work.
        self.recover_pending_staging_cleanup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    action_title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    adapter_type TEXT NOT NULL,
                    runtime_id TEXT NOT NULL,
                    tool_name TEXT,
                    input_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    result_json TEXT,
                    error_json TEXT,
                    checkpoint_json TEXT,
                    artifacts_json TEXT NOT NULL DEFAULT '[]',
                    state_json TEXT,
                    workflow_type TEXT NOT NULL DEFAULT 'legacy',
                    workflow_version INTEGER NOT NULL DEFAULT 1,
                    recovery TEXT NOT NULL DEFAULT 'manual',
                    parent_run_id TEXT,
                    retry_of TEXT,
                    idempotency_key TEXT,
                    correlation_json TEXT,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    lease_epoch INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(parent_run_id) REFERENCES runs(id),
                    FOREIGN KEY(retry_of) REFERENCES runs(id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_idempotency
                    ON runs(owner_id, action_id, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_runs_runtime_status ON runs(runtime_id, status);

                CREATE TABLE IF NOT EXISTS run_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    stream_epoch TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    session_id TEXT,
                    step_id TEXT,
                    attempt INTEGER,
                    trace_id TEXT,
                    duration_ms REAL,
                    model_usage_json TEXT,
                    redacted INTEGER NOT NULL DEFAULT 0,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id, sequence);

                CREATE TABLE IF NOT EXISTS run_store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_interactions (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    run_version INTEGER,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_run_interactions_run ON run_interactions(run_id, status);

                CREATE TABLE IF NOT EXISTS run_steps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    step_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    output_json TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(run_id, step_key, attempt),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
            run_migrations = {
                "artifacts_json": "TEXT NOT NULL DEFAULT '[]'",
                "state_json": "TEXT",
                "workflow_type": "TEXT NOT NULL DEFAULT 'legacy'",
                "workflow_version": "INTEGER NOT NULL DEFAULT 1",
                "version": "INTEGER NOT NULL DEFAULT 1",
                "lease_epoch": "INTEGER NOT NULL DEFAULT 0",
                "correlation_json": "TEXT",
            }
            for column, declaration in run_migrations.items():
                if column not in columns:
                    connection.execute(f"ALTER TABLE runs ADD COLUMN {column} {declaration}")

            interaction_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(run_interactions)").fetchall()
            }
            if "run_version" not in interaction_columns:
                connection.execute("ALTER TABLE run_interactions ADD COLUMN run_version INTEGER")

            connection.execute(
                "INSERT OR IGNORE INTO run_store_meta(key, value) VALUES ('stream_epoch', ?)",
                (str(uuid.uuid4()),),
            )
            stream_epoch = connection.execute(
                "SELECT value FROM run_store_meta WHERE key='stream_epoch'"
            ).fetchone()["value"]
            event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(run_events)").fetchall()}
            event_migrations = {
                "event_id": "TEXT",
                "schema_version": "INTEGER NOT NULL DEFAULT 1",
                "stream_epoch": "TEXT",
                "session_id": "TEXT",
                "step_id": "TEXT",
                "attempt": "INTEGER",
                "trace_id": "TEXT",
                "duration_ms": "REAL",
                "model_usage_json": "TEXT",
                "redacted": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, declaration in event_migrations.items():
                if column not in event_columns:
                    connection.execute(f"ALTER TABLE run_events ADD COLUMN {column} {declaration}")
            legacy_events = connection.execute(
                "SELECT sequence FROM run_events WHERE event_id IS NULL OR stream_epoch IS NULL"
            ).fetchall()
            for event in legacy_events:
                connection.execute(
                    "UPDATE run_events SET event_id=COALESCE(event_id, ?), stream_epoch=COALESCE(stream_epoch, ?) WHERE sequence=?",
                    (str(uuid.uuid4()), stream_epoch, event["sequence"]),
                )
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_event_id ON run_events(event_id)")

            # SQLite cannot drop the legacy UNIQUE(run_id, step_key) constraint.
            # Rebuild once so every reducer attempt has its own immutable row.
            step_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='run_steps'"
            ).fetchone()
            normalized_step_sql = "".join((step_sql_row["sql"] if step_sql_row else "").lower().split())
            if "unique(run_id,step_key,attempt)" not in normalized_step_sql:
                connection.executescript(
                    """
                    ALTER TABLE run_steps RENAME TO run_steps_v1;
                    CREATE TABLE run_steps (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        step_key TEXT NOT NULL,
                        status TEXT NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 1,
                        output_json TEXT,
                        started_at TEXT,
                        finished_at TEXT,
                        UNIQUE(run_id, step_key, attempt),
                        FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                    );
                    INSERT INTO run_steps(id, run_id, step_key, status, attempt, output_json, started_at, finished_at)
                    SELECT id, run_id, step_key, status, attempt, output_json, started_at, finished_at
                    FROM run_steps_v1;
                    DROP TABLE run_steps_v1;
                    """
                )

            # A v1 in-memory call stack cannot be reconstructed honestly. A
            # never-started chat command is the sole safe exception: its full
            # input can be replayed from the v2 route phase. Run this audit on
            # every process start because compatibility callers may have
            # created a v1 Run after the schema migration.
            active_legacy = connection.execute(
                """SELECT * FROM runs
                   WHERE adapter_type IN ('internal','internal_agent') AND workflow_version=1
                     AND status IN ('queued','running','waiting_user','cancel_requested')"""
            ).fetchall()
            for row in active_legacy:
                replay_state: AgentRunState | None = None
                if (
                    row["status"] == "queued"
                    and row["runtime_id"] == "internal:agent"
                    and row["action_id"] == "chat"
                    and row["source_type"] == "chat"
                    and isinstance(row["source_id"], str)
                    and row["source_id"]
                ):
                    try:
                        legacy_input = json.loads(row["input_json"])
                        legacy_state = json.loads(row["state_json"]) if row["state_json"] else {}
                    except (TypeError, json.JSONDecodeError):
                        legacy_input = None
                        legacy_state = {}
                    if (
                        isinstance(legacy_input, dict)
                        and isinstance(legacy_input.get("content"), str)
                        and legacy_input["content"].strip()
                        and isinstance(legacy_state, dict)
                    ):
                        legacy_data = (
                            legacy_state.get("data") if isinstance(legacy_state.get("data"), dict) else {}
                        )
                        replay_data: dict[str, Any] = {"workspace_dir": self.workspace_dir}
                        for key in ("user_message_id", "language"):
                            if key in legacy_data:
                                replay_data[key] = legacy_data[key]
                            elif key in legacy_input:
                                replay_data[key] = legacy_input[key]
                        replay_state = AgentRunState(
                            workflow_type="agent_chat",
                            workflow_version=CURRENT_AGENT_WORKFLOW_VERSION,
                            session_id=row["source_id"],
                            phase="route",
                            model_snapshot=(
                                legacy_state.get("model_snapshot")
                                if isinstance(legacy_state.get("model_snapshot"), dict)
                                else {}
                            ),
                            data=replay_data,
                            context_summary_ref=(
                                legacy_state.get("context_summary_ref")
                                if isinstance(legacy_state.get("context_summary_ref"), str)
                                else None
                            ),
                        )
                if replay_state is not None:
                    connection.execute(
                        """UPDATE runs SET adapter_type='internal_agent', recovery='restart_safe',
                           state_json=?, workflow_type='agent_chat', workflow_version=?, summary=?,
                           lease_owner=NULL, lease_expires_at=NULL, updated_at=?, version=version+1
                           WHERE id=?""",
                        (
                            _json(replay_state.model_dump(mode="json")),
                            CURRENT_AGENT_WORKFLOW_VERSION,
                            "Migrated queued chat command to durable workflow",
                            _now(),
                            row["id"],
                        ),
                    )
                    self._append_event(
                        connection,
                        row["id"],
                        "migration_replayable_upgraded",
                        {
                            "from_workflow_version": 1,
                            "to_workflow_version": CURRENT_AGENT_WORKFLOW_VERSION,
                            "phase": "route",
                        },
                    )
                    continue
                connection.execute(
                    """UPDATE runs SET status='needs_attention', summary=?, updated_at=?,
                       lease_owner=NULL, lease_expires_at=NULL, version=version+1
                       WHERE id=?""",
                    ("Legacy in-memory run cannot be resumed after upgrade", _now(), row["id"]),
                )
                self._append_event(
                    connection,
                    row["id"],
                    "migration_attention_required",
                    {
                        "from": row["status"],
                        "to": "needs_attention",
                        "workflow_version": 1,
                        "reason": "legacy_state_not_replayable",
                    },
                )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: Any,
        *,
        step_id_override: str | None = None,
        attempt_override: int | None = None,
        trace_id_override: str | None = None,
    ) -> int:
        run = connection.execute(
            "SELECT source_type, source_id, state_json FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        state: dict[str, Any] = {}
        if run and run["state_json"]:
            try:
                decoded = json.loads(run["state_json"])
                state = decoded if isinstance(decoded, dict) else {}
            except (TypeError, json.JSONDecodeError):
                pass
        session_id = state.get("session_id") or (
            run["source_id"] if run and run["source_type"] == "chat" else None
        )
        state_data = state.get("data") if isinstance(state.get("data"), dict) else {}
        trace_id = trace_id_override or state_data.get("trace_id") or run_id
        payload_dict = payload if isinstance(payload, dict) else {}
        step_id = step_id_override or payload_dict.get(
            "step_id", payload_dict.get("step_key", state.get("phase"))
        )
        attempt = attempt_override or payload_dict.get("attempt", state.get("attempt"))
        duration_ms = payload_dict.get("duration_ms")
        if not isinstance(duration_ms, (int, float)) or isinstance(duration_ms, bool):
            duration_ms = None
        model_usage = payload_dict.get("model_usage")
        if not isinstance(model_usage, dict):
            budget = state.get("budget") if isinstance(state.get("budget"), dict) else {}
            model_usage = (
                {
                    "model_turns": int(budget.get("model_turns", 0) or 0),
                    "tokens": int(budget.get("tokens_used", 0) or 0),
                    "cost_usd": float(budget.get("cost_usd", 0.0) or 0.0),
                }
                if budget
                else None
            )
        sanitized_payload, redacted = _sanitize_event_value(payload)
        epoch_row = connection.execute(
            "SELECT value FROM run_store_meta WHERE key='stream_epoch'"
        ).fetchone()
        stream_epoch = epoch_row["value"] if epoch_row else "legacy"
        cursor = connection.execute(
            """INSERT INTO run_events(
                   event_id, schema_version, stream_epoch, run_id, session_id,
                   step_id, attempt, trace_id, duration_ms, model_usage_json,
                   redacted, type, payload_json, created_at
               ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                stream_epoch,
                run_id,
                session_id,
                step_id,
                attempt,
                trace_id,
                duration_ms,
                _json(model_usage) if model_usage is not None else None,
                int(redacted),
                event_type,
                _json(sanitized_payload),
                _now(),
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _session_id(row: sqlite3.Row) -> str | None:
        raw_state = row["state_json"] if "state_json" in row.keys() else None
        if raw_state:
            try:
                state = json.loads(raw_state)
                session_id = state.get("session_id") if isinstance(state, dict) else None
                if isinstance(session_id, str) and session_id:
                    return session_id
            except (TypeError, json.JSONDecodeError):
                pass
        if row["source_type"] == "chat" and row["source_id"]:
            return str(row["source_id"])
        return None

    @classmethod
    def _session_lane_available(cls, candidate: sqlite3.Row, active_rows: list[sqlite3.Row]) -> bool:
        session_id = cls._session_id(candidate)
        if not session_id:
            return True
        candidate_order = (candidate["created_at"], candidate["id"])
        for other in active_rows:
            if other["id"] == candidate["id"] or cls._session_id(other) != session_id:
                continue
            if other["status"] in {"running", "waiting_user", "cancel_requested", "needs_attention"}:
                return False
            if other["status"] == "queued" and (other["created_at"], other["id"]) < candidate_order:
                return False
        return True

    def create_run(
        self,
        *,
        owner_id: str,
        action_id: str,
        action_title: str,
        source_type: str,
        source_id: str | None,
        adapter_type: str,
        runtime_id: str,
        input_data: Any,
        tool_name: str | None = None,
        recovery: str = "manual",
        parent_run_id: str | None = None,
        retry_of: str | None = None,
        idempotency_key: str | None = None,
        correlation: dict[str, Any] | None = None,
        attempt: int = 1,
        status: str = "queued",
        state: AgentRunState | dict[str, Any] | None = None,
        workflow_type: str | None = None,
        workflow_version: int | None = None,
    ) -> dict[str, Any]:
        if correlation is not None and not isinstance(correlation, dict):
            raise ValueError("run correlation must be a JSON object")
        if idempotency_key:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM runs WHERE owner_id=? AND action_id=? AND idempotency_key=?",
                    (owner_id, action_id, idempotency_key),
                ).fetchone()
                if existing:
                    existing_input = json.loads(existing["input_json"])
                    existing_correlation = (
                        json.loads(existing["correlation_json"])
                        if "correlation_json" in existing.keys() and existing["correlation_json"]
                        else None
                    )
                    if existing_input != input_data or existing_correlation != correlation:
                        raise ValueError("run idempotency key was reused with different input or correlation")
                    return _decode_row(existing) or {}
        run_id = str(uuid.uuid4())
        now = _now()
        started_at = now if status == "running" else None
        normalized_state: AgentRunState | None = None
        if state is not None:
            normalized_state = state if isinstance(state, AgentRunState) else AgentRunState.model_validate(state)
            if workflow_type is not None and normalized_state.workflow_type != workflow_type:
                raise ValueError("workflow_type must match state.workflow_type")
            if workflow_version is not None and normalized_state.workflow_version != workflow_version:
                raise ValueError("workflow_version must match state.workflow_version")
        resolved_workflow_type = workflow_type or (
            normalized_state.workflow_type if normalized_state is not None else "legacy"
        )
        resolved_workflow_version = workflow_version or (
            normalized_state.workflow_version if normalized_state is not None else 1
        )
        state_json = normalized_state.model_dump(mode="json") if normalized_state is not None else None
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                INSERT INTO runs(
                    id, owner_id, action_id, action_title, source_type, source_id,
                    adapter_type, runtime_id, tool_name, input_json, status,
                    state_json, workflow_type, workflow_version,
                    recovery, parent_run_id, retry_of, idempotency_key, correlation_json, attempt,
                    created_at, updated_at, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        run_id,
                        owner_id,
                        action_id,
                        action_title,
                        source_type,
                        source_id,
                        adapter_type,
                        runtime_id,
                        tool_name,
                        _json(input_data),
                        status,
                        _json(state_json) if state_json is not None else None,
                        resolved_workflow_type,
                        resolved_workflow_version,
                        recovery,
                        parent_run_id,
                        retry_of,
                        idempotency_key,
                        _json(correlation) if correlation is not None else None,
                        attempt,
                        now,
                        now,
                        started_at,
                    ),
                )
                created_payload: dict[str, Any] = {"status": status}
                if correlation is not None:
                    created_payload["correlation"] = correlation
                self._append_event(connection, run_id, "run_created", created_payload)
        except sqlite3.IntegrityError:
            if not idempotency_key:
                raise
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM runs WHERE owner_id=? AND action_id=? AND idempotency_key=?",
                    (owner_id, action_id, idempotency_key),
                ).fetchone()
            if existing:
                existing_input = json.loads(existing["input_json"])
                existing_correlation = (
                    json.loads(existing["correlation_json"])
                    if "correlation_json" in existing.keys() and existing["correlation_json"]
                    else None
                )
                if existing_input != input_data or existing_correlation != correlation:
                    raise ValueError("run idempotency key was reused with different input or correlation")
                return _decode_row(existing) or {}
            raise
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str, *, include_events: bool = False) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = _decode_row(connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            if run is None:
                return None
            run["interactions"] = [
                _decode_row(row)
                for row in connection.execute(
                    "SELECT * FROM run_interactions WHERE run_id=? ORDER BY created_at", (run_id,)
                ).fetchall()
            ]
            run["steps"] = [
                _decode_row(row)
                for row in connection.execute(
                    "SELECT * FROM run_steps WHERE run_id=? ORDER BY id", (run_id,)
                ).fetchall()
            ]
            if include_events:
                run["events"] = [
                    _decode_row(row)
                    for row in connection.execute(
                        "SELECT * FROM run_events WHERE run_id=? ORDER BY sequence", (run_id,)
                    ).fetchall()
                ]
            return run

    def list_runs(
        self, *, status: str | None = None, owner_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            statuses = [item.strip() for item in status.split(",") if item.strip()]
            where.append(f"status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if owner_id:
            where.append("owner_id=?")
            params.append(owner_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([max(1, min(limit, 500)), max(0, offset)])
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM runs {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?", params
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def events_after(self, sequence: int, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE sequence>? ORDER BY sequence LIMIT ?", (sequence, limit)
            ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def stream_info(self) -> dict[str, Any]:
        """Return the durable identity and high-water mark for event replay."""

        with self._connect() as connection:
            epoch_row = connection.execute(
                "SELECT value FROM run_store_meta WHERE key='stream_epoch'"
            ).fetchone()
            latest = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM run_events"
            ).fetchone()
        return {
            "stream_epoch": epoch_row["value"] if epoch_row else "legacy",
            "latest_sequence": int(latest["sequence"] if latest else 0),
        }

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Any,
        *,
        expected_lease_owner: str | None = None,
        expected_lease_epoch: int | None = None,
    ) -> int:
        with self._connect() as connection:
            if expected_lease_owner is not None:
                connection.execute("BEGIN IMMEDIATE")
            if expected_lease_owner is not None:
                run = connection.execute(
                    "SELECT status, lease_owner, lease_epoch FROM runs WHERE id=?",
                    (run_id,),
                ).fetchone()
                if run is None:
                    raise KeyError(run_id)
                if (
                    run["status"] not in {"running", "cancel_requested"}
                    or run["lease_owner"] != expected_lease_owner
                    or int(run["lease_epoch"]) != expected_lease_epoch
                ):
                    raise StaleLeaseError(f"worker no longer owns active run {run_id}")
            return self._append_event(connection, run_id, event_type, payload)

    def transition(
        self,
        run_id: str,
        status: str,
        *,
        expected_lease_owner: str | None = None,
        expected_lease_epoch: int | None = None,
        expected_version: int | None = None,
        **updates: Any,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            if expected_version is not None and row["version"] != expected_version:
                raise RunVersionConflict(
                    f"run {run_id} version changed: expected {expected_version}, found {row['version']}"
                )
            if expected_lease_owner is not None and (
                row["lease_owner"] != expected_lease_owner or row["lease_epoch"] != expected_lease_epoch
            ):
                raise StaleLeaseError(f"worker no longer owns run {run_id}")
            current = row["status"]
            if status != current and status not in _TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid run transition: {current} -> {status}")
            now = _now()
            fields: dict[str, Any] = {"status": status, "updated_at": now}
            fields.update(updates)
            next_version = int(row["version"]) + 1
            fields["version"] = next_version
            if status == "running" and not row["started_at"]:
                fields["started_at"] = now
            if status in TERMINAL_STATUSES | {"queued", "needs_attention"} or (
                status == "waiting_user" and row["adapter_type"] != "internal"
            ):
                fields["lease_owner"] = None
                fields["lease_expires_at"] = None
            if status in TERMINAL_STATUSES:
                fields["finished_at"] = now
                if status == "succeeded":
                    fields.setdefault("progress", 1.0)
            encoded: dict[str, Any] = {}
            for key, value in fields.items():
                column = f"{key}_json" if key in _JSON_COLUMNS else key
                encoded[column] = _json(value) if key in _JSON_COLUMNS and value is not None else value
            assignments = ", ".join(f"{key}=?" for key in encoded)
            connection.execute(f"UPDATE runs SET {assignments} WHERE id=?", [*encoded.values(), run_id])
            if status == "waiting_user":
                connection.execute(
                    "UPDATE run_interactions SET run_version=? WHERE run_id=? AND status='pending'",
                    (next_version, run_id),
                )
                if row["adapter_type"] != "internal":
                    connection.execute(
                        """UPDATE run_steps SET status='interrupted', finished_at=?
                           WHERE run_id=? AND status='running'""",
                        (now, run_id),
                    )
            elif status in TERMINAL_STATUSES:
                connection.execute(
                    """UPDATE run_interactions SET status='cancelled', resolved_at=?
                       WHERE run_id=? AND status='pending'""",
                    (now, run_id),
                )
            self._append_event(connection, run_id, "status_changed", {"from": current, "to": status, **updates})
        return self.get_run(run_id) or {}

    def update_progress(
        self,
        run_id: str,
        progress: float,
        summary: str = "",
        *,
        expected_lease_owner: str | None = None,
        expected_lease_epoch: int | None = None,
    ) -> None:
        progress = max(0.0, min(1.0, progress))
        with self._connect() as connection:
            if expected_lease_owner is not None:
                connection.execute("BEGIN IMMEDIATE")
            if expected_lease_owner is not None:
                run = connection.execute(
                    "SELECT status, lease_owner, lease_epoch FROM runs WHERE id=?",
                    (run_id,),
                ).fetchone()
                if run is None:
                    raise KeyError(run_id)
                if (
                    run["status"] != "running"
                    or run["lease_owner"] != expected_lease_owner
                    or int(run["lease_epoch"]) != expected_lease_epoch
                ):
                    raise StaleLeaseError(f"worker no longer owns active run {run_id}")
            connection.execute(
                "UPDATE runs SET progress=?, summary=?, updated_at=? WHERE id=?",
                (progress, summary, _now(), run_id),
            )
            self._append_event(connection, run_id, "progress", {"progress": progress, "summary": summary})

    def claim_next(
        self,
        worker_id: str,
        global_limit: int,
        owner_limit: int,
        lease_seconds: int = 30,
        exclude_run_ids: set[str] | None = None,
    ) -> dict[str, Any] | None:
        excluded = exclude_run_ids or set()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            running_count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('running','cancel_requested')"
            ).fetchone()[0]
            if running_count >= global_limit:
                return None
            owner_counts = {
                row["owner_id"]: row["count"]
                for row in connection.execute(
                    "SELECT owner_id, COUNT(*) AS count FROM runs WHERE status IN ('running','cancel_requested') GROUP BY owner_id"
                ).fetchall()
            }
            candidates = connection.execute(
                "SELECT * FROM runs WHERE status='queued' AND adapter_type<>'internal' ORDER BY created_at, id LIMIT 100"
            ).fetchall()
            active_rows = connection.execute(
                """SELECT * FROM runs
                   WHERE status IN ('queued','running','waiting_user','cancel_requested','needs_attention')"""
            ).fetchall()
            row = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate["id"] not in excluded
                    and owner_counts.get(candidate["owner_id"], 0) < owner_limit
                    and self._session_lane_available(candidate, active_rows)
                ),
                None,
            )
            if row is None:
                return None
            now = _now()
            expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
            connection.execute(
                """UPDATE runs SET status='running', started_at=COALESCE(started_at, ?), updated_at=?,
                   lease_owner=?, lease_expires_at=?, lease_epoch=lease_epoch+1, version=version+1
                   WHERE id=? AND status='queued'""",
                (now, now, worker_id, expires, row["id"]),
            )
            claimed = connection.execute("SELECT lease_epoch, version FROM runs WHERE id=?", (row["id"],)).fetchone()
            self._append_event(
                connection,
                row["id"],
                "status_changed",
                {
                    "from": "queued",
                    "to": "running",
                    "lease_epoch": claimed["lease_epoch"],
                    "version": claimed["version"],
                },
            )
        return self.get_run(row["id"])

    def claim_specific(
        self, run_id: str, worker_id: str, global_limit: int, owner_limit: int, lease_seconds: int = 30
    ) -> dict[str, Any] | None:
        """Claim an externally executed Run while sharing the same limits."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE id=? AND status='queued'", (run_id,)).fetchone()
            if row is None:
                return None
            active_rows = connection.execute(
                """SELECT * FROM runs
                   WHERE status IN ('queued','running','waiting_user','cancel_requested','needs_attention')"""
            ).fetchall()
            if not self._session_lane_available(row, active_rows):
                return None
            running_count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('running','cancel_requested')"
            ).fetchone()[0]
            owner_count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE owner_id=? AND status IN ('running','cancel_requested')",
                (row["owner_id"],),
            ).fetchone()[0]
            if running_count >= global_limit or owner_count >= owner_limit:
                return None
            now = _now()
            expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
            connection.execute(
                """UPDATE runs SET status='running', started_at=COALESCE(started_at, ?), updated_at=?,
                   lease_owner=?, lease_expires_at=?, lease_epoch=lease_epoch+1, version=version+1
                   WHERE id=? AND status='queued'""",
                (now, now, worker_id, expires, run_id),
            )
            claimed = connection.execute("SELECT lease_epoch, version FROM runs WHERE id=?", (run_id,)).fetchone()
            self._append_event(
                connection,
                run_id,
                "status_changed",
                {
                    "from": "queued",
                    "to": "running",
                    "lease_epoch": claimed["lease_epoch"],
                    "version": claimed["version"],
                },
            )
        return self.get_run(run_id)

    def heartbeat(self, worker_id: str, lease_seconds: int = 30, run_ids: set[str] | None = None) -> None:
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            if run_ids is None:
                connection.execute(
                    """UPDATE runs SET lease_expires_at=?
                       WHERE lease_owner=? AND status IN ('running','cancel_requested')""",
                    (expires, worker_id),
                )
            elif run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                connection.execute(
                    f"""UPDATE runs SET lease_expires_at=?
                        WHERE lease_owner=? AND status IN ('running','cancel_requested')
                          AND id IN ({placeholders})""",
                    (expires, worker_id, *sorted(run_ids)),
                )

    def finalize_cancel_requested(self, run_id: str, worker_id: str) -> dict[str, Any]:
        """Finish cancellation after the owning task exits, including pre-start races."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["status"] != "cancel_requested" or run["lease_owner"] != worker_id:
                connection.rollback()
                return self.get_run(run_id) or {}
            running_step = connection.execute(
                "SELECT 1 FROM run_steps WHERE run_id=? AND status='running' LIMIT 1",
                (run_id,),
            ).fetchone()
            # A cancellation is safe only when the worker acknowledges it via
            # a fenced Cancelled outcome.  If the task disappeared while a
            # step was active, restart-safe replay says nothing about whether
            # that in-flight effect committed, so manual review is required.
            target = "needs_attention" if running_step else "cancelled"
            now = _now()
            connection.execute(
                """UPDATE runs SET status=?, summary=?, updated_at=?, finished_at=?,
                   lease_owner=NULL, lease_expires_at=NULL, version=version+1 WHERE id=?""",
                (
                    target,
                    "Cancellation outcome requires review" if target == "needs_attention" else "Cancelled",
                    now,
                    now if target == "cancelled" else None,
                    run_id,
                ),
            )
            connection.execute(
                """UPDATE run_steps SET status=?, finished_at=? WHERE run_id=? AND status='running'""",
                ("interrupted" if target == "needs_attention" else "cancelled", now, run_id),
            )
            connection.execute(
                """UPDATE run_interactions SET status='cancelled', resolved_at=?
                   WHERE run_id=? AND status='pending'""",
                (now, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "cancellation_completed",
                {"status": target, "effect_state": "unknown" if target == "needs_attention" else "none"},
            )
        return self.get_run(run_id) or {}

    def recover_orphaned(self, worker_id: str | None = None) -> int:
        self.recover_pending_staging_cleanup()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT id, status, recovery FROM runs
                   WHERE status IN ('running','cancel_requested')
                     AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at<?)""",
                (_now(),),
            ).fetchall()
            for row in rows:
                if row["status"] == "cancel_requested":
                    running_step = connection.execute(
                        "SELECT 1 FROM run_steps WHERE run_id=? AND status='running' LIMIT 1",
                        (row["id"],),
                    ).fetchone()
                    target = "needs_attention" if running_step else "cancelled"
                elif row["recovery"] == "restart_safe":
                    target = "queued"
                else:
                    target = "needs_attention"
                finished = _now() if target == "cancelled" else None
                connection.execute(
                    """UPDATE runs SET status=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=?,
                       finished_at=COALESCE(finished_at, ?), version=version+1 WHERE id=?""",
                    (target, _now(), finished, row["id"]),
                )
                connection.execute(
                    """UPDATE run_steps SET status='interrupted', finished_at=?
                       WHERE run_id=? AND status='running'""",
                    (_now(), row["id"]),
                )
                if target in {"cancelled", "needs_attention"}:
                    connection.execute(
                        """UPDATE run_interactions SET status='cancelled', resolved_at=?
                           WHERE run_id=? AND status='pending'""",
                        (_now(), row["id"]),
                    )
                self._append_event(connection, row["id"], "recovered", {"status": target})
            return len(rows)

    def release_worker(self, worker_id: str) -> int:
        """Release leases synchronously during graceful process shutdown."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT id, status, recovery FROM runs
                   WHERE lease_owner=? AND status IN ('running','cancel_requested')""",
                (worker_id,),
            ).fetchall()
            for row in rows:
                if row["status"] == "cancel_requested":
                    running_step = connection.execute(
                        "SELECT 1 FROM run_steps WHERE run_id=? AND status='running' LIMIT 1",
                        (row["id"],),
                    ).fetchone()
                    target = "needs_attention" if running_step else "cancelled"
                elif row["recovery"] == "restart_safe":
                    target = "queued"
                else:
                    target = "needs_attention"
                finished = _now() if target == "cancelled" else None
                connection.execute(
                    """UPDATE runs SET status=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=?,
                       finished_at=COALESCE(finished_at, ?), version=version+1 WHERE id=?""",
                    (target, _now(), finished, row["id"]),
                )
                connection.execute(
                    """UPDATE run_steps SET status='interrupted', finished_at=?
                       WHERE run_id=? AND status='running'""",
                    (_now(), row["id"]),
                )
                if target in {"cancelled", "needs_attention"}:
                    connection.execute(
                        """UPDATE run_interactions SET status='cancelled', resolved_at=?
                           WHERE run_id=? AND status='pending'""",
                        (_now(), row["id"]),
                    )
                self._append_event(connection, row["id"], "lease_released", {"status": target})
            return len(rows)

    def begin_step_attempt(
        self,
        run_id: str,
        step_key: str,
        *,
        lease_owner: str | None = None,
        lease_epoch: int | None = None,
    ) -> int | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if lease_owner is not None and (
                run["lease_owner"] != lease_owner or run["lease_epoch"] != lease_epoch or run["status"] != "running"
            ):
                raise StaleLeaseError(f"worker no longer owns run {run_id}")
            row = connection.execute(
                """SELECT status, attempt FROM run_steps
                   WHERE run_id=? AND step_key=? ORDER BY attempt DESC LIMIT 1""",
                (run_id, step_key),
            ).fetchone()
            attempt = int(row["attempt"]) + 1 if row else 1
            connection.execute(
                """INSERT INTO run_steps(run_id, step_key, status, attempt, started_at)
                   VALUES (?, ?, 'running', ?, ?)""",
                (run_id, step_key, attempt, _now()),
            )
            self._append_event(
                connection,
                run_id,
                "step_started",
                {"step_key": step_key, "attempt": attempt, "lease_epoch": run["lease_epoch"]},
            )
            return attempt

    def begin_step(
        self,
        run_id: str,
        step_key: str,
        *,
        lease_owner: str | None = None,
        lease_epoch: int | None = None,
    ) -> bool:
        return (
            self.begin_step_attempt(
                run_id,
                step_key,
                lease_owner=lease_owner,
                lease_epoch=lease_epoch,
            )
            is not None
        )

    def finish_step(
        self,
        run_id: str,
        step_key: str,
        output: Any = None,
        status: str = "succeeded",
        *,
        attempt: int | None = None,
        lease_owner: str | None = None,
        lease_epoch: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["adapter_type"] == "internal_agent" and lease_owner is None:
                raise StaleLeaseError("durable agent steps require a fenced commit")
            if lease_owner is not None and (
                run["lease_owner"] != lease_owner or run["lease_epoch"] != lease_epoch or run["status"] != "running"
            ):
                raise StaleLeaseError(f"worker no longer owns run {run_id}")
            if attempt is None:
                row = connection.execute(
                    """SELECT attempt FROM run_steps WHERE run_id=? AND step_key=?
                       ORDER BY attempt DESC LIMIT 1""",
                    (run_id, step_key),
                ).fetchone()
                if row is None:
                    raise ValueError(f"step has not started: {step_key}")
                attempt = int(row["attempt"])
            connection.execute(
                """UPDATE run_steps SET status=?, output_json=?, finished_at=?
                   WHERE run_id=? AND step_key=? AND attempt=?""",
                (status, _json(output) if output is not None else None, _now(), run_id, step_key, attempt),
            )
            if status == "succeeded":
                connection.execute(
                    "UPDATE runs SET checkpoint_json=?, updated_at=? WHERE id=?",
                    (_json({"last_step": step_key, "attempt": attempt, "output": output}), _now(), run_id),
                )

    @staticmethod
    def _coerce_outcome(outcome: StepOutcomeValue | dict[str, Any]) -> StepOutcomeValue:
        if isinstance(outcome, (Continue, Wait, Succeeded, Failed, Cancelled)):
            return outcome
        if not isinstance(outcome, dict):
            raise TypeError("outcome must be a StepOutcome or mapping")
        model_by_kind: dict[str, type[StepOutcome]] = {
            "continue": Continue,
            "wait": Wait,
            "succeeded": Succeeded,
            "failed": Failed,
            "cancelled": Cancelled,
        }
        model = model_by_kind.get(str(outcome.get("kind")))
        if model is None:
            raise ValueError(f"unknown step outcome: {outcome.get('kind')}")
        return model.model_validate(outcome)  # type: ignore[return-value]

    def commit_step(
        self,
        run_id: str,
        step_key: str,
        *,
        attempt: int,
        lease_owner: str,
        lease_epoch: int,
        state: AgentRunState | dict[str, Any],
        outcome: StepOutcomeValue | dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically commit a reducer attempt, checkpoint, Run status and event.

        The lease epoch is a fencing token.  Once a Run is cancelled, recovered,
        or claimed by another worker, a late callback cannot publish state.
        """

        normalized_state = state if isinstance(state, AgentRunState) else AgentRunState.model_validate(state)
        normalized_outcome = self._coerce_outcome(outcome)
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            lease_matches = run["lease_owner"] == lease_owner and int(run["lease_epoch"]) == lease_epoch
            cancellation_ack = run["status"] == "cancel_requested" and isinstance(normalized_outcome, Cancelled)
            if not lease_matches or (run["status"] != "running" and not cancellation_ack):
                raise StaleLeaseError(f"worker no longer owns active run {run_id}")
            step = connection.execute(
                """SELECT status, started_at FROM run_steps
                   WHERE run_id=? AND step_key=? AND attempt=?""",
                (run_id, step_key, attempt),
            ).fetchone()
            if step is None or step["status"] != "running":
                raise StaleLeaseError(f"step attempt is no longer active: {step_key}#{attempt}")
            next_version = int(run["version"]) + 1

            if isinstance(normalized_outcome, Continue):
                target = "queued"
                if normalized_outcome.next_phase:
                    normalized_state.phase = normalized_outcome.next_phase
            elif isinstance(normalized_outcome, Wait):
                target = "waiting_user"
                normalized_state.pending_interaction_id = normalized_outcome.interaction_id
                interaction = connection.execute(
                    "SELECT * FROM run_interactions WHERE id=?",
                    (normalized_outcome.interaction_id,),
                ).fetchone()
                if interaction is None:
                    if not normalized_outcome.interaction_type or not normalized_outcome.interaction_prompt:
                        raise ValueError("wait outcome must include a durable interaction definition")
                    connection.execute(
                        """INSERT INTO run_interactions(
                               id, run_id, type, prompt, payload_json, status, created_at, run_version
                           ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                        (
                            normalized_outcome.interaction_id,
                            run_id,
                            normalized_outcome.interaction_type,
                            normalized_outcome.interaction_prompt,
                            _json(normalized_outcome.interaction_payload),
                            now,
                            next_version,
                        ),
                    )
                    self._append_event(
                        connection,
                        run_id,
                        "interaction_requested",
                        {
                            "interaction_id": normalized_outcome.interaction_id,
                            "type": normalized_outcome.interaction_type,
                        },
                        step_id_override=step_key,
                        attempt_override=attempt,
                    )
                elif interaction["run_id"] != run_id or interaction["status"] != "pending":
                    raise ValueError("wait outcome requires a pending interaction for this run")
            elif isinstance(normalized_outcome, Succeeded):
                target = "succeeded"
                normalized_state.pending_interaction_id = None
                # Keep the terminal checkpoint explicit and uniform across
                # workflow types while the committed step_key retains the
                # concrete phase (for example ``promote`` or ``graph_commit``).
                normalized_state.phase = "done"
            elif isinstance(normalized_outcome, Failed):
                target = (
                    "needs_attention"
                    if normalized_outcome.effect_state == "unknown"
                    else "queued"
                    if normalized_outcome.retryable
                    else "failed"
                )
                normalized_state.last_error = {
                    "code": normalized_outcome.error_code,
                    "message": normalized_outcome.message,
                    "retryable": normalized_outcome.retryable,
                    "effect_state": normalized_outcome.effect_state,
                }
            else:
                target = "needs_attention" if normalized_outcome.effect_state == "unknown" else "cancelled"

            pending_events = list(normalized_outcome.events)
            outcome_json = normalized_outcome.model_dump(mode="json")
            outcome_json.pop("events", None)
            state_json = normalized_state.model_dump(mode="json")
            duration_ms: float | None = None
            if step["started_at"]:
                try:
                    duration_ms = max(
                        0.0,
                        (datetime.fromisoformat(now) - datetime.fromisoformat(step["started_at"])).total_seconds()
                        * 1000,
                    )
                except (TypeError, ValueError):
                    duration_ms = None
            connection.execute(
                """UPDATE run_steps SET status=?, output_json=?, finished_at=?
                   WHERE run_id=? AND step_key=? AND attempt=? AND status='running'""",
                (
                    "failed"
                    if isinstance(normalized_outcome, Failed)
                    else "cancelled"
                    if isinstance(normalized_outcome, Cancelled)
                    else "succeeded",
                    _json(outcome_json),
                    now,
                    run_id,
                    step_key,
                    attempt,
                ),
            )
            fields: dict[str, Any] = {
                "status": target,
                "state_json": _json(state_json),
                "workflow_type": normalized_state.workflow_type,
                "workflow_version": normalized_state.workflow_version,
                "checkpoint_json": _json(
                    {"last_step": step_key, "attempt": attempt, "state": state_json, "outcome": outcome_json}
                ),
                "summary": normalized_outcome.summary,
                "updated_at": now,
                "lease_owner": None,
                "lease_expires_at": None,
                "version": next_version,
            }
            if isinstance(normalized_outcome, Succeeded):
                fields.update(
                    result_json=_json(normalized_outcome.result),
                    artifacts_json=_json(normalized_outcome.artifacts),
                    progress=1.0,
                    finished_at=now,
                )
            elif isinstance(normalized_outcome, Failed):
                fields["error_json"] = _json(normalized_state.last_error)
                if target == "failed":
                    fields["finished_at"] = now
            elif isinstance(normalized_outcome, Cancelled) and target == "cancelled":
                fields["finished_at"] = now
            assignments = ", ".join(f"{column}=?" for column in fields)
            connection.execute(
                f"UPDATE runs SET {assignments} WHERE id=?",
                [*fields.values(), run_id],
            )
            if target == "waiting_user":
                connection.execute(
                    "UPDATE run_interactions SET run_version=? WHERE id=?",
                    (next_version, normalized_outcome.interaction_id),
                )
            elif target in TERMINAL_STATUSES | {"needs_attention"}:
                connection.execute(
                    """UPDATE run_interactions SET status='cancelled', resolved_at=?
                       WHERE run_id=? AND status='pending'""",
                    (now, run_id),
                )
            for pending_event in pending_events:
                self._append_event(
                    connection,
                    run_id,
                    pending_event.type,
                    pending_event.payload,
                    step_id_override=step_key,
                    attempt_override=attempt,
                    trace_id_override=(
                        str(normalized_state.data.get("trace_id"))
                        if normalized_state.data.get("trace_id")
                        else run_id
                    ),
                )
            self._append_event(
                connection,
                run_id,
                "step_committed",
                {
                    "step_key": step_key,
                    "attempt": attempt,
                    "lease_epoch": lease_epoch,
                    "run_version": next_version,
                    "duration_ms": duration_ms,
                    "model_usage": {
                        "model_turns": normalized_state.budget.model_turns,
                        "tokens": normalized_state.budget.tokens_used,
                        "cost_usd": normalized_state.budget.cost_usd,
                    },
                    "outcome": outcome_json,
                },
            )
            self._append_event(
                connection,
                run_id,
                "status_changed",
                {"from": run["status"], "to": target, "version": next_version},
            )
        return self.get_run(run_id) or {}

    def create_interaction(
        self, run_id: str, interaction_type: str, prompt: str, payload: Any, interaction_id: str | None = None
    ) -> dict[str, Any]:
        interaction_id = interaction_id or str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM run_interactions WHERE id=?", (interaction_id,)).fetchone()
            if existing:
                return _decode_row(existing) or {}
            run = connection.execute("SELECT status, version FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["status"] in TERMINAL_STATUSES | {"needs_attention"}:
                raise ValueError("cannot create an interaction for an inactive run")
            connection.execute(
                """INSERT INTO run_interactions(
                       id, run_id, type, prompt, payload_json, status, created_at, run_version
                   ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (interaction_id, run_id, interaction_type, prompt, _json(payload), _now(), run["version"]),
            )
            self._append_event(
                connection,
                run_id,
                "interaction_requested",
                {"interaction_id": interaction_id, "type": interaction_type},
            )
        return self.get_interaction(interaction_id) or {}

    def wait_for_interaction(
        self,
        run_id: str,
        interaction_type: str,
        prompt: str,
        payload: Any,
        *,
        interaction_id: str,
        lease_owner: str,
        lease_epoch: int,
        summary: str,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        """Atomically persist an adapter interaction and release its worker."""

        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if (
                run["status"] != "running"
                or run["lease_owner"] != lease_owner
                or int(run["lease_epoch"]) != lease_epoch
            ):
                raise StaleLeaseError(f"worker no longer owns active run {run_id}")
            existing = connection.execute(
                "SELECT * FROM run_interactions WHERE id=?",
                (interaction_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError("interaction ID is already in use")
            next_version = int(run["version"]) + 1
            connection.execute(
                """INSERT INTO run_interactions(
                       id, run_id, type, prompt, payload_json, status, created_at, run_version
                   ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    interaction_id,
                    run_id,
                    interaction_type,
                    prompt,
                    _json(payload),
                    now,
                    next_version,
                ),
            )
            connection.execute(
                """UPDATE runs SET status='waiting_user', summary=?, updated_at=?, version=?,
                   lease_owner=NULL, lease_expires_at=NULL WHERE id=?""",
                (summary, now, next_version, run_id),
            )
            self._append_event(
                connection,
                run_id,
                "interaction_requested",
                {"interaction_id": interaction_id, "type": interaction_type},
            )
            if event_type:
                self._append_event(connection, run_id, event_type, payload)
            self._append_event(
                connection,
                run_id,
                "status_changed",
                {"from": "running", "to": "waiting_user", "version": next_version},
            )
        return self.get_run(run_id) or {}

    def get_interaction(self, interaction_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return _decode_row(
                connection.execute("SELECT * FROM run_interactions WHERE id=?", (interaction_id,)).fetchone()
            )

    def resolve_interaction(
        self,
        interaction_id: str,
        response: Any,
        *,
        expected_run_version: int | None = None,
        target_status: Literal["queued", "running", "failed", "cancelled"] | None = None,
        summary: str = "Interaction resolved",
        error: Any = None,
    ) -> dict[str, Any]:
        """Resolve an interaction and resume its Run in one SQLite transaction."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM run_interactions WHERE id=?", (interaction_id,)).fetchone()
            if not row:
                raise KeyError(interaction_id)
            if row["status"] != "pending":
                raise ValueError("interaction is already resolved")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (row["run_id"],)).fetchone()
            if run is None:
                raise KeyError(row["run_id"])
            if run["status"] != "waiting_user":
                raise ValueError(f"run is no longer waiting for interaction: {run['status']}")
            expected = expected_run_version if expected_run_version is not None else row["run_version"]
            if expected is not None and int(run["version"]) != int(expected):
                raise RunVersionConflict(
                    f"run {run['id']} version changed: expected {expected}, found {run['version']}"
                )
            resolved_target = target_status or ("running" if run["adapter_type"] == "internal" else "queued")
            if resolved_target not in _TRANSITIONS["waiting_user"]:
                raise ValueError(f"invalid interaction target status: {resolved_target}")
            now = _now()
            connection.execute(
                "UPDATE run_interactions SET status='resolved', response_json=?, resolved_at=? WHERE id=?",
                (_json(response), now, interaction_id),
            )
            connection.execute(
                """UPDATE run_interactions SET status='cancelled', resolved_at=?
                   WHERE run_id=? AND id<>? AND status='pending'""",
                (now, row["run_id"], interaction_id),
            )
            next_version = int(run["version"]) + 1
            updates: dict[str, Any] = {
                "status": resolved_target,
                "summary": summary,
                "updated_at": now,
                "version": next_version,
            }
            if resolved_target != "running":
                updates["lease_owner"] = None
                updates["lease_expires_at"] = None
            if resolved_target in TERMINAL_STATUSES:
                updates["finished_at"] = now
            if error is not None:
                updates["error_json"] = _json(error)
            assignments = ", ".join(f"{key}=?" for key in updates)
            connection.execute(
                f"UPDATE runs SET {assignments} WHERE id=?",
                [*updates.values(), row["run_id"]],
            )
            self._append_event(
                connection,
                row["run_id"],
                "interaction_resolved",
                {
                    "interaction_id": interaction_id,
                    "run_version": next_version,
                    "status": resolved_target,
                },
            )
            self._append_event(
                connection,
                row["run_id"],
                "status_changed",
                {"from": run["status"], "to": resolved_target, "version": next_version},
            )
        return self.get_interaction(interaction_id) or {}

    def recover_pending_staging_cleanup(self, run_id: str | None = None) -> int:
        """Finish staging deletion from a durable tombstone.

        Filesystem deletion intentionally occurs *after* the tombstone commit.
        A crash before deletion leaves a recoverable marker; a crash after
        deletion is also safe because ``discard_opencode_staging`` accepts an
        already-missing, otherwise valid staging path.
        """

        params: list[Any] = []
        run_filter = ""
        if run_id is not None:
            run_filter = " AND id=?"
            params.append(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT id, status, state_json, checkpoint_json, version, error_json
                    FROM runs
                    WHERE status IN ('cancel_requested','needs_attention','cancelled')
                      AND state_json IS NOT NULL{run_filter}""",
                params,
            ).fetchall()

        completed = 0
        for row in rows:
            try:
                state = json.loads(row["state_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict) or not isinstance(state.get("data"), dict):
                continue
            pending = state["data"].get("staged_app_cleanup_pending")
            if not isinstance(pending, dict):
                continue
            pending_identity = _json(pending)
            try:
                app_id = _discard_pending_staged_app(state)
            except Exception as exc:
                error = {
                    "code": "staged_artifact_cleanup_failed",
                    "message": str(exc),
                    "effect_state": "unknown",
                }
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    current = connection.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone()
                    if current is None:
                        continue
                    try:
                        current_state = json.loads(current["state_json"]) if current["state_json"] else {}
                    except (TypeError, json.JSONDecodeError):
                        current_state = {}
                    current_data = current_state.get("data") if isinstance(current_state, dict) else None
                    current_pending = current_data.get("staged_app_cleanup_pending") if isinstance(current_data, dict) else None
                    if not isinstance(current_pending, dict) or _json(current_pending) != pending_identity:
                        continue
                    existing_error = (
                        json.loads(current["error_json"])
                        if current["error_json"]
                        else None
                    )
                    if isinstance(current_state, dict):
                        current_state["last_error"] = error
                    if current["status"] == "needs_attention" and existing_error == error:
                        continue
                    now = _now()
                    next_version = int(current["version"]) + 1
                    connection.execute(
                        """UPDATE runs SET status='needs_attention', summary=?, error_json=?, state_json=?,
                           updated_at=?, finished_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                           version=? WHERE id=?""",
                        (
                            "Cancellation requires staged artifact cleanup",
                            _json(error),
                            _json(current_state),
                            now,
                            next_version,
                            row["id"],
                        ),
                    )
                    self._append_event(
                        connection,
                        row["id"],
                        "staged_artifact_cleanup_failed",
                        {"error": type(exc).__name__, "reason": "run_cancelled"},
                    )
                    if current["status"] != "needs_attention":
                        self._append_event(
                            connection,
                            row["id"],
                            "status_changed",
                            {
                                "from": current["status"],
                                "to": "needs_attention",
                                "effect_state": "unknown",
                                "version": next_version,
                            },
                        )
                continue

            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone()
                if current is None:
                    continue
                try:
                    current_state = json.loads(current["state_json"]) if current["state_json"] else {}
                except (TypeError, json.JSONDecodeError):
                    current_state = {}
                current_data = current_state.get("data") if isinstance(current_state, dict) else None
                current_pending = current_data.get("staged_app_cleanup_pending") if isinstance(current_data, dict) else None
                if not isinstance(current_pending, dict) or _json(current_pending) != pending_identity:
                    continue
                current_data.pop("staged_app_cleanup_pending", None)
                unresolved_effect = _state_has_unresolved_effect(current_data)
                target = "needs_attention" if unresolved_effect else "cancelled"
                now = _now()
                next_version = int(current["version"]) + 1
                connection.execute(
                    """UPDATE runs SET status=?, summary=?, error_json=?, state_json=?, checkpoint_json=?,
                       updated_at=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL,
                       version=? WHERE id=?""",
                    (
                        target,
                        "Cancellation requires effect reconciliation" if unresolved_effect else "Cancelled",
                        current["error_json"] if unresolved_effect else None,
                        _json(current_state),
                        _clear_staged_app_cleanup_from_checkpoint(current["checkpoint_json"]),
                        now,
                        None if unresolved_effect else now,
                        next_version,
                        row["id"],
                    ),
                )
                connection.execute(
                    """UPDATE run_interactions SET status='cancelled', resolved_at=?
                       WHERE run_id=? AND status='pending'""",
                    (now, row["id"]),
                )
                connection.execute(
                    """UPDATE run_steps SET status='cancelled', finished_at=?
                       WHERE run_id=? AND status='running'""",
                    (now, row["id"]),
                )
                self._append_event(
                    connection,
                    row["id"],
                    "staged_artifact_discarded",
                    {"app_id": app_id, "reason": "run_cancelled"},
                )
                if current["status"] != target:
                    self._append_event(
                        connection,
                        row["id"],
                        "status_changed",
                        {
                            "from": current["status"],
                            "to": target,
                            "effect_state": "unknown" if unresolved_effect else "none",
                            "version": next_version,
                        },
                    )
            completed += 1
        return completed

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        # A prior process may have committed the cleanup tombstone and stopped
        # before completing it. Retrying cancel first resumes that work.
        self.recover_pending_staging_cleanup(run_id)
        cleanup_queued = False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["status"] in TERMINAL_STATUSES:
                connection.rollback()
                return self.get_run(run_id) or {}
            if run["status"] == "needs_attention":
                raise ValueError(
                    "needs-attention runs require explicit effect reconciliation; "
                    "they cannot be marked cancelled"
                )
            if run["status"] == "cancel_requested":
                connection.rollback()
                return self.get_run(run_id) or {}
            raw_state: dict[str, Any] | None = None
            state_data: dict[str, Any] = {}
            if run["state_json"]:
                try:
                    decoded_state = json.loads(run["state_json"])
                    if isinstance(decoded_state, dict):
                        raw_state = decoded_state
                        if isinstance(raw_state.get("data"), dict):
                            state_data = raw_state["data"]
                    else:
                        state_data = {"effect_state_unreadable": True}
                except (TypeError, json.JSONDecodeError):
                    state_data = {"effect_state_unreadable": True}

            cleanup_app_id: str | None = None
            promoted_effect_detected = False
            staging_cleanup_error: str | None = None
            if run["status"] in {"queued", "waiting_user"} and state_data.get("staged_app"):
                try:
                    if raw_state is None:
                        raise ValueError("staged App has no readable durable state")
                    staged = state_data.get("staged_app")
                    if not isinstance(staged, dict):
                        raise ValueError("staged_app checkpoint must be an object")
                    from backend.opencode_service import (
                        OpenCodeStagedResult,
                        validate_opencode_promotion,
                    )

                    staged_result = OpenCodeStagedResult(
                        output=str(staged.get("output") or ""),
                        app_id=str(staged.get("app_id") or ""),
                        staging_dir=Path(str(staged.get("staging_dir") or "")),
                        live_dir=Path(str(staged.get("live_dir") or "")),
                    )
                    promoted_effect_detected = (
                        validate_opencode_promotion(staged_result, run_id) is not None
                    )
                    if promoted_effect_detected:
                        state_data["effects_committed"] = True
                        state_data["non_compensable_effect"] = True
                    else:
                        cleanup_app_id = _queue_staged_app_cleanup(raw_state)
                        cleanup_queued = cleanup_app_id is not None
                except Exception as exc:
                    # An unsafe or unreadable handle cannot be deleted or
                    # silently closed. It remains intact for manual review.
                    staging_cleanup_error = type(exc).__name__
            unresolved_effect = _state_has_unresolved_effect(state_data) or bool(staging_cleanup_error)
            target = (
                "cancel_requested"
                if run["status"] == "running"
                else "needs_attention"
                if unresolved_effect
                else "cancel_requested"
                if cleanup_queued
                else "cancelled"
            )
            attention_error: dict[str, Any] | None = None
            if target == "needs_attention":
                attention_error = {
                    "code": (
                        "cancel_after_artifact_promotion"
                        if promoted_effect_detected
                        else "staged_artifact_cleanup_failed"
                        if staging_cleanup_error
                        else "cancellation_effect_unknown"
                    ),
                    "message": (
                        "The App was already promoted before cancellation"
                        if promoted_effect_detected
                        else "Staged artifact cleanup could not be confirmed"
                        if staging_cleanup_error
                        else "Cancellation requires effect reconciliation"
                    ),
                    "effect_state": "committed" if promoted_effect_detected else "unknown",
                }
                if raw_state is not None:
                    raw_state["last_error"] = attention_error
            now = _now()
            updates: dict[str, Any] = {
                "status": target,
                "summary": (
                    "Cancellation requested"
                    if target == "cancel_requested" and not cleanup_queued
                    else "Cancelling staged artifact"
                    if cleanup_queued
                    else "Cancellation requires staged artifact cleanup"
                    if staging_cleanup_error
                    else "Cancellation requires effect reconciliation"
                    if target == "needs_attention"
                    else "Cancelled"
                ),
                "updated_at": now,
                "version": int(run["version"]) + 1,
            }
            if (cleanup_queued or promoted_effect_detected or attention_error is not None) and raw_state is not None:
                updates["state_json"] = _json(raw_state)
                if cleanup_queued and run["checkpoint_json"]:
                    updates["checkpoint_json"] = _queue_staged_app_cleanup_in_checkpoint(
                        run["checkpoint_json"]
                    )
            if attention_error is not None:
                updates["error_json"] = _json(attention_error)
            if target == "cancelled":
                updates.update(finished_at=now, lease_owner=None, lease_expires_at=None)
            assignments = ", ".join(f"{column}=?" for column in updates)
            connection.execute(f"UPDATE runs SET {assignments} WHERE id=?", [*updates.values(), run_id])
            cancelled_interactions = connection.execute(
                """UPDATE run_interactions SET status='cancelled', resolved_at=?
                   WHERE run_id=? AND status='pending'""",
                (now, run_id),
            ).rowcount
            if target == "cancelled":
                connection.execute(
                    """UPDATE run_steps SET status='cancelled', finished_at=?
                       WHERE run_id=? AND status='running'""",
                    (now, run_id),
                )
            if cleanup_queued:
                self._append_event(
                    connection,
                    run_id,
                    "staged_artifact_cleanup_requested",
                    {"app_id": cleanup_app_id, "reason": "run_cancelled"},
                )
            elif staging_cleanup_error:
                self._append_event(
                    connection,
                    run_id,
                    "staged_artifact_cleanup_failed",
                    {"error": staging_cleanup_error, "reason": "run_cancelled"},
                )
            self._append_event(
                connection,
                run_id,
                "status_changed",
                {
                    "from": run["status"],
                    "to": target,
                    "closed_interactions": cancelled_interactions,
                    "effect_state": "unknown" if target == "needs_attention" else "none",
                    "version": updates["version"],
                },
            )
        if cleanup_queued:
            self.recover_pending_staging_cleanup(run_id)
        return self.get_run(run_id) or {}

    def reconcile_effect(
        self,
        run_id: str,
        resolution: Literal["confirmed_not_committed", "compensated", "confirmed_committed"],
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an unknown-effect Run without inventing a cancellation outcome.

        Reconciliation is deliberately a durable command.  Confirming that no
        effect committed (or that it was compensated) makes a later explicit
        retry safe; confirming a committed effect closes review but keeps retry
        blocked so the action cannot be duplicated.
        """

        allowed = {"confirmed_not_committed", "compensated", "confirmed_committed"}
        if resolution not in allowed:
            raise ValueError(f"unsupported effect reconciliation: {resolution}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["status"] != "needs_attention":
                raise ValueError("only needs-attention runs can be reconciled")
            if run["state_json"]:
                try:
                    pending_state = json.loads(run["state_json"])
                except (TypeError, json.JSONDecodeError):
                    pending_state = {}
                pending_data = pending_state.get("data") if isinstance(pending_state, dict) else None
                cleanup_blocked = isinstance(pending_data, dict) and bool(
                    pending_data.get("staged_app_cleanup_pending")
                    or (
                        pending_data.get("staged_app")
                        and run["error_json"]
                        and (json.loads(run["error_json"]) or {}).get("code")
                        == "staged_artifact_cleanup_failed"
                    )
                )
                if cleanup_blocked:
                    raise ValueError("staged artifact cleanup must complete before effect reconciliation")
            effect_state = "committed" if resolution == "confirmed_committed" else "none"
            error = {
                "code": "effect_reconciled",
                "message": note or resolution.replace("_", " "),
                "effect_state": effect_state,
                "reconciliation": resolution,
            }
            state_json = run["state_json"]
            if state_json:
                try:
                    state = json.loads(state_json)
                    if isinstance(state, dict):
                        state["last_error"] = error
                        data = state.get("data")
                        if isinstance(data, dict):
                            data.pop("effect_in_flight", None)
                            if effect_state == "none":
                                data["effects_committed"] = False
                                data["non_compensable_effect"] = False
                                data["graph_compensations"] = []
                        state_json = _json(state)
                except (TypeError, json.JSONDecodeError):
                    state_json = run["state_json"]
            now = _now()
            next_version = int(run["version"]) + 1
            connection.execute(
                """UPDATE runs SET status='failed', summary=?, error_json=?, state_json=?,
                   updated_at=?, finished_at=?, lease_owner=NULL, lease_expires_at=NULL,
                   version=? WHERE id=?""",
                (
                    "Effect reconciliation completed",
                    _json(error),
                    state_json,
                    now,
                    now,
                    next_version,
                    run_id,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "effect_reconciled",
                {
                    "resolution": resolution,
                    "effect_state": effect_state,
                    "version": next_version,
                },
            )
            self._append_event(
                connection,
                run_id,
                "status_changed",
                {"from": "needs_attention", "to": "failed", "version": next_version},
            )
        return self.get_run(run_id) or {}

    def has_active_runtime(self, runtime_id: str) -> bool:
        with self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE runtime_id=? AND status IN ('queued','running','waiting_user','cancel_requested')",
                (runtime_id,),
            ).fetchone()[0]
        return bool(count)

    def has_active_owner(self, owner_id: str) -> bool:
        with self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE owner_id=? AND status IN ('queued','running','waiting_user','cancel_requested')",
                (owner_id,),
            ).fetchone()[0]
        return bool(count)

    def cleanup_events(self, days: int | None = None) -> int:
        if days is None:
            try:
                days = int(os.getenv("RUN_EVENT_RETENTION_DAYS", "30"))
            except ValueError:
                days = 30
        days = max(1, days)
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM run_events WHERE created_at<? AND run_id IN (SELECT id FROM runs WHERE status IN ('succeeded','failed','cancelled'))",
                (cutoff,),
            )
            return cursor.rowcount


class RunCoordinator:
    def __init__(self, store: RunStore, app_store: AppStoreService, app_manager: Any, backend_manager: Any):
        self.store = store
        self.app_store = app_store
        self.app_manager = app_manager
        self.backend_manager = backend_manager
        self.worker_id = f"worker-{uuid.uuid4()}"
        self.global_limit = max(1, int(os.getenv("RUNNER_MAX_CONCURRENCY", "4")))
        self.owner_limit = max(1, int(os.getenv("RUNNER_MAX_PER_APP", "1")))
        self._wake = asyncio.Event()
        self._scheduler: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._active: dict[str, asyncio.Task] = {}
        self._event_callbacks: dict[str, Any] = {}
        self._completion_callbacks: dict[str, Any] = {}
        self._internal_agent_executor: Any = None
        self._status_listeners: list[Any] = []

    async def start(self) -> None:
        self.ensure_started()

    def ensure_started(self) -> None:
        if self._scheduler is not None and not self._scheduler.done():
            return
        # TestClient and embedded hosts may create a fresh event loop for each
        # lifespan. asyncio primitives must belong to the current loop.
        self._wake = asyncio.Event()
        self.worker_id = f"worker-{uuid.uuid4()}"
        self.store.recover_orphaned(self.worker_id)
        self.store.cleanup_events()
        self._scheduler = asyncio.create_task(self._scheduler_loop())
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        self._wake.set()

    async def shutdown(self) -> None:
        tasks = [task for task in (self._scheduler, self._heartbeat, *self._active.values()) if task]
        for task in tasks:
            task.cancel()
        grace_seconds = max(0.0, float(os.getenv("RUNNER_SHUTDOWN_GRACE_SECONDS", "5")))
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=grace_seconds)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            for task in pending:
                task.cancel()
        self.store.release_worker(self.worker_id)
        self._active.clear()
        self._event_callbacks.clear()
        self._completion_callbacks.clear()
        self._scheduler = None
        self._heartbeat = None

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                self.store.heartbeat(self.worker_id, run_ids=set(self._active))
                self.store.recover_orphaned(self.worker_id)
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            return

    async def _scheduler_loop(self) -> None:
        try:
            while True:
                self._wake.clear()
                self.store.recover_orphaned(self.worker_id)
                while len(self._active) < self.global_limit:
                    run = self.store.claim_next(
                        self.worker_id,
                        self.global_limit,
                        self.owner_limit,
                        exclude_run_ids=set(self._active),
                    )
                    if run is None:
                        break
                    task = asyncio.create_task(self._execute(run))
                    self._active[run["id"]] = task
                    task.add_done_callback(lambda _task, run_id=run["id"]: self._finished(run_id))
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            return

    def _finished(self, run_id: str) -> None:
        current = self.store.get_run(run_id)
        if current and current["status"] == "cancel_requested":
            self.store.finalize_cancel_requested(run_id, self.worker_id)
            current = self.store.get_run(run_id)
        self._active.pop(run_id, None)
        is_terminal = bool(
            current and current["status"] in TERMINAL_STATUSES | {"needs_attention"}
        )
        if is_terminal:
            self._event_callbacks.pop(run_id, None)
        completion_callback = self._completion_callbacks.pop(run_id, None) if is_terminal else None
        if current and completion_callback:
            try:
                result = completion_callback(current)
                if inspect.isawaitable(result):
                    callback_task = asyncio.create_task(result)
                    callback_task.add_done_callback(
                        lambda completed: completed.exception() if not completed.cancelled() else None
                    )
            except Exception:
                # Compatibility projection is not part of Run correctness.
                pass
        if current:
            for listener in tuple(self._status_listeners):
                try:
                    result = listener(current)
                    if inspect.isawaitable(result):
                        asyncio.create_task(result)
                except Exception:
                    # Status projection must never compromise scheduler cleanup.
                    pass
        self._wake.set()

    def _resolve_action(self, run: dict[str, Any]) -> CapabilityAction | None:
        if run["adapter_type"] == "internal_agent":
            return None
        if run["owner_id"].startswith("app:"):
            return None
        return self.app_store.get_action(run["owner_id"], run["action_id"])

    def register_internal_agent_executor(self, executor: Any) -> None:
        """Register the versioned reducer used for ``internal_agent`` Runs.

        The callable receives ``(run, AgentRunState)`` and returns an awaitable
        or direct :class:`StepOutcome`.  Keeping registration explicit avoids
        importing the application harness into the durable core.
        """

        self._internal_agent_executor = executor

    def register_status_listener(self, listener: Any) -> None:
        """Subscribe a best-effort UI projection to committed Run statuses."""

        if listener not in self._status_listeners:
            self._status_listeners.append(listener)

    def register_completion_callback(self, run_id: str, callback: Any) -> None:
        """Attach a best-effort legacy projection without owning execution.

        Canonical completion remains in RunStore and ``/ws/runs``.  This
        process-local callback only preserves the historical command socket
        response while the client is connected.
        """

        current = self.store.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if current["status"] in TERMINAL_STATUSES | {"needs_attention"}:
            result = callback(current)
            if inspect.isawaitable(result):
                asyncio.create_task(result)
            return
        self._completion_callbacks[run_id] = callback

    async def _ensure_permission_or_wait(
        self,
        run: dict[str, Any],
        invocation_type: str,
        app_id: str,
        manifest: Any,
    ) -> bool:
        approved = True
        permission_type = ""
        value: Any = None
        if invocation_type in {"mcp_tool", "mcp_request"} and manifest.mcp_server:
            command = manifest.mcp_server["command"]
            args = manifest.mcp_server.get("args", [])
            if hasattr(self.backend_manager, "mcp_permission_identity"):
                identity = self.backend_manager.mcp_permission_identity(manifest)
                approved = self.backend_manager.is_mcp_identity_approved(app_id, identity)
            else:
                identity = None
                approved = self.backend_manager.is_mcp_approved(app_id, command, args)
            permission_type = "mcp_spawn"
            value = {
                "app_id": app_id,
                "command": command,
                "args": args,
                **({"identity": identity} if identity is not None else {}),
            }
        elif invocation_type == "agent_message" and manifest.agent_url:
            approved = self.backend_manager.is_agent_approved(app_id, manifest.agent_url)
            permission_type = "agent_connect"
            value = {"app_id": app_id, "agent_url": manifest.agent_url}
        if approved:
            return True
        current_run = self.store.get_run(run["id"]) or run
        interaction_id = str(uuid.uuid4())
        request_payload = {
            "type": "backend_permission_request",
            "request_id": interaction_id,
            "run_id": run["id"],
            "run_version": int(current_run.get("version", 0)) + 1,
            "app_id": app_id,
            "permission_type": permission_type,
            "value": value,
        }
        self.store.wait_for_interaction(
            run["id"],
            "permission",
            f"Allow {permission_type} for {app_id}?",
            request_payload,
            interaction_id=interaction_id,
            lease_owner=self.worker_id,
            lease_epoch=int(run["lease_epoch"]),
            summary="Waiting for permission",
            event_type="backend_permission_request",
        )
        callback = self._event_callbacks.get(run["id"])
        if callback:
            result = callback(request_payload)
            if inspect.isawaitable(result):
                await result
        return False

    async def _execute(self, run: dict[str, Any]) -> None:
        run_id = run["id"]
        lease_epoch = int(run["lease_epoch"])
        step_key = "invoke"
        step_attempt: int | None = None
        effect_started = False
        state = AgentRunState.model_validate(run["state"]) if run.get("state") else AgentRunState(
            workflow_type="capability" if run["adapter_type"] != "internal_agent" else run["workflow_type"],
            workflow_version=int(run["workflow_version"]),
            session_id=run["source_id"] if run["source_type"] == "chat" else None,
            phase="invoke",
            attempt=int(run["attempt"]),
        )
        try:
            action = self._resolve_action(run)
            invocation_type = run["adapter_type"]
            app_id = run["runtime_id"]
            tool_name = run.get("tool_name")
            if action is not None:
                invocation_type = action.invocation.type
                app_id = action.invocation.app_id
                tool_name = action.invocation.tool_name
            manifest = None
            if invocation_type != "internal_agent":
                manifest = self.app_manager.get_manifest(app_id)
                if manifest is None:
                    raise ValueError("Backend App is unavailable")
                if not await self._ensure_permission_or_wait(run, invocation_type, app_id, manifest):
                    return
            self.store.update_progress(
                run_id,
                0.05,
                "Starting",
                expected_lease_owner=self.worker_id,
                expected_lease_epoch=lease_epoch,
            )
            if invocation_type == "internal_agent":
                step_key = state.phase
            step_attempt = self.store.begin_step_attempt(
                run_id,
                step_key,
                lease_owner=self.worker_id,
                lease_epoch=lease_epoch,
            )
            if step_attempt is None:
                raise RuntimeError(f"completed run was queued again at step {step_key}")

            if invocation_type == "internal_agent":
                state.attempt = step_attempt
                run["step_key"] = step_key
                run["step_attempt"] = step_attempt
                if self._internal_agent_executor is None:
                    raise RuntimeError("No internal_agent workflow executor is registered")
                produced = self._internal_agent_executor(run, state)
                outcome = await produced if inspect.isawaitable(produced) else produced
                if not isinstance(outcome, (Continue, Wait, Succeeded, Failed, Cancelled, dict)):
                    raise TypeError("internal_agent executor must return a StepOutcome")
                committed = self.store.commit_step(
                    run_id,
                    step_key,
                    attempt=step_attempt,
                    lease_owner=self.worker_id,
                    lease_epoch=lease_epoch,
                    state=state,
                    outcome=outcome,
                )
                dispatcher = getattr(self._internal_agent_executor, "dispatch_committed_events", None)
                if dispatcher is not None:
                    dispatched = dispatcher(committed, self.store._coerce_outcome(outcome))
                    if inspect.isawaitable(dispatched):
                        await dispatched
                return

            else:
                if manifest is None:
                    raise ValueError("Backend App is unavailable")

                async def emit(payload: dict[str, Any]) -> None:
                    self.store.append_event(
                        run_id,
                        payload.get("type", "adapter_event"),
                        payload,
                        expected_lease_owner=self.worker_id,
                        expected_lease_epoch=lease_epoch,
                    )
                    callback = self._event_callbacks.get(run_id)
                    if callback:
                        await callback(payload)

                if invocation_type == "mcp_tool":
                    client = await self.backend_manager.get_or_start_mcp_client(app_id, manifest, emit)
                    if client is None:
                        raise ValueError("MCP runtime is unavailable")
                    effect_started = True
                    result = await client.call("tools/call", {"name": tool_name, "arguments": run["input"]})
                elif invocation_type == "mcp_request":
                    client = await self.backend_manager.get_or_start_mcp_client(app_id, manifest, emit)
                    if client is None:
                        raise ValueError("MCP runtime is unavailable")
                    if not isinstance(tool_name, str) or not tool_name:
                        raise ValueError("MCP request method is missing")
                    effect_started = True
                    result = await client.call(tool_name, run["input"])
                elif invocation_type == "agent_message":
                    events: list[Any] = []

                    async def collect(payload: dict[str, Any]) -> None:
                        events.append(payload.get("event", payload))
                        await emit(payload)

                    effect_started = True
                    await self.backend_manager.handle_agent_message(app_id, manifest, run["input"], collect)
                    result = {"events": events, "status": "completed"}
                else:
                    raise ValueError(f"Unsupported run adapter: {invocation_type}")
                if action is not None:
                    validate_json_schema(result, action.result_schema, "result")
                summary = action.title if action is not None else run["action_title"]
                artifacts = result.get("artifacts", []) if isinstance(result, dict) else []
                self.store.commit_step(
                    run_id,
                    step_key,
                    attempt=step_attempt,
                    lease_owner=self.worker_id,
                    lease_epoch=lease_epoch,
                    state=state,
                    outcome=Succeeded(
                        summary=f"{summary} completed",
                        result=result,
                        artifacts=artifacts,
                        output=result,
                    ),
                )
        except asyncio.CancelledError:
            current = self.store.get_run(run_id)
            if current and current["status"] == "cancel_requested" and step_attempt is not None:
                durable_effect_unknown = invocation_type == "internal_agent" and bool(
                    state.data.get("non_compensable_effect")
                    or state.data.get("effects_committed")
                    or state.data.get("effect_in_flight")
                )
                effect_state = (
                    "unknown"
                    if durable_effect_unknown or (effect_started and current["recovery"] != "restart_safe")
                    else "none"
                )
                try:
                    self.store.commit_step(
                        run_id,
                        step_key,
                        attempt=step_attempt,
                        lease_owner=self.worker_id,
                        lease_epoch=lease_epoch,
                        state=state,
                        outcome=Cancelled(
                            summary=(
                                "Cancellation requested; external effect is unknown"
                                if effect_state == "unknown"
                                else "Cancelled"
                            ),
                            effect_state=effect_state,
                        ),
                    )
                except StaleLeaseError:
                    pass
            raise
        except StaleLeaseError:
            # Cancellation, recovery, or a newer worker fenced this callback.
            return
        except Exception as exc:
            current = self.store.get_run(run_id)
            if step_attempt is None or current is None or current["status"] != "running":
                return
            effect_state = "unknown" if effect_started and current["recovery"] != "restart_safe" else "none"
            try:
                self.store.commit_step(
                    run_id,
                    step_key,
                    attempt=step_attempt,
                    lease_owner=self.worker_id,
                    lease_epoch=lease_epoch,
                    state=state,
                    outcome=Failed(
                        summary="Run needs attention" if effect_state == "unknown" else "Run failed",
                        error_code=type(exc).__name__,
                        message=str(exc),
                        effect_state=effect_state,
                    ),
                )
            except StaleLeaseError:
                return

    def submit(
        self,
        catalog_id: str,
        action_id: str,
        input_data: Any,
        *,
        source_type: str = "user",
        source_id: str | None = None,
        idempotency_key: str | None = None,
        correlation: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_started()
        action = self.app_store.get_action(catalog_id, action_id)
        if action is None:
            raise KeyError("Capability action not found")
        validate_json_schema(input_data, action.input_schema)
        invocation = action.invocation
        run = self.store.create_run(
            owner_id=catalog_id,
            action_id=action.id,
            action_title=action.title,
            source_type=source_type,
            source_id=source_id,
            adapter_type=invocation.type,
            runtime_id=invocation.app_id,
            tool_name=invocation.tool_name,
            input_data=input_data,
            # A manifest assertion alone is not proof that a remote MCP tool
            # or HTTP agent supports idempotent replay/reconciliation.  These
            # adapters therefore remain manual until an enforceable protocol
            # is part of their ToolSpec/transport contract.
            recovery="manual",
            parent_run_id=parent_run_id,
            idempotency_key=idempotency_key,
            correlation=correlation,
        )
        self._wake.set()
        return run

    def submit_direct_mcp(
        self,
        app_id: str,
        tool_name: str,
        input_data: Any,
        *,
        source_type: str,
        source_id: str | None,
        idempotency_key: str | None = None,
        correlation: dict[str, Any] | None = None,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        self.ensure_started()
        run = self.store.create_run(
            owner_id=f"app:{app_id}",
            action_id=tool_name,
            action_title=tool_name,
            source_type=source_type,
            source_id=source_id,
            adapter_type="mcp_tool",
            runtime_id=app_id,
            tool_name=tool_name,
            input_data=input_data,
            recovery="manual",
            idempotency_key=idempotency_key,
            correlation=correlation,
        )
        if event_callback:
            self._event_callbacks[run["id"]] = event_callback
        self._wake.set()
        return run

    def submit_direct_mcp_request(
        self,
        app_id: str,
        method: str,
        params: Any,
        *,
        source_type: str,
        source_id: str | None,
        idempotency_key: str | None = None,
        correlation: dict[str, Any] | None = None,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        """Submit a non-tool MCP request through the same durable adapter."""

        self.ensure_started()
        if method not in {"resources/read", "resources/list", "prompts/get", "prompts/list"}:
            raise ValueError(f"Unsupported direct MCP method: {method}")
        run = self.store.create_run(
            owner_id=f"app:{app_id}",
            action_id=method,
            action_title=method,
            source_type=source_type,
            source_id=source_id,
            adapter_type="mcp_request",
            runtime_id=app_id,
            tool_name=method,
            input_data=params,
            recovery="restart_safe" if method in {"resources/read", "resources/list", "prompts/get", "prompts/list"} else "manual",
            idempotency_key=idempotency_key,
            correlation=correlation,
        )
        if event_callback:
            self._event_callbacks[run["id"]] = event_callback
        self._wake.set()
        return run

    def submit_direct_agent_message(
        self,
        app_id: str,
        message: Any,
        *,
        source_type: str,
        source_id: str | None,
        idempotency_key: str | None = None,
        correlation: dict[str, Any] | None = None,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        """Submit an AG-UI/HTTP agent message through durable Run control."""

        self.ensure_started()
        run = self.store.create_run(
            owner_id=f"app:{app_id}",
            action_id="agent_message",
            action_title="Agent message",
            source_type=source_type,
            source_id=source_id,
            adapter_type="agent_message",
            runtime_id=app_id,
            input_data=message,
            recovery="manual",
            idempotency_key=idempotency_key,
            correlation=correlation,
        )
        if event_callback:
            self._event_callbacks[run["id"]] = event_callback
        self._wake.set()
        return run

    def submit_internal_agent(
        self,
        *,
        owner_id: str,
        action_id: str,
        title: str,
        session_id: str,
        input_data: Any,
        source_type: str = "chat",
        workflow_type: str = "converse",
        workflow_version: int = 1,
        state: AgentRunState | dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit durable in-process agent work to the regular scheduler."""

        self.ensure_started()
        normalized_state = (
            state
            if isinstance(state, AgentRunState)
            else AgentRunState.model_validate(state)
            if state is not None
            else AgentRunState(
                workflow_type=workflow_type,
                workflow_version=workflow_version,
                session_id=session_id,
            )
        )
        if normalized_state.session_id != session_id:
            raise ValueError("state session_id must match the submitted session")
        run = self.store.create_run(
            owner_id=owner_id,
            action_id=action_id,
            action_title=title,
            source_type=source_type,
            source_id=session_id,
            adapter_type="internal_agent",
            runtime_id="internal:agent",
            input_data=input_data,
            recovery="restart_safe",
            parent_run_id=parent_run_id,
            idempotency_key=idempotency_key,
            state=normalized_state,
            workflow_type=normalized_state.workflow_type,
            workflow_version=normalized_state.workflow_version,
        )
        self._wake.set()
        return run

    def create_external_run(
        self, *, owner_id: str, action_id: str, title: str, source_type: str, source_id: str | None, input_data: Any
    ) -> dict[str, Any]:
        return self.store.create_run(
            owner_id=owner_id,
            action_id=action_id,
            action_title=title,
            source_type=source_type,
            source_id=source_id,
            adapter_type="internal",
            runtime_id="internal:agent",
            input_data=input_data,
            recovery="manual",
            status="queued",
        )

    def claim_external(self, run_id: str) -> dict[str, Any] | None:
        return self.store.claim_specific(run_id, self.worker_id, self.global_limit, self.owner_limit)

    def bind_external_task(self, run_id: str, task: asyncio.Task) -> None:
        self._active[run_id] = task
        task.add_done_callback(lambda _task, rid=run_id: self._finished(rid))

    async def wait_terminal(self, run_id: str, timeout: float | None = None) -> dict[str, Any]:
        async def wait() -> dict[str, Any]:
            while True:
                run = self.store.get_run(run_id)
                if run is None:
                    raise KeyError(run_id)
                if run["status"] in TERMINAL_STATUSES | {"needs_attention"}:
                    return run
                await asyncio.sleep(0.1)

        return await asyncio.wait_for(wait(), timeout=timeout) if timeout else await wait()

    def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.store.request_cancel(run_id)
        task = self._active.get(run_id)
        if task and run["status"] == "cancel_requested":
            task.cancel()
        self._wake.set()
        return self.store.get_run(run_id) or run

    def retry(self, run_id: str) -> dict[str, Any]:
        original = self.store.get_run(run_id)
        if original is None:
            raise KeyError(run_id)
        if original["status"] == "needs_attention":
            raise ValueError("needs-attention runs require explicit effect reconciliation before retry")
        if original["status"] not in {"failed", "cancelled"}:
            raise ValueError("only failed or safely cancelled runs can be retried")
        if (original.get("error") or {}).get("effect_state") in {"unknown", "committed"}:
            raise ValueError("run has an unresolved or committed external effect and cannot be retried")
        retry_state = original.get("state")
        if retry_state and original["adapter_type"] == "internal_agent":
            normalized_retry_state = AgentRunState.model_validate(retry_state)
            if (normalized_retry_state.last_error or {}).get("effect_state") == "unknown":
                raise ValueError("run has an unknown external effect and cannot be retried automatically")
            normalized_retry_state.attempt = int(original["attempt"]) + 1
            normalized_retry_state.pending_interaction_id = None
            normalized_retry_state.last_error = None
            normalized_retry_state.data.pop("phase_retries", None)
            if original["status"] == "cancelled":
                preserved = {
                    key: normalized_retry_state.data[key]
                    for key in ("workspace_dir", "user_message_id", "language")
                    if key in normalized_retry_state.data
                }
                normalized_retry_state.phase = "route"
                normalized_retry_state.intent = None
                normalized_retry_state.artifact_refs = []
                normalized_retry_state.data = preserved
            retry_state = normalized_retry_state
        run = self.store.create_run(
            owner_id=original["owner_id"],
            action_id=original["action_id"],
            action_title=original["action_title"],
            source_type=original["source_type"],
            source_id=original["source_id"],
            adapter_type=original["adapter_type"],
            runtime_id=original["runtime_id"],
            tool_name=original["tool_name"],
            input_data=original["input"],
            recovery=original["recovery"],
            parent_run_id=original["parent_run_id"],
            retry_of=run_id,
            correlation=original.get("correlation"),
            attempt=int(original["attempt"]) + 1,
            state=retry_state,
            workflow_type=original.get("workflow_type"),
            workflow_version=original.get("workflow_version"),
        )
        self._wake.set()
        return run

    def resolve_interaction(
        self,
        interaction_id: str,
        response: Any,
        *,
        expected_run_version: int | None = None,
    ) -> dict[str, Any]:
        interaction = self.store.get_interaction(interaction_id)
        if interaction is None:
            raise KeyError(interaction_id)
        approved = bool(response.get("approved")) if isinstance(response, dict) else bool(response)
        payload = interaction.get("payload") or {}
        permission_type = payload.get("permission_type")
        value = payload.get("value") or {}
        run_id = interaction["run_id"]
        if expected_run_version is None and isinstance(response, dict):
            supplied_version = response.get("expected_run_version", response.get("run_version"))
            if supplied_version is not None:
                expected_run_version = int(supplied_version)
        if approved and permission_type == "mcp_spawn":
            if value.get("identity") is not None and hasattr(self.backend_manager, "approve_mcp_identity"):
                self.backend_manager.approve_mcp_identity(value["app_id"], value["identity"])
            else:
                self.backend_manager.approve_mcp(value["app_id"], value["command"], value.get("args", []))
        elif approved and permission_type == "agent_connect":
            self.backend_manager.approve_agent(value["app_id"], value["agent_url"])
        if not permission_type or approved:
            self.store.resolve_interaction(
                interaction_id,
                response,
                expected_run_version=expected_run_version,
                summary="Permission granted" if permission_type else "Interaction resolved",
            )
            run = self.store.get_run(run_id) or {}
            if run.get("status") == "queued":
                self._wake.set()
            return run
        self.store.resolve_interaction(
            interaction_id,
            response,
            expected_run_version=expected_run_version,
            target_status="failed",
            summary="Permission denied",
            error={"message": "Permission denied"},
        )
        run = self.store.get_run(run_id) or {}
        if run.get("status") == "queued":
            self._wake.set()
        return run
