import json
import logging
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app_manifest import APP_MANIFEST_VERSION, AppManifest, ManifestValidationError, validate_app_id
from backend.app_records import AppRecord, AppRecordStore, AppRecordTransaction

logger = logging.getLogger(__name__)

_SOURCE_FILES = ("index.html", "style.css", "controller.js")
_UNSET = object()
_TOMBSTONE_PATTERN = re.compile(r"^\.(?P<app_id>[a-z0-9]+(?:-[a-z0-9]+)*)\.deleting-[0-9a-f]{32}$")


class AppManager:
    def __init__(self):
        workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.apps_dir = os.getenv("APPS_DIR", os.path.join(workspace_dir, "apps"))
        self._record_store = AppRecordStore(Path(self.apps_dir).resolve() / ".ambient")

    def _get_app_path(self, app_id: str) -> Path:
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
    def _default_title(app_id: str, app_path: Path) -> str:
        title = app_id.replace("-", " ").title()
        try:
            html_content = (app_path / "index.html").read_text(encoding="utf-8")
            title_match = re.search(r"<title>(.*?)</title>", html_content, re.IGNORECASE | re.DOTALL)
            if title_match and title_match.group(1).strip():
                title = title_match.group(1).strip()
        except (OSError, UnicodeError):
            pass
        return title

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
            },
            expected_app_id=app_id,
        )

    @staticmethod
    def _parse_timestamp(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ManifestValidationError(f"legacy metadata {field} must be an ISO 8601 string")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ManifestValidationError(f"legacy metadata {field} must be an ISO 8601 string") from exc
        if parsed.tzinfo is None:
            raise ManifestValidationError(f"legacy metadata {field} must include a timezone")
        return parsed.astimezone(UTC).isoformat()

    def _read_legacy_metadata(self, app_id: str, metadata_path: Path) -> tuple[str, str, str]:
        try:
            with metadata_path.open(encoding="utf-8") as metadata_file:
                metadata = json.load(metadata_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestValidationError("legacy metadata must be readable, valid UTF-8 JSON") from exc
        if not isinstance(metadata, dict):
            raise ManifestValidationError("legacy metadata must be a JSON object")
        if metadata.get("id") != app_id:
            raise ManifestValidationError("legacy metadata id must match its App directory name")
        title = metadata.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ManifestValidationError("legacy metadata title must be a non-empty string")
        created_at = self._parse_timestamp(metadata.get("created_at"), "created_at")
        updated_at = self._parse_timestamp(metadata.get("updated_at"), "updated_at")
        return title.strip(), created_at, updated_at

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
            try:
                validate_app_id(app_id)
            except ManifestValidationError:
                continue
            if self._is_link_or_junction(tombstone) or not tombstone.is_dir():
                logger.critical(
                    "Ignoring unsafe pending-deletion path for App %s: expected a non-symlink directory",
                    app_id,
                )
                continue

            app_path = apps_root / app_id
            record = self._record_store.get(transaction, app_id)
            if app_path.exists() or app_path.is_symlink():
                logger.critical(
                    "Cannot reconcile pending deletion for App %s: canonical path and tombstone both exist",
                    app_id,
                )
                continue
            if record is None:
                transaction.add_after_commit(lambda path=tombstone: self._remove_path(path))
                continue

            tombstone.replace(app_path)

            def restore_tombstone(source: Path = app_path, target: Path = tombstone) -> None:
                if source.exists() and not target.exists():
                    source.replace(target)

            transaction.add_rollback(restore_tombstone)
            logger.warning("Restored App %s from an interrupted deletion", app_id)

    def _reconcile_pending_deletions(self) -> None:
        with self._record_store.serialized() as transaction:
            self._reconcile_tombstones(transaction)

    @staticmethod
    def _schedule_metadata_cleanup(transaction: AppRecordTransaction, metadata_path: Path) -> None:
        if metadata_path.exists():
            transaction.add_after_commit(lambda: metadata_path.unlink(missing_ok=True))

    def _read_or_migrate_manifest(
        self, transaction: AppRecordTransaction, app_id: str, app_path: Path
    ) -> tuple[AppManifest, AppRecord] | None:
        manifest_path = app_path / "manifest.json"
        metadata_path = app_path / "metadata.json"
        if manifest_path.exists():
            manifest = AppManifest.read(manifest_path, expected_app_id=app_id)
            record = self._record_store.get(transaction, app_id)
            if record is None:
                if metadata_path.exists():
                    try:
                        _, created_at, updated_at = self._read_legacy_metadata(app_id, metadata_path)
                    except ManifestValidationError:
                        logger.warning(
                            "Legacy metadata for App %s cannot recover lifecycle timestamps; "
                            "timestamps start at discovery time",
                            app_id,
                            exc_info=True,
                        )
                        now = datetime.now(UTC).isoformat()
                        created_at = updated_at = now
                    record = self._record_store.put(
                        transaction,
                        app_id,
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                else:
                    now = datetime.now(UTC).isoformat()
                    logger.warning(
                        "Initializing missing lifecycle record for App %s; timestamps start at discovery time",
                        app_id,
                    )
                    record = self._record_store.put(transaction, app_id, created_at=now, updated_at=now)
            self._schedule_metadata_cleanup(transaction, metadata_path)
            return manifest, record
        if not (app_path / "index.html").is_file() and not (app_path / "layout.json").is_file():
            return None

        title = self._default_title(app_id, app_path)
        if metadata_path.exists():
            title, created_at, updated_at = self._read_legacy_metadata(app_id, metadata_path)
        else:
            now = datetime.now(UTC).isoformat()
            created_at = updated_at = now

        manifest = self._new_manifest(app_id, title)

        def restore_migration_files() -> None:
            manifest_path.unlink(missing_ok=True)

        transaction.add_rollback(restore_migration_files)
        try:
            manifest.write_atomic(manifest_path)
            verified = AppManifest.read(manifest_path, expected_app_id=app_id)
            record = self._record_store.put(
                transaction,
                app_id,
                created_at=created_at,
                updated_at=updated_at,
            )
            self._schedule_metadata_cleanup(transaction, metadata_path)
        except Exception:
            manifest_path.unlink(missing_ok=True)
            raise
        return verified, record

    @staticmethod
    def _manifest_record(manifest: AppManifest, record: AppRecord) -> dict[str, Any]:
        return {
            **manifest.to_dict(),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def create_or_update_app(
        self,
        app_id: str,
        title: str,
        html: str = "",
        css: str = "",
        js: str = "",
        *,
        layout: Any = _UNSET,
        description: Any = _UNSET,
        app_version: Any = _UNSET,
        intents: Any = _UNSET,
        schema_refs: Any = _UNSET,
    ) -> None:
        self._reconcile_pending_deletions()
        app_path = self._get_app_path(app_id)
        if not all(isinstance(source, str) for source in (html, css, js)):
            raise TypeError("html, css, and js must be strings")
        with self._record_store.serialized() as transaction:
            existed = app_path.exists()
            app_path.mkdir(parents=True, exist_ok=True)
            originals = {
                filename: (app_path / filename).read_bytes() if (app_path / filename).exists() else None
                for filename in (*_SOURCE_FILES, "layout.json", "manifest.json", "metadata.json")
            }

            def restore_app_files() -> None:
                if not existed:
                    shutil.rmtree(app_path, ignore_errors=True)
                else:
                    for filename, content in originals.items():
                        path = app_path / filename
                        if content is None:
                            path.unlink(missing_ok=True)
                        else:
                            path.write_bytes(content)

            transaction.add_rollback(restore_app_files)
            manifest_path = app_path / "manifest.json"
            legacy_created_at: str | None = None
            if manifest_path.exists():
                current = AppManifest.read(manifest_path, expected_app_id=app_id)
                manifest_data = current.to_dict()
                manifest_data["title"] = title
                if self._record_store.get(transaction, app_id) is None:
                    metadata_path = app_path / "metadata.json"
                    if metadata_path.exists():
                        try:
                            _, legacy_created_at, _ = self._read_legacy_metadata(app_id, metadata_path)
                        except ManifestValidationError:
                            logger.warning(
                                "Legacy metadata for App %s cannot recover its creation timestamp; "
                                "the explicit update establishes a new lifecycle record",
                                app_id,
                                exc_info=True,
                            )
                    else:
                        logger.warning(
                            "Initializing missing lifecycle record for App %s during explicit update",
                            app_id,
                        )
            else:
                manifest_data = self._new_manifest(app_id, title).to_dict()
                metadata_path = app_path / "metadata.json"
                if metadata_path.exists():
                    _, created_at, updated_at = self._read_legacy_metadata(app_id, metadata_path)
                    legacy_created_at = created_at
            replacements = {
                "description": description,
                "app_version": app_version,
                "intents": intents,
                "schema_refs": schema_refs,
            }
            for field, value in replacements.items():
                if value is not _UNSET:
                    manifest_data[field] = value
            manifest = AppManifest.from_dict(manifest_data, expected_app_id=app_id)

            if layout is not _UNSET:
                (app_path / "layout.json").write_text(layout, encoding="utf-8")
                # For A2UI, remove legacy HTML/CSS files to avoid confusion
                (app_path / "index.html").unlink(missing_ok=True)
                (app_path / "style.css").unlink(missing_ok=True)
                # Still save controller.js
                (app_path / "controller.js").write_text(js, encoding="utf-8")
            else:
                # If creating legacy app, make sure layout.json is deleted
                (app_path / "layout.json").unlink(missing_ok=True)
                for filename, content in zip(_SOURCE_FILES, (html, css, js), strict=True):
                    (app_path / filename).write_text(content, encoding="utf-8")

            manifest.write_atomic(manifest_path)
            AppManifest.read(manifest_path, expected_app_id=app_id)
            if legacy_created_at is None:
                self._record_store.put(transaction, app_id)
            else:
                self._record_store.put(
                    transaction,
                    app_id,
                    created_at=legacy_created_at,
                )
            metadata_path = app_path / "metadata.json"
            self._schedule_metadata_cleanup(transaction, metadata_path)

    def get_manifest(self, app_id: str) -> AppManifest | None:
        self._reconcile_pending_deletions()
        try:
            app_path = self._get_app_path(app_id)
        except ValueError:
            return None
        with self._record_store.serialized() as transaction:
            try:
                result = self._read_or_migrate_manifest(transaction, app_id, app_path)
                if result is None:
                    return None
                manifest, _ = result
                return manifest
            except (OSError, UnicodeError, ManifestValidationError):
                return None

    def get_app_files(self, app_id: str) -> dict[str, Any] | None:
        self._reconcile_pending_deletions()
        app_path = self._get_app_path(app_id)
        with self._record_store.serialized() as transaction:
            try:
                result = self._read_or_migrate_manifest(transaction, app_id, app_path)
                if result is None:
                    return None
                manifest, record = result
                source = {
                    key: (app_path / filename).read_text(encoding="utf-8") if (app_path / filename).exists() else ""
                    for key, filename in zip(("html", "css", "js"), _SOURCE_FILES, strict=True)
                }
                layout_path = app_path / "layout.json"
                if layout_path.exists():
                    source["layout"] = layout_path.read_text(encoding="utf-8")
                return {**self._manifest_record(manifest, record), **source}
            except (OSError, UnicodeError, ManifestValidationError):
                logger.warning("Unable to load App %s", app_id, exc_info=True)
                return None

    def list_apps(self) -> list[dict[str, Any]]:
        self._reconcile_pending_deletions()
        apps_root = Path(self.apps_dir)
        if not apps_root.exists():
            return []

        apps_list: list[dict[str, Any]] = []
        try:
            entries = sorted(
                (entry for entry in apps_root.iterdir() if entry.is_dir() and not entry.name.startswith(".")),
                key=lambda entry: entry.name,
            )
        except OSError:
            logger.warning("Unable to list Apps directory %s", apps_root, exc_info=True)
            return []

        for item_path in entries:
            app_id = item_path.name
            try:
                safe_path = self._get_app_path(app_id)
                if safe_path != item_path.resolve() or item_path.is_symlink():
                    raise ValueError("App directory must be a direct, non-symlink child of the Apps directory")
                with self._record_store.serialized() as transaction:
                    result = self._read_or_migrate_manifest(transaction, app_id, safe_path)
                    if result is not None:
                        manifest, record = result
                        apps_list.append(self._manifest_record(manifest, record))
            except (OSError, UnicodeError, ValueError, ManifestValidationError):
                logger.warning("Skipping invalid App %s", app_id, exc_info=True)
        return apps_list

    def delete_app(self, app_id: str) -> bool:
        self._reconcile_pending_deletions()
        app_path = self._get_app_path(app_id)
        with self._record_store.serialized() as transaction:
            if not app_path.exists():
                return False
            tombstone = app_path.with_name(f".{app_id}.deleting-{uuid.uuid4().hex}")

            def restore_deleted_app() -> None:
                if tombstone.exists() and not app_path.exists():
                    tombstone.replace(app_path)

            try:
                app_path.replace(tombstone)
                transaction.add_rollback(restore_deleted_app)
                transaction.add_after_commit(lambda: shutil.rmtree(tombstone))
                self._record_store.delete(transaction, app_id)
                return True
            except OSError:
                logger.warning("Unable to delete App %s", app_id, exc_info=True)
                return False
