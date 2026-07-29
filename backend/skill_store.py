from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.skill_manifest import SkillManifest, SkillManifestError
from backend.skill_version import compare_semver, parse_semver


_DIGEST_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
_PACKAGE_FILES = frozenset({"SKILL.md", "market.json"})
MAX_MARKET_FILE_BYTES = 128 * 1024


class SkillStoreError(RuntimeError):
    """Base error for installed-skill persistence."""


class SkillStoreCorruptionError(SkillStoreError):
    """Persistent state is malformed and must not be silently replaced."""


class SkillPackageIntegrityError(SkillStoreError):
    """An installed content-addressed package no longer matches its digest."""


class SkillNotInstalledError(SkillStoreError, KeyError):
    """The requested catalog item is not installed."""


class SkillRevisionConflict(SkillStoreError):
    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(f"Skill registry revision conflict: expected {expected}, found {actual}")


class SkillVersionConflict(SkillStoreError):
    """The same published version was observed with different package bytes."""

    def __init__(self, catalog_id: str, version: str, installed_digest: str, candidate_digest: str):
        self.catalog_id = catalog_id
        self.version = version
        self.installed_digest = installed_digest
        self.candidate_digest = candidate_digest
        super().__init__(
            f"Skill '{catalog_id}' version '{version}' is already pinned to a different digest"
        )


class SkillDowngradeConflict(SkillStoreError):
    """A normal update attempted to replace an installation with an older version."""

    def __init__(self, catalog_id: str, installed_version: str, candidate_version: str):
        self.catalog_id = catalog_id
        self.installed_version = installed_version
        self.candidate_version = candidate_version
        super().__init__(
            f"Skill '{catalog_id}' cannot downgrade from '{installed_version}' "
            f"to '{candidate_version}' through the update API"
        )


@dataclass(frozen=True, slots=True)
class InstalledSkill:
    catalog_id: str
    market_id: str
    name: str
    title: str
    version: str
    digest: str
    enabled: bool
    installed_at: str
    updated_at: str
    record: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.record,
            "catalog_id": self.catalog_id,
            "market_id": self.market_id,
            "name": self.name,
            "title": self.title,
            "version": self.version,
            "digest": self.digest,
            "enabled": self.enabled,
            "installed_at": self.installed_at,
            "updated_at": self.updated_at,
        }


def compute_package_digest(skill_content: bytes, market_content: bytes) -> str:
    """Hash exact package bytes with names and lengths to avoid ambiguous concatenation."""
    digest = hashlib.sha256()
    digest.update(b"ambient-agent-skill-package-v1\0")
    for name, content in (("SKILL.md", skill_content), ("market.json", market_content)):
        name_bytes = name.encode("ascii")
        digest.update(len(name_bytes).to_bytes(2, "big"))
        digest.update(name_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


class SkillStore:
    """Transactional installation registry with immutable, content-addressed packages."""

    def __init__(self, workspace_dir: str | Path):
        workspace = Path(workspace_dir).expanduser().absolute()
        if workspace.exists() and (workspace.is_symlink() or not workspace.is_dir()):
            raise OSError(f"Skill workspace must be a real directory: {workspace}")
        workspace.mkdir(parents=True, exist_ok=True)

        state_dir = workspace / ".ambient"
        if state_dir.exists() and (state_dir.is_symlink() or not state_dir.is_dir()):
            raise OSError(f"Skill state directory must be a real directory: {state_dir}")
        state_dir.mkdir(parents=True, exist_ok=True)

        skills_dir = state_dir / "skills"
        packages_dir = skills_dir / "packages"
        for directory in (skills_dir, packages_dir):
            if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
                raise OSError(f"Skill package directory must be a real directory: {directory}")
            directory.mkdir(parents=True, exist_ok=True)

        self.workspace_dir = workspace
        self.state_dir = state_dir
        self.db_path = state_dir / "skills.db"
        self.packages_dir = packages_dir
        self._validate_paths()
        self._initialize()

    def _validate_paths(self) -> None:
        if self.state_dir.is_symlink() or not self.state_dir.is_dir():
            raise OSError(f"Skill state directory must be a real directory: {self.state_dir}")
        if self.packages_dir.is_symlink() or not self.packages_dir.is_dir():
            raise OSError(f"Skill package directory must be a real directory: {self.packages_dir}")
        if self.db_path.exists() and (self.db_path.is_symlink() or not self.db_path.is_file()):
            raise OSError(f"Skill registry database must be a regular file: {self.db_path}")

    def _connect(self) -> sqlite3.Connection:
        self._validate_paths()
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS skill_registry_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    revision INTEGER NOT NULL CHECK (revision >= 0)
                );

                INSERT OR IGNORE INTO skill_registry_meta (singleton, revision)
                VALUES (1, 0);

                CREATE TABLE IF NOT EXISTS skill_installations (
                    catalog_id TEXT PRIMARY KEY,
                    market_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    version TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    record_json TEXT NOT NULL,
                    installed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, *, expected_revision: int | None = None) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if expected_revision is not None:
                if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
                    raise ValueError("expected_revision must be a non-negative integer")
                actual = self._revision(connection)
                if actual != expected_revision:
                    raise SkillRevisionConflict(expected_revision, actual)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT revision FROM skill_registry_meta WHERE singleton = 1"
        ).fetchone()
        if row is None or not isinstance(row["revision"], int) or row["revision"] < 0:
            raise SkillStoreCorruptionError("Skill registry revision state is missing or invalid")
        return int(row["revision"])

    @staticmethod
    def _increment_revision(connection: sqlite3.Connection) -> int:
        connection.execute(
            "UPDATE skill_registry_meta SET revision = revision + 1 WHERE singleton = 1"
        )
        return SkillStore._revision(connection)

    def revision(self) -> int:
        connection = self._connect()
        try:
            return self._revision(connection)
        finally:
            connection.close()

    def install(
        self,
        record: dict[str, Any],
        *,
        skill_content: bytes,
        market_content: bytes,
        expected_revision: int | None = None,
    ) -> InstalledSkill:
        normalized_record = self._validate_install_input(record, skill_content, market_content)
        canonical_record = _canonical_json(normalized_record)
        digest = normalized_record["digest"]
        now = datetime.now(UTC).isoformat()

        with self._transaction(expected_revision=expected_revision) as connection:
            existing_row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (normalized_record["catalog_id"],),
            ).fetchone()
            market_owner = connection.execute(
                "SELECT catalog_id FROM skill_installations WHERE market_id = ?",
                (normalized_record["market_id"],),
            ).fetchone()
            if market_owner is not None and market_owner["catalog_id"] != normalized_record["catalog_id"]:
                raise SkillStoreError(
                    f"Market id '{normalized_record['market_id']}' is already installed as "
                    f"'{market_owner['catalog_id']}'"
                )
            if existing_row is not None and existing_row["market_id"] != normalized_record["market_id"]:
                raise SkillStoreError(
                    f"Catalog id '{normalized_record['catalog_id']}' is already owned by a different market item"
                )

            if existing_row is not None:
                existing = self._row_to_installed(existing_row)
                if existing.digest == digest and existing_row["record_json"] == canonical_record:
                    self._ensure_package(skill_content, market_content, digest)
                    return existing
                version_precedence = compare_semver(
                    normalized_record["version"],
                    existing.version,
                )
                if version_precedence == 0 and existing.digest != digest:
                    raise SkillVersionConflict(
                        existing.catalog_id,
                        existing.version,
                        existing.digest,
                        digest,
                    )
                if version_precedence < 0:
                    raise SkillDowngradeConflict(
                        existing.catalog_id,
                        existing.version,
                        normalized_record["version"],
                    )
                enabled = int(existing.enabled)
                installed_at = existing.installed_at
            else:
                enabled = 1
                installed_at = now

            self._ensure_package(skill_content, market_content, digest)

            connection.execute(
                """
                INSERT INTO skill_installations (
                    catalog_id, market_id, name, title, version, digest, enabled,
                    record_json, installed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                    market_id = excluded.market_id,
                    name = excluded.name,
                    title = excluded.title,
                    version = excluded.version,
                    digest = excluded.digest,
                    enabled = excluded.enabled,
                    record_json = excluded.record_json,
                    installed_at = excluded.installed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_record["catalog_id"],
                    normalized_record["market_id"],
                    normalized_record["name"],
                    normalized_record["title"],
                    normalized_record["version"],
                    digest,
                    enabled,
                    canonical_record,
                    installed_at,
                    now,
                ),
            )
            self._increment_revision(connection)
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (normalized_record["catalog_id"],),
            ).fetchone()
            if row is None:
                raise SkillStoreCorruptionError("Installed skill disappeared during its transaction")
            return self._row_to_installed(row)

    def set_enabled(
        self,
        catalog_id: str,
        enabled: bool,
        *,
        expected_revision: int | None = None,
    ) -> InstalledSkill:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        now = datetime.now(UTC).isoformat()
        with self._transaction(expected_revision=expected_revision) as connection:
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
            if row is None:
                raise SkillNotInstalledError(catalog_id)
            current = self._row_to_installed(row)
            # Recovery operations must remain available for a damaged package.
            # Enabling is fail-closed; disabling never loads package content.
            if enabled:
                self._verify_installed_package(current)
            if current.enabled == enabled:
                return current
            connection.execute(
                "UPDATE skill_installations SET enabled = ?, updated_at = ? WHERE catalog_id = ?",
                (int(enabled), now, catalog_id),
            )
            self._increment_revision(connection)
            updated = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
            if updated is None:
                raise SkillStoreCorruptionError("Installed skill disappeared during its transaction")
            return self._row_to_installed(updated)

    def uninstall(self, catalog_id: str, *, expected_revision: int | None = None) -> bool:
        with self._transaction(expected_revision=expected_revision) as connection:
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
            if row is None:
                return False
            self._row_to_installed(row)
            connection.execute("DELETE FROM skill_installations WHERE catalog_id = ?", (catalog_id,))
            self._increment_revision(connection)

        # Keep immutable content-addressed packages as a cache. Synchronous GC
        # here races a reinstall between the registry commit and filesystem
        # deletion. A future collector needs a lock/lease and a grace period.
        return True

    def get(self, catalog_id: str, *, verify_package: bool = True) -> InstalledSkill | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        installed = self._row_to_installed(row)
        if verify_package:
            self._verify_installed_package(installed)
        return installed

    def get_by_market_id(self, market_id: str, *, verify_package: bool = True) -> InstalledSkill | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE market_id = ?",
                (market_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        installed = self._row_to_installed(row)
        if verify_package:
            self._verify_installed_package(installed)
        return installed

    def list(self, *, verify_packages: bool = True) -> list[InstalledSkill]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM skill_installations ORDER BY catalog_id"
            ).fetchall()
        finally:
            connection.close()
        installed = [self._row_to_installed(row) for row in rows]
        if verify_packages:
            for item in installed:
                self._verify_installed_package(item)
        return installed

    def state(self, *, verify_packages: bool = True) -> dict[str, Any]:
        return {
            "revision": self.revision(),
            "items": [item.as_dict() for item in self.list(verify_packages=verify_packages)],
        }

    def read_manifest(self, installed: InstalledSkill | str) -> SkillManifest:
        item = self.get(installed) if isinstance(installed, str) else installed
        if item is None:
            raise SkillNotInstalledError(str(installed))
        package_dir = self._verify_installed_package(item)
        try:
            content = (package_dir / "SKILL.md").read_bytes()
        except OSError as exc:
            raise SkillPackageIntegrityError(f"Unable to read installed package '{item.catalog_id}'") from exc
        try:
            return SkillManifest.from_bytes(content, expected_name=item.name)
        except SkillManifestError as exc:
            raise SkillPackageIntegrityError(
                f"Installed package '{item.catalog_id}' contains an invalid SKILL.md"
            ) from exc

    def package_path(self, digest: str) -> Path:
        match = _DIGEST_PATTERN.fullmatch(digest)
        if match is None:
            raise ValueError("Skill digest must use the form sha256:<64 lowercase hex characters>")
        return self.packages_dir / match.group(1)

    def _validate_install_input(
        self,
        record: dict[str, Any],
        skill_content: bytes,
        market_content: bytes,
    ) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise ValueError("Skill installation record must be an object")
        if not isinstance(skill_content, bytes) or not isinstance(market_content, bytes):
            raise ValueError("Skill package content must be bytes")
        if len(market_content) > MAX_MARKET_FILE_BYTES:
            raise ValueError(f"market.json exceeds the {MAX_MARKET_FILE_BYTES}-byte limit")
        try:
            parsed_market = json.loads(market_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("market.json must be valid UTF-8 JSON") from exc
        if not isinstance(parsed_market, dict):
            raise ValueError("market.json must contain a JSON object")

        normalized = dict(record)
        for field in ("catalog_id", "market_id", "name", "title", "version", "digest"):
            value = normalized.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Skill installation field '{field}' must be a non-empty string")
            if value != value.strip():
                raise ValueError(f"Skill installation field '{field}' must not contain surrounding whitespace")
        parse_semver(normalized["version"])

        manifest = SkillManifest.from_bytes(skill_content, expected_name=normalized["name"])
        if manifest.name != normalized["name"]:
            raise ValueError("Skill installation name does not match SKILL.md")
        computed_digest = compute_package_digest(skill_content, market_content)
        if normalized["digest"] != computed_digest:
            raise SkillPackageIntegrityError("Skill installation digest does not match package content")
        _canonical_json(normalized)
        return normalized

    @staticmethod
    def _row_to_installed(row: sqlite3.Row) -> InstalledSkill:
        try:
            record = json.loads(row["record_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise SkillStoreCorruptionError(
                f"Installed skill '{row['catalog_id']}' has invalid JSON state"
            ) from exc
        if not isinstance(record, dict):
            raise SkillStoreCorruptionError(
                f"Installed skill '{row['catalog_id']}' JSON state must be an object"
            )
        expected = {
            "catalog_id": row["catalog_id"],
            "market_id": row["market_id"],
            "name": row["name"],
            "title": row["title"],
            "version": row["version"],
            "digest": row["digest"],
        }
        for field, value in expected.items():
            if record.get(field) != value:
                raise SkillStoreCorruptionError(
                    f"Installed skill '{row['catalog_id']}' has inconsistent field '{field}'"
                )
        if row["enabled"] not in (0, 1):
            raise SkillStoreCorruptionError(
                f"Installed skill '{row['catalog_id']}' has invalid enabled state"
            )
        return InstalledSkill(
            catalog_id=row["catalog_id"],
            market_id=row["market_id"],
            name=row["name"],
            title=row["title"],
            version=row["version"],
            digest=row["digest"],
            enabled=bool(row["enabled"]),
            installed_at=row["installed_at"],
            updated_at=row["updated_at"],
            record=record,
        )

    def _ensure_package(self, skill_content: bytes, market_content: bytes, digest: str) -> Path:
        destination = self.package_path(digest)
        if destination.exists():
            self._verify_package_dir(destination, digest)
            return destination
        if destination.is_symlink():
            raise SkillPackageIntegrityError(f"Package destination is an unsafe link: {destination}")

        staging_path = Path(tempfile.mkdtemp(prefix=".install-", dir=self.packages_dir))
        try:
            _write_and_sync(staging_path / "SKILL.md", skill_content)
            _write_and_sync(staging_path / "market.json", market_content)
            _fsync_directory(staging_path)
            self._verify_package_dir(staging_path, digest)
            try:
                os.replace(staging_path, destination)
            except OSError:
                # Another process may have published the same immutable package first.
                if not destination.exists():
                    raise
                self._verify_package_dir(destination, digest)
            else:
                # Persist the directory rename before the SQLite installation
                # transaction is allowed to commit.
                _fsync_directory(self.packages_dir)
            return destination
        finally:
            if staging_path.exists():
                shutil.rmtree(staging_path)

    def _verify_installed_package(self, installed: InstalledSkill) -> Path:
        package_dir = self.package_path(installed.digest)
        self._verify_package_dir(package_dir, installed.digest)
        return package_dir

    def _verify_package_dir(self, package_dir: Path, expected_digest: str) -> None:
        if package_dir.is_symlink() or not package_dir.is_dir():
            raise SkillPackageIntegrityError(f"Installed package directory is missing or unsafe: {package_dir}")
        try:
            children = list(package_dir.iterdir())
        except OSError as exc:
            raise SkillPackageIntegrityError(f"Unable to inspect installed package: {package_dir}") from exc
        if {child.name for child in children} != _PACKAGE_FILES:
            raise SkillPackageIntegrityError(f"Installed package has unexpected or missing files: {package_dir}")
        if any(child.is_symlink() or not child.is_file() for child in children):
            raise SkillPackageIntegrityError(f"Installed package contains an unsafe file: {package_dir}")
        try:
            skill_content = (package_dir / "SKILL.md").read_bytes()
            market_content = (package_dir / "market.json").read_bytes()
        except OSError as exc:
            raise SkillPackageIntegrityError(f"Unable to read installed package: {package_dir}") from exc
        actual_digest = compute_package_digest(skill_content, market_content)
        if actual_digest != expected_digest:
            raise SkillPackageIntegrityError(
                f"Installed package digest mismatch: expected {expected_digest}, found {actual_digest}"
            )

def _canonical_json(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Skill installation record must be JSON serializable") from exc


def _write_and_sync(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    """Persist directory entries when the host filesystem supports it."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if os.name == "nt" or exc.errno in {errno.EINVAL, errno.ENOTSUP}:
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if os.name != "nt" and exc.errno not in {errno.EINVAL, errno.ENOTSUP}:
            raise
    finally:
        os.close(descriptor)
