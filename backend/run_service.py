"""Persistent workspace-scoped background run service.

The run service deliberately owns execution state instead of a WebSocket.  A
browser may disconnect, switch chat sessions, or close an App window while a
Run continues.  SQLite leases make queue ownership explicit and provide safe
recovery after a backend restart.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app_store import AppStoreService, CapabilityAction


ACTIVE_STATUSES = {"queued", "running", "waiting_user", "cancel_requested", "needs_attention"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
_JSON_COLUMNS = {"input", "result", "error", "checkpoint", "artifacts", "payload", "response", "output"}
_TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"waiting_user", "cancel_requested", "succeeded", "failed", "cancelled", "needs_attention"},
    "waiting_user": {"queued", "running", "cancelled", "failed", "needs_attention"},
    "cancel_requested": {"cancelled", "failed"},
    "needs_attention": {"queued", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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
        state_dir = Path(workspace_dir) / ".ambient"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = state_dir / "runs.db"
        self._initialize()

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
                    recovery TEXT NOT NULL DEFAULT 'manual',
                    parent_run_id TEXT,
                    retry_of TEXT,
                    idempotency_key TEXT,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
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
                    run_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id, sequence);

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
                    UNIQUE(run_id, step_key),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(runs)").fetchall()}
            if "artifacts_json" not in columns:
                connection.execute("ALTER TABLE runs ADD COLUMN artifacts_json TEXT NOT NULL DEFAULT '[]'")

    @staticmethod
    def _append_event(connection: sqlite3.Connection, run_id: str, event_type: str, payload: Any) -> int:
        cursor = connection.execute(
            "INSERT INTO run_events(run_id, type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (run_id, event_type, _json(payload), _now()),
        )
        return int(cursor.lastrowid)

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
        attempt: int = 1,
        status: str = "queued",
    ) -> dict[str, Any]:
        if idempotency_key:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM runs WHERE owner_id=? AND action_id=? AND idempotency_key=?",
                    (owner_id, action_id, idempotency_key),
                ).fetchone()
                if existing:
                    return _decode_row(existing) or {}
        run_id = str(uuid.uuid4())
        now = _now()
        started_at = now if status == "running" else None
        try:
            with self._connect() as connection:
                connection.execute(
                """
                INSERT INTO runs(
                    id, owner_id, action_id, action_title, source_type, source_id,
                    adapter_type, runtime_id, tool_name, input_json, status,
                    recovery, parent_run_id, retry_of, idempotency_key, attempt,
                    created_at, updated_at, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                    run_id, owner_id, action_id, action_title, source_type, source_id,
                    adapter_type, runtime_id, tool_name, _json(input_data), status,
                    recovery, parent_run_id, retry_of, idempotency_key, attempt,
                    now, now, started_at,
                    ),
                )
                self._append_event(connection, run_id, "run_created", {"status": status})
        except sqlite3.IntegrityError:
            if not idempotency_key:
                raise
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM runs WHERE owner_id=? AND action_id=? AND idempotency_key=?",
                    (owner_id, action_id, idempotency_key),
                ).fetchone()
            if existing:
                return _decode_row(existing) or {}
            raise
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str, *, include_events: bool = False) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = _decode_row(connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
            if run is None:
                return None
            run["interactions"] = [
                _decode_row(row) for row in connection.execute(
                    "SELECT * FROM run_interactions WHERE run_id=? ORDER BY created_at", (run_id,)
                ).fetchall()
            ]
            run["steps"] = [
                _decode_row(row) for row in connection.execute(
                    "SELECT * FROM run_steps WHERE run_id=? ORDER BY id", (run_id,)
                ).fetchall()
            ]
            if include_events:
                run["events"] = [
                    _decode_row(row) for row in connection.execute(
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

    def append_event(self, run_id: str, event_type: str, payload: Any) -> int:
        with self._connect() as connection:
            return self._append_event(connection, run_id, event_type, payload)

    def transition(self, run_id: str, status: str, **updates: Any) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(run_id)
            current = row["status"]
            if status != current and status not in _TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid run transition: {current} -> {status}")
            now = _now()
            fields: dict[str, Any] = {"status": status, "updated_at": now}
            fields.update(updates)
            if status == "running" and not row["started_at"]:
                fields["started_at"] = now
            if status in TERMINAL_STATUSES:
                fields["finished_at"] = now
                fields["lease_owner"] = None
                fields["lease_expires_at"] = None
                if status == "succeeded":
                    fields.setdefault("progress", 1.0)
            encoded: dict[str, Any] = {}
            for key, value in fields.items():
                column = f"{key}_json" if key in _JSON_COLUMNS else key
                encoded[column] = _json(value) if key in _JSON_COLUMNS and value is not None else value
            assignments = ", ".join(f"{key}=?" for key in encoded)
            connection.execute(
                f"UPDATE runs SET {assignments} WHERE id=?", [*encoded.values(), run_id]
            )
            self._append_event(connection, run_id, "status_changed", {"from": current, "to": status, **updates})
        return self.get_run(run_id) or {}

    def update_progress(self, run_id: str, progress: float, summary: str = "") -> None:
        progress = max(0.0, min(1.0, progress))
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET progress=?, summary=?, updated_at=? WHERE id=?",
                (progress, summary, _now(), run_id),
            )
            self._append_event(connection, run_id, "progress", {"progress": progress, "summary": summary})

    def claim_next(self, worker_id: str, global_limit: int, owner_limit: int, lease_seconds: int = 30) -> dict[str, Any] | None:
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
            row = next((candidate for candidate in candidates if owner_counts.get(candidate["owner_id"], 0) < owner_limit), None)
            if row is None:
                return None
            now = _now()
            expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
            connection.execute(
                """UPDATE runs SET status='running', started_at=COALESCE(started_at, ?), updated_at=?,
                   lease_owner=?, lease_expires_at=? WHERE id=? AND status='queued'""",
                (now, now, worker_id, expires, row["id"]),
            )
            self._append_event(connection, row["id"], "status_changed", {"from": "queued", "to": "running"})
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
                   lease_owner=?, lease_expires_at=? WHERE id=? AND status='queued'""",
                (now, now, worker_id, expires, run_id),
            )
            self._append_event(connection, run_id, "status_changed", {"from": "queued", "to": "running"})
        return self.get_run(run_id)

    def heartbeat(self, worker_id: str, lease_seconds: int = 30) -> None:
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at=? WHERE lease_owner=? AND status IN ('running','cancel_requested')",
                (expires, worker_id),
            )

    def recover_orphaned(self, worker_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT id, status, recovery FROM runs
                   WHERE status IN ('running','cancel_requested')
                     AND (lease_owner IS NULL OR (lease_owner<>? AND (lease_expires_at IS NULL OR lease_expires_at<?)))""",
                (worker_id, _now()),
            ).fetchall()
            for row in rows:
                if row["status"] == "cancel_requested":
                    target = "cancelled"
                elif row["recovery"] == "restart_safe":
                    target = "queued"
                else:
                    target = "needs_attention"
                finished = _now() if target == "cancelled" else None
                connection.execute(
                    "UPDATE runs SET status=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=?, finished_at=COALESCE(finished_at, ?) WHERE id=?",
                    (target, _now(), finished, row["id"]),
                )
                self._append_event(connection, row["id"], "recovered", {"status": target})

    def begin_step(self, run_id: str, step_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM run_steps WHERE run_id=? AND step_key=?", (run_id, step_key)
            ).fetchone()
            if row and row["status"] == "succeeded":
                return False
            connection.execute(
                """INSERT INTO run_steps(run_id, step_key, status, started_at)
                   VALUES (?, ?, 'running', ?)
                   ON CONFLICT(run_id, step_key) DO UPDATE SET
                     status='running', attempt=run_steps.attempt+1, started_at=excluded.started_at, finished_at=NULL""",
                (run_id, step_key, _now()),
            )
        return True

    def finish_step(self, run_id: str, step_key: str, output: Any = None, status: str = "succeeded") -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE run_steps SET status=?, output_json=?, finished_at=? WHERE run_id=? AND step_key=?",
                (status, _json(output) if output is not None else None, _now(), run_id, step_key),
            )
            if status == "succeeded":
                connection.execute(
                    "UPDATE runs SET checkpoint_json=?, updated_at=? WHERE id=?",
                    (_json({"last_step": step_key, "output": output}), _now(), run_id),
                )

    def create_interaction(
        self, run_id: str, interaction_type: str, prompt: str, payload: Any, interaction_id: str | None = None
    ) -> dict[str, Any]:
        interaction_id = interaction_id or str(uuid.uuid4())
        with self._connect() as connection:
            existing = connection.execute("SELECT * FROM run_interactions WHERE id=?", (interaction_id,)).fetchone()
            if existing:
                return _decode_row(existing) or {}
            connection.execute(
                """INSERT INTO run_interactions(id, run_id, type, prompt, payload_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (interaction_id, run_id, interaction_type, prompt, _json(payload), _now()),
            )
            self._append_event(connection, run_id, "interaction_requested", {"interaction_id": interaction_id, "type": interaction_type})
        return self.get_interaction(interaction_id) or {}

    def get_interaction(self, interaction_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return _decode_row(
                connection.execute("SELECT * FROM run_interactions WHERE id=?", (interaction_id,)).fetchone()
            )

    def resolve_interaction(self, interaction_id: str, response: Any) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM run_interactions WHERE id=?", (interaction_id,)).fetchone()
            if not row:
                raise KeyError(interaction_id)
            if row["status"] != "pending":
                raise ValueError("interaction is already resolved")
            connection.execute(
                "UPDATE run_interactions SET status='resolved', response_json=?, resolved_at=? WHERE id=?",
                (_json(response), _now(), interaction_id),
            )
            self._append_event(connection, row["run_id"], "interaction_resolved", {"interaction_id": interaction_id})
        return self.get_interaction(interaction_id) or {}

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] in TERMINAL_STATUSES:
            return run
        if run["status"] in {"queued", "waiting_user", "needs_attention"}:
            return self.transition(run_id, "cancelled", summary="Cancelled")
        if run["status"] == "running":
            return self.transition(run_id, "cancel_requested", summary="Cancellation requested")
        return run

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

    def cleanup_events(self, days: int = 30) -> int:
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
        for task in (self._scheduler, self._heartbeat):
            if task:
                task.cancel()
        for task in list(self._active.values()):
            task.cancel()
        await asyncio.gather(
            *[task for task in (self._scheduler, self._heartbeat, *self._active.values()) if task],
            return_exceptions=True,
        )
        self._active.clear()
        self._scheduler = None
        self._heartbeat = None

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                self.store.heartbeat(self.worker_id)
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            return

    async def _scheduler_loop(self) -> None:
        try:
            while True:
                self._wake.clear()
                while len(self._active) < self.global_limit:
                    run = self.store.claim_next(self.worker_id, self.global_limit, self.owner_limit)
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
        self._active.pop(run_id, None)
        self._event_callbacks.pop(run_id, None)
        self._wake.set()

    def _resolve_action(self, run: dict[str, Any]) -> CapabilityAction | None:
        if run["owner_id"].startswith("app:"):
            return None
        return self.app_store.get_action(run["owner_id"], run["action_id"])

    def _ensure_permission_or_wait(self, run: dict[str, Any], action: CapabilityAction) -> bool:
        invocation = action.invocation
        manifest = self.app_manager.get_manifest(invocation.app_id)
        if manifest is None:
            raise ValueError("Capability invocation target is unavailable")
        approved = True
        permission_type = ""
        value: Any = None
        if invocation.type == "mcp_tool" and manifest.mcp_server:
            command = manifest.mcp_server["command"]
            args = manifest.mcp_server.get("args", [])
            approved = self.backend_manager.is_mcp_approved(invocation.app_id, command, args)
            permission_type = "mcp_spawn"
            value = {"app_id": invocation.app_id, "command": command, "args": args}
        elif invocation.type == "agent_message" and manifest.agent_url:
            approved = self.backend_manager.is_agent_approved(invocation.app_id, manifest.agent_url)
            permission_type = "agent_connect"
            value = {"app_id": invocation.app_id, "agent_url": manifest.agent_url}
        if approved:
            return True
        self.store.create_interaction(
            run["id"],
            "permission",
            f"Allow {permission_type} for {invocation.app_id}?",
            {"permission_type": permission_type, "value": value},
        )
        self.store.transition(run["id"], "waiting_user", summary="Waiting for permission")
        return False

    async def _execute(self, run: dict[str, Any]) -> None:
        run_id = run["id"]
        try:
            action = self._resolve_action(run)
            invocation_type = run["adapter_type"]
            app_id = run["runtime_id"]
            tool_name = run.get("tool_name")
            if action is not None:
                if not self._ensure_permission_or_wait(run, action):
                    return
                invocation_type = action.invocation.type
                app_id = action.invocation.app_id
                tool_name = action.invocation.tool_name
            self.store.update_progress(run_id, 0.05, "Starting")
            if not self.store.begin_step(run_id, "invoke"):
                step = next((step for step in self.store.get_run(run_id)["steps"] if step["step_key"] == "invoke"), None)
                result = step.get("output") if step else None
            else:
                manifest = self.app_manager.get_manifest(app_id)
                if manifest is None:
                    raise ValueError("Backend App is unavailable")

                async def emit(payload: dict[str, Any]) -> None:
                    self.store.append_event(run_id, payload.get("type", "adapter_event"), payload)
                    if payload.get("type") == "backend_permission_request" and payload.get("request_id"):
                        self.store.create_interaction(
                            run_id,
                            "permission",
                            f"Allow {payload.get('permission_type', 'backend operation')} for {app_id}?",
                            {"request": payload},
                            payload["request_id"],
                        )
                        current = self.store.get_run(run_id)
                        if current and current["status"] == "running":
                            self.store.transition(run_id, "waiting_user", summary="Waiting for permission")
                    callback = self._event_callbacks.get(run_id)
                    if callback:
                        await callback(payload)

                if invocation_type == "mcp_tool":
                    client = await self.backend_manager.get_or_start_mcp_client(app_id, manifest, emit)
                    if client is None:
                        raise ValueError("MCP runtime is unavailable")
                    result = await client.call("tools/call", {"name": tool_name, "arguments": run["input"]})
                elif invocation_type == "agent_message":
                    events: list[Any] = []

                    async def collect(payload: dict[str, Any]) -> None:
                        events.append(payload.get("event", payload))
                        await emit(payload)

                    await self.backend_manager.handle_agent_message(app_id, manifest, run["input"], collect)
                    result = {"events": events, "status": "completed"}
                else:
                    raise ValueError(f"Unsupported run adapter: {invocation_type}")
                if action is not None:
                    validate_json_schema(result, action.result_schema, "result")
                self.store.finish_step(run_id, "invoke", result)

            current = self.store.get_run(run_id)
            if current and current["status"] == "cancel_requested":
                self.store.transition(run_id, "cancelled", summary="Cancelled")
            else:
                summary = action.title if action is not None else run["action_title"]
                artifacts = result.get("artifacts", []) if isinstance(result, dict) else []
                self.store.transition(
                    run_id, "succeeded", summary=f"{summary} completed", result=result, artifacts=artifacts
                )
        except asyncio.CancelledError:
            current = self.store.get_run(run_id)
            if current and current["status"] == "cancel_requested":
                self.store.finish_step(run_id, "invoke", status="cancelled")
                self.store.transition(run_id, "cancelled", summary="Cancelled")
            raise
        except Exception as exc:
            self.store.finish_step(run_id, "invoke", {"message": str(exc)}, status="failed")
            current = self.store.get_run(run_id)
            if current and current["status"] not in TERMINAL_STATUSES:
                self.store.transition(
                    run_id, "failed", summary="Run failed", error={"type": type(exc).__name__, "message": str(exc)}
                )

    def submit(
        self,
        catalog_id: str,
        action_id: str,
        input_data: Any,
        *,
        source_type: str = "user",
        source_id: str | None = None,
        idempotency_key: str | None = None,
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
            recovery=action.recovery,
            parent_run_id=parent_run_id,
            idempotency_key=idempotency_key,
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
        event_callback: Any = None,
    ) -> dict[str, Any]:
        self.ensure_started()
        run = self.store.create_run(
            owner_id=f"app:{app_id}", action_id=tool_name, action_title=tool_name,
            source_type=source_type, source_id=source_id, adapter_type="mcp_tool",
            runtime_id=app_id, tool_name=tool_name, input_data=input_data, recovery="manual",
        )
        if event_callback:
            self._event_callbacks[run["id"]] = event_callback
        self._wake.set()
        return run

    def create_external_run(
        self, *, owner_id: str, action_id: str, title: str, source_type: str, source_id: str | None, input_data: Any
    ) -> dict[str, Any]:
        return self.store.create_run(
            owner_id=owner_id, action_id=action_id, action_title=title,
            source_type=source_type, source_id=source_id, adapter_type="internal",
            runtime_id="internal:agent", input_data=input_data, recovery="manual", status="queued",
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
        if original["status"] not in TERMINAL_STATUSES | {"needs_attention"}:
            raise ValueError("only terminal or needs-attention runs can be retried")
        run = self.store.create_run(
            owner_id=original["owner_id"], action_id=original["action_id"], action_title=original["action_title"],
            source_type=original["source_type"], source_id=original["source_id"],
            adapter_type=original["adapter_type"], runtime_id=original["runtime_id"], tool_name=original["tool_name"],
            input_data=original["input"], recovery=original["recovery"], parent_run_id=original["parent_run_id"],
            retry_of=run_id, attempt=int(original["attempt"]) + 1,
        )
        self._wake.set()
        return run

    def resolve_interaction(self, interaction_id: str, response: Any) -> dict[str, Any]:
        interaction = self.store.resolve_interaction(interaction_id, response)
        approved = bool(response.get("approved")) if isinstance(response, dict) else bool(response)
        payload = interaction.get("payload") or {}
        permission_type = payload.get("permission_type")
        value = payload.get("value") or {}
        run_id = interaction["run_id"]
        if not permission_type:
            run = self.store.get_run(run_id)
            if run and run["status"] == "waiting_user":
                return self.store.transition(run_id, "running", summary="Interaction resolved")
            return run or {}
        if approved and permission_type == "mcp_spawn":
            self.backend_manager.approve_mcp(value["app_id"], value["command"], value.get("args", []))
        elif approved and permission_type == "agent_connect":
            self.backend_manager.approve_agent(value["app_id"], value["agent_url"])
        if approved:
            self.store.transition(run_id, "queued", summary="Permission granted")
            self._wake.set()
        else:
            self.store.transition(run_id, "failed", summary="Permission denied", error={"message": "Permission denied"})
        return self.store.get_run(run_id) or {}
