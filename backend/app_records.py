import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


@dataclass(frozen=True, slots=True)
class AppRecord:
    app_id: str
    created_at: str
    updated_at: str


class AppRecordTransaction:
    """SQLite transaction with filesystem compensation hooks."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self._rollback_actions: list[Callable[[], None]] = []
        self._after_commit_actions: list[Callable[[], None]] = []

    def add_rollback(self, action: Callable[[], None]) -> None:
        self._rollback_actions.append(action)

    def add_after_commit(self, action: Callable[[], None]) -> None:
        self._after_commit_actions.append(action)

    def rollback_filesystem(self) -> None:
        for action in reversed(self._rollback_actions):
            try:
                action()
            except Exception:
                logger.critical("Filesystem transaction compensation failed", exc_info=True)

    def finish(self) -> None:
        for action in self._after_commit_actions:
            try:
                action()
            except Exception:
                logger.error("Post-commit filesystem cleanup failed", exc_info=True)


class AppRecordStore:
    """Platform-owned lifecycle records; App declarations remain in manifest.json."""

    def __init__(self, workspace_dir: Path):
        workspace_dir = workspace_dir.absolute()
        if workspace_dir.exists() and (_is_link_or_junction(workspace_dir) or not workspace_dir.is_dir()):
            raise OSError(f"App record directory must be a real directory: {workspace_dir}")
        workspace_dir.mkdir(parents=True, exist_ok=True)
        if _is_link_or_junction(workspace_dir) or workspace_dir.resolve() != workspace_dir:
            raise OSError(f"App record directory must not be a link or path escape: {workspace_dir}")
        self.db_path = workspace_dir / "app_records.db"
        self._validate_db_path()
        self._initialize()

    def _validate_db_path(self) -> None:
        if self.db_path.exists() and (_is_link_or_junction(self.db_path) or not self.db_path.is_file()):
            raise OSError(f"App record database must be a regular file: {self.db_path}")
        if self.db_path.parent.resolve() != self.db_path.parent:
            raise OSError(f"App record database must remain inside its state directory: {self.db_path}")

    def _connect(self) -> sqlite3.Connection:
        self._validate_db_path()
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS app_records (
                    app_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def serialized(self) -> Iterator[AppRecordTransaction]:
        """Serialize filesystem and record changes across managers and processes."""
        connection = self._connect()
        transaction = AppRecordTransaction(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield transaction
            connection.commit()
        except Exception:
            connection.rollback()
            transaction.rollback_filesystem()
            raise
        else:
            transaction.finish()
        finally:
            connection.close()

    @staticmethod
    def get(transaction: AppRecordTransaction, app_id: str) -> AppRecord | None:
        row = transaction.connection.execute(
            "SELECT app_id, created_at, updated_at FROM app_records WHERE app_id = ?",
            (app_id,),
        ).fetchone()
        return AppRecord(**dict(row)) if row else None

    @staticmethod
    def put(
        transaction: AppRecordTransaction,
        app_id: str,
        *,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> AppRecord:
        existing = AppRecordStore.get(transaction, app_id)
        now = datetime.now(UTC).isoformat()
        record = AppRecord(
            app_id=app_id,
            created_at=created_at or (existing.created_at if existing else now),
            updated_at=updated_at or now,
        )
        transaction.connection.execute(
            """
            INSERT INTO app_records (app_id, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(app_id) DO UPDATE SET
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (record.app_id, record.created_at, record.updated_at),
        )
        return record

    @staticmethod
    def delete(transaction: AppRecordTransaction, app_id: str) -> None:
        transaction.connection.execute("DELETE FROM app_records WHERE app_id = ?", (app_id,))
