from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from backend.app_manifest import APP_MANIFEST_VERSION, AppManifest, ManifestValidationError, validate_app_id
from backend.app_records import AppRecord, AppRecordStore, AppRecordTransaction

logger = logging.getLogger(__name__)

_UNSET = object()
_OBSOLETE_APP_FILES = ("index.html", "index.jsx", "layout.json", "metadata.json", "style.css")
_TOMBSTONE_PATTERN = re.compile(r"^\.(?P<app_id>[a-z0-9]+(?:-[a-z0-9]+)*)\.deleting-[0-9a-f]{32}$")


class AppManager:
    """Application service for Manifest V2 Apps and their lifecycle records."""

    def __init__(self):
        workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.apps_dir = os.getenv("APPS_DIR", os.path.join(workspace_dir, "apps"))

    @property
    def apps_dir(self) -> str:
        return self._apps_dir

    @apps_dir.setter
    def apps_dir(self, value: str | os.PathLike[str]) -> None:
        self._apps_dir = os.fspath(value)
        self._record_store = AppRecordStore(Path(self._apps_dir).resolve() / ".ambient")

    def app_path(self, app_id: str) -> Path:
        """Resolve a validated App ID to its direct workspace directory."""

        try:
            validate_app_id(app_id)
        except ManifestValidationError as exc:
            raise ValueError(f"invalid app_id: {exc}") from exc
        apps_root = Path(self.apps_dir).resolve()
        app_path = (apps_root / app_id).resolve()
        if app_path.parent != apps_root:
            raise ValueError("invalid app_id: App path must be a direct child of the Apps directory")
        return app_path

    @staticmethod
    def _new_manifest(app_id: str, title: str) -> AppManifest:
        return AppManifest.from_dict(
            {
                "manifest_version": APP_MANIFEST_VERSION,
                "id": app_id,
                "title": title,
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": [],
                "capabilities": [],
            },
            expected_app_id=app_id,
        )

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    @staticmethod
    def _is_link_or_junction(path: Path) -> bool:
        is_junction = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(is_junction and is_junction())

    def _reconcile_tombstones(self, transaction: AppRecordTransaction) -> None:
        apps_root = Path(self.apps_dir).resolve()
        if not apps_root.exists():
            return
        try:
            entries = list(apps_root.iterdir())
        except OSError:
            logger.warning("Unable to inspect pending App deletions in %s", apps_root, exc_info=True)
            return
        for tombstone in entries:
            match = _TOMBSTONE_PATTERN.fullmatch(tombstone.name)
            if match is None:
                continue
            app_id = match.group("app_id")
            if self._is_link_or_junction(tombstone) or not tombstone.is_dir():
                logger.critical("Ignoring unsafe pending-deletion path for App %s", app_id)
                continue
            app_path = apps_root / app_id
            record = self._record_store.get(transaction, app_id)
            if app_path.exists() or app_path.is_symlink():
                logger.critical("Cannot reconcile App %s: canonical path and tombstone both exist", app_id)
                continue
            if record is None:
                transaction.add_after_commit(lambda path=tombstone: self._remove_path(path))
                continue
            tombstone.replace(app_path)

            def restore(source: Path = app_path, target: Path = tombstone) -> None:
                if source.exists() and not target.exists():
                    source.replace(target)

            transaction.add_rollback(restore)

    def _reconcile_pending_deletions(self) -> None:
        with self._record_store.serialized() as transaction:
            self._reconcile_tombstones(transaction)

    def _read_manifest(
        self,
        transaction: AppRecordTransaction,
        app_id: str,
        app_path: Path,
    ) -> tuple[AppManifest, AppRecord] | None:
        manifest_path = app_path / "manifest.json"
        if not manifest_path.is_file():
            return None
        manifest = AppManifest.read(manifest_path, expected_app_id=app_id)
        record = self._record_store.get(transaction, app_id)
        if record is None:
            record = self._record_store.put(transaction, app_id)
        return manifest, record

    @staticmethod
    def _manifest_record(manifest: AppManifest, record: AppRecord) -> dict[str, Any]:
        return {
            **manifest.to_dict(),
            "manifest_revision": manifest.revision,
            "grants_digest": manifest.grants_digest,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def create_or_update_app(
        self,
        app_id: str,
        title: str,
        js: str = "",
        *,
        description: Any = _UNSET,
        app_version: Any = _UNSET,
        intents: Any = _UNSET,
        schema_refs: Any = _UNSET,
        capabilities: Any = _UNSET,
    ) -> None:
        if not isinstance(js, str):
            raise TypeError("js must be a string")
        self._reconcile_pending_deletions()
        app_path = self.app_path(app_id)
        with self._record_store.serialized() as transaction:
            existed = app_path.exists()
            app_path.mkdir(parents=True, exist_ok=True)
            tracked_names = ("controller.js", "manifest.json", *_OBSOLETE_APP_FILES)
            originals = {
                name: (app_path / name).read_bytes() if (app_path / name).is_file() else None
                for name in tracked_names
            }

            def restore() -> None:
                if not existed:
                    shutil.rmtree(app_path, ignore_errors=True)
                    return
                for name, content in originals.items():
                    path = app_path / name
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(content)

            transaction.add_rollback(restore)
            manifest_path = app_path / "manifest.json"
            if manifest_path.is_file():
                manifest_data = AppManifest.read(manifest_path, expected_app_id=app_id).to_dict()
                manifest_data["title"] = title
            else:
                manifest_data = self._new_manifest(app_id, title).to_dict()
            for field, value in {
                "description": description,
                "app_version": app_version,
                "intents": intents,
                "schema_refs": schema_refs,
                "capabilities": capabilities,
            }.items():
                if value is not _UNSET:
                    manifest_data[field] = value
            manifest = AppManifest.from_dict(manifest_data, expected_app_id=app_id)
            (app_path / "controller.js").write_text(js, encoding="utf-8")
            for name in _OBSOLETE_APP_FILES:
                (app_path / name).unlink(missing_ok=True)
            manifest.write_atomic(manifest_path)
            AppManifest.read(manifest_path, expected_app_id=app_id)
            self._record_store.put(transaction, app_id)

    def get_manifest(self, app_id: str) -> AppManifest | None:
        self._reconcile_pending_deletions()
        try:
            app_path = self.app_path(app_id)
        except ValueError:
            return None
        with self._record_store.serialized() as transaction:
            try:
                result = self._read_manifest(transaction, app_id, app_path)
                return result[0] if result is not None else None
            except (OSError, UnicodeError, ManifestValidationError):
                return None

    def get_app_files(self, app_id: str) -> dict[str, Any] | None:
        self._reconcile_pending_deletions()
        app_path = self.app_path(app_id)
        with self._record_store.serialized() as transaction:
            try:
                result = self._read_manifest(transaction, app_id, app_path)
                if result is None:
                    return None
                manifest, record = result
                js = (app_path / "controller.js").read_text(encoding="utf-8") if (app_path / "controller.js").is_file() else ""
                return {**self._manifest_record(manifest, record), "js": js}
            except (OSError, UnicodeError, ManifestValidationError):
                logger.warning("Unable to load App %s", app_id, exc_info=True)
                return None

    def list_apps(self) -> list[dict[str, Any]]:
        self._reconcile_pending_deletions()
        apps_root = Path(self.apps_dir)
        if not apps_root.exists():
            return []
        try:
            entries = sorted(
                (entry for entry in apps_root.iterdir() if entry.is_dir() and not entry.name.startswith(".")),
                key=lambda entry: entry.name,
            )
        except OSError:
            return []
        result: list[dict[str, Any]] = []
        for item_path in entries:
            try:
                safe_path = self.app_path(item_path.name)
                if safe_path != item_path.resolve() or self._is_link_or_junction(item_path):
                    raise ValueError("App directory must be a direct, non-symlink child")
                with self._record_store.serialized() as transaction:
                    loaded = self._read_manifest(transaction, item_path.name, safe_path)
                    if loaded is not None:
                        result.append(self._manifest_record(*loaded))
            except (OSError, UnicodeError, ValueError, ManifestValidationError):
                logger.warning("Skipping invalid App %s", item_path.name, exc_info=True)
        return result

    def delete_app(self, app_id: str) -> bool:
        self._reconcile_pending_deletions()
        app_path = self.app_path(app_id)
        with self._record_store.serialized() as transaction:
            if not app_path.exists():
                return False
            tombstone = app_path.with_name(f".{app_id}.deleting-{uuid.uuid4().hex}")

            def restore() -> None:
                if tombstone.exists() and not app_path.exists():
                    tombstone.replace(app_path)

            try:
                app_path.replace(tombstone)
                transaction.add_rollback(restore)
                transaction.add_after_commit(lambda: shutil.rmtree(tombstone))
                self._record_store.delete(transaction, app_id)
                return True
            except OSError:
                logger.warning("Unable to delete App %s", app_id, exc_info=True)
                return False
