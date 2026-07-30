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
SKILL_ACTIVATION_POLICIES = frozenset({"none", "explicit_only", "implicit"})
SKILL_AUTHORIZATION_STATES = frozenset({"trusted", "quarantined", "authorized"})


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


class SkillAuthorizationRequiredError(SkillStoreError):
    """A quarantined installation cannot be enabled or selected."""

    def __init__(self, catalog_id: str):
        self.catalog_id = catalog_id
        super().__init__(f"Skill '{catalog_id}' requires authorization for its current digest")


class SkillAuthorizationDigestMismatch(SkillStoreError):
    """An authorization decision was made against stale package bytes."""

    def __init__(self, catalog_id: str, expected_digest: str, actual_digest: str):
        self.catalog_id = catalog_id
        self.expected_digest = expected_digest
        self.actual_digest = actual_digest
        super().__init__(
            f"Skill '{catalog_id}' digest changed: expected '{expected_digest}', "
            f"found '{actual_digest}'"
        )


class SkillTrustedAuthorizationImmutableError(SkillStoreError):
    """Bundled trust is loader-derived and cannot be rewritten as a user grant."""

    def __init__(self, catalog_id: str):
        self.catalog_id = catalog_id
        super().__init__(
            f"Skill '{catalog_id}' has loader-derived bundled trust; use enabled state "
            "to disable it"
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
    authorization_state: str
    activation_policy: str
    authorized_digest: str | None
    registry_revision: int | None
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
            "authorization_state": self.authorization_state,
            "activation_policy": self.activation_policy,
            "authorized_digest": self.authorized_digest,
            "registry_revision": self.registry_revision,
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
                    authorization_state TEXT NOT NULL DEFAULT 'quarantined',
                    activation_policy TEXT NOT NULL DEFAULT 'none',
                    authorized_digest TEXT,
                    record_json TEXT NOT NULL,
                    installed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Serialize additive DDL/backfill across concurrently starting Host
            # processes. A second initializer re-reads the columns only after
            # the first migration commits.
            connection.execute("BEGIN IMMEDIATE")
            self._migrate_authorization_columns(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _migrate_authorization_columns(connection: sqlite3.Connection) -> None:
        """Add authorization state without trusting legacy external installations.

        A pre-authorization registry had only an ``enabled`` bit. Bundled
        records can safely inherit loader-derived trust. Every other legacy
        record is disabled and quarantined, even if it used to be enabled.
        """

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(skill_installations)").fetchall()
        }
        definitions = {
            "authorization_state": "TEXT NOT NULL DEFAULT 'quarantined'",
            "activation_policy": "TEXT NOT NULL DEFAULT 'none'",
            "authorized_digest": "TEXT",
        }
        missing = [name for name in definitions if name not in columns]
        for name in missing:
            connection.execute(
                f"ALTER TABLE skill_installations ADD COLUMN {name} {definitions[name]}"
            )
        if not missing:
            return

        rows = connection.execute(
            "SELECT catalog_id, digest, record_json FROM skill_installations"
        ).fetchall()
        migrated_at = datetime.now(UTC).isoformat()
        for row in rows:
            try:
                record = json.loads(row["record_json"])
            except (TypeError, json.JSONDecodeError):
                record = None
            if isinstance(record, dict) and _record_has_bundled_trust(record):
                connection.execute(
                    """
                    UPDATE skill_installations
                    SET authorization_state = 'trusted',
                        activation_policy = 'implicit',
                        authorized_digest = ?,
                        updated_at = ?
                    WHERE catalog_id = ?
                    """,
                    (row["digest"], migrated_at, row["catalog_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE skill_installations
                    SET enabled = 0,
                        authorization_state = 'quarantined',
                        activation_policy = 'none',
                        authorized_digest = NULL,
                        updated_at = ?
                    WHERE catalog_id = ?
                    """,
                    (migrated_at, row["catalog_id"]),
                )
        if rows:
            SkillStore._increment_revision(connection)

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
                existing = self._row_to_installed(
                    existing_row,
                    registry_revision=self._revision(connection),
                )
                if existing.digest == digest and existing_row["record_json"] == canonical_record:
                    self._ensure_package(skill_content, market_content, digest)
                    return existing
                content_hash_update = _is_content_hash_update(
                    existing.record,
                    normalized_record,
                )
                content_hash_governed = (
                    _record_uses_content_hash(existing.record)
                    or _record_uses_content_hash(normalized_record)
                )
                if content_hash_governed and not content_hash_update:
                    raise SkillVersionConflict(
                        existing.catalog_id,
                        existing.version,
                        existing.digest,
                        digest,
                    )
                if not content_hash_governed:
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
                installed_at = existing.installed_at
                if _record_has_bundled_trust(normalized_record):
                    enabled = int(existing.enabled)
                    authorization_state = "trusted"
                    activation_policy = "implicit"
                    authorized_digest = digest
                else:
                    # A grant is bound to exact bytes. Updating any external
                    # package atomically revokes the old grant and disables the
                    # new, unreviewed content.
                    enabled = 0
                    authorization_state = "quarantined"
                    activation_policy = "none"
                    authorized_digest = None
            else:
                installed_at = now
                if _record_has_bundled_trust(normalized_record):
                    enabled = 1
                    authorization_state = "trusted"
                    activation_policy = "implicit"
                    authorized_digest = digest
                else:
                    enabled = 0
                    authorization_state = "quarantined"
                    activation_policy = "none"
                    authorized_digest = None

            self._ensure_package(skill_content, market_content, digest)

            connection.execute(
                """
                INSERT INTO skill_installations (
                    catalog_id, market_id, name, title, version, digest, enabled,
                    authorization_state, activation_policy, authorized_digest,
                    record_json, installed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(catalog_id) DO UPDATE SET
                    market_id = excluded.market_id,
                    name = excluded.name,
                    title = excluded.title,
                    version = excluded.version,
                    digest = excluded.digest,
                    enabled = excluded.enabled,
                    authorization_state = excluded.authorization_state,
                    activation_policy = excluded.activation_policy,
                    authorized_digest = excluded.authorized_digest,
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
                    authorization_state,
                    activation_policy,
                    authorized_digest,
                    canonical_record,
                    installed_at,
                    now,
                ),
            )
            revision = self._increment_revision(connection)
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (normalized_record["catalog_id"],),
            ).fetchone()
            if row is None:
                raise SkillStoreCorruptionError("Installed skill disappeared during its transaction")
            return self._row_to_installed(row, registry_revision=revision)

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
            revision = self._revision(connection)
            current = self._row_to_installed(row, registry_revision=revision)
            # Recovery operations must remain available for a damaged package.
            # Enabling is fail-closed; disabling never loads package content.
            if enabled:
                if current.authorization_state == "quarantined":
                    raise SkillAuthorizationRequiredError(catalog_id)
                self._verify_installed_package(current)
            if current.enabled == enabled:
                return current
            connection.execute(
                "UPDATE skill_installations SET enabled = ?, updated_at = ? WHERE catalog_id = ?",
                (int(enabled), now, catalog_id),
            )
            revision = self._increment_revision(connection)
            updated = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
            if updated is None:
                raise SkillStoreCorruptionError("Installed skill disappeared during its transaction")
            return self._row_to_installed(updated, registry_revision=revision)

    def set_authorization(
        self,
        catalog_id: str,
        activation_policy: str,
        *,
        expected_digest: str,
        expected_revision: int | None = None,
    ) -> InstalledSkill:
        """Authorize or revoke context injection for the exact installed bytes.

        Authorization is a control-plane decision. It grants only
        ``agent.context.inject`` and never interprets ``allowed-tools`` as a
        capability grant.
        """

        if activation_policy not in SKILL_ACTIVATION_POLICIES:
            raise ValueError(
                "activation_policy must be one of: none, explicit_only, implicit"
            )
        if not isinstance(expected_digest, str) or _DIGEST_PATTERN.fullmatch(expected_digest) is None:
            raise ValueError(
                "expected_digest must use the form sha256:<64 lowercase hex characters>"
            )
        now = datetime.now(UTC).isoformat()
        with self._transaction(expected_revision=expected_revision) as connection:
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
            if row is None:
                raise SkillNotInstalledError(catalog_id)
            revision = self._revision(connection)
            current = self._row_to_installed(row, registry_revision=revision)
            if current.digest != expected_digest:
                raise SkillAuthorizationDigestMismatch(
                    catalog_id,
                    expected_digest,
                    current.digest,
                )

            if current.authorization_state == "trusted":
                if activation_policy != "implicit":
                    raise SkillTrustedAuthorizationImmutableError(catalog_id)
                if current.enabled:
                    return current
                self._verify_installed_package(current)
                next_state = "trusted"
                next_policy = "implicit"
                next_authorized_digest: str | None = current.digest
                next_enabled = 1
            elif activation_policy == "none":
                next_state = "quarantined"
                next_policy = "none"
                next_authorized_digest = None
                next_enabled = 0
                if (
                    current.authorization_state == next_state
                    and current.activation_policy == next_policy
                    and current.authorized_digest is None
                    and not current.enabled
                ):
                    return current
            else:
                self._verify_installed_package(current)
                next_state = "authorized"
                next_policy = activation_policy
                next_authorized_digest = current.digest
                next_enabled = 1
                if (
                    current.authorization_state == next_state
                    and current.activation_policy == next_policy
                    and current.authorized_digest == next_authorized_digest
                    and current.enabled
                ):
                    return current

            connection.execute(
                """
                UPDATE skill_installations
                SET enabled = ?,
                    authorization_state = ?,
                    activation_policy = ?,
                    authorized_digest = ?,
                    updated_at = ?
                WHERE catalog_id = ?
                """,
                (
                    next_enabled,
                    next_state,
                    next_policy,
                    next_authorized_digest,
                    now,
                    catalog_id,
                ),
            )
            revision = self._increment_revision(connection)
            updated = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
            if updated is None:
                raise SkillStoreCorruptionError("Installed skill disappeared during its transaction")
            return self._row_to_installed(updated, registry_revision=revision)

    def uninstall(self, catalog_id: str, *, expected_revision: int | None = None) -> bool:
        return (
            self.uninstall_with_revision(
                catalog_id,
                expected_revision=expected_revision,
            )
            is not None
        )

    def uninstall_with_revision(
        self,
        catalog_id: str,
        *,
        expected_revision: int | None = None,
    ) -> int | None:
        with self._transaction(expected_revision=expected_revision) as connection:
            row = connection.execute(
                "SELECT * FROM skill_installations WHERE catalog_id = ?",
                (catalog_id,),
            ).fetchone()
            if row is None:
                return None
            self._row_to_installed(row)
            connection.execute("DELETE FROM skill_installations WHERE catalog_id = ?", (catalog_id,))
            revision = self._increment_revision(connection)

        # Keep immutable content-addressed packages as a cache. Synchronous GC
        # here races a reinstall between the registry commit and filesystem
        # deletion. A future collector needs a lock/lease and a grace period.
        return revision

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
        _, installed = self.list_with_revision(verify_packages=verify_packages)
        return installed

    def list_with_revision(
        self,
        *,
        verify_packages: bool = True,
    ) -> tuple[int, list[InstalledSkill]]:
        """Read one registry snapshot and its matching CAS revision."""

        connection = self._connect()
        try:
            connection.execute("BEGIN")
            revision = self._revision(connection)
            rows = connection.execute(
                "SELECT * FROM skill_installations ORDER BY catalog_id"
            ).fetchall()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        installed = [
            self._row_to_installed(row, registry_revision=revision)
            for row in rows
        ]
        if verify_packages:
            for item in installed:
                self._verify_installed_package(item)
        return revision, installed

    def state(self, *, verify_packages: bool = True) -> dict[str, Any]:
        revision, items = self.list_with_revision(verify_packages=verify_packages)
        return {
            "revision": revision,
            "items": [item.as_dict() for item in items],
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
        if _record_uses_content_hash(normalized):
            source = normalized.get("catalog_source")
            if not isinstance(source, dict):
                raise ValueError("Content-addressed Skill source metadata is missing")
            source_revision = source.get("source_revision")
            upstream_hash = source.get("upstream_hash")
            if (
                not isinstance(source_revision, str)
                or not source_revision
                or normalized["version"] != source_revision
            ):
                raise ValueError(
                    "Content-addressed Skill version must equal source_revision"
                )
            if (
                not isinstance(upstream_hash, str)
                or _DIGEST_PATTERN.fullmatch(upstream_hash) is None
            ):
                raise ValueError(
                    "Content-addressed Skill upstream_hash must be a SHA-256 digest"
                )
        else:
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
    def _row_to_installed(
        row: sqlite3.Row,
        *,
        registry_revision: int | None = None,
    ) -> InstalledSkill:
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
        authorization_state = row["authorization_state"]
        activation_policy = row["activation_policy"]
        authorized_digest = row["authorized_digest"]
        if authorization_state not in SKILL_AUTHORIZATION_STATES:
            raise SkillStoreCorruptionError(
                f"Installed skill '{row['catalog_id']}' has invalid authorization state"
            )
        if activation_policy not in SKILL_ACTIVATION_POLICIES:
            raise SkillStoreCorruptionError(
                f"Installed skill '{row['catalog_id']}' has invalid activation policy"
            )
        if authorized_digest is not None and (
            not isinstance(authorized_digest, str)
            or _DIGEST_PATTERN.fullmatch(authorized_digest) is None
        ):
            raise SkillStoreCorruptionError(
                f"Installed skill '{row['catalog_id']}' has invalid authorized digest"
            )
        if authorization_state == "quarantined":
            valid_authorization = (
                activation_policy == "none"
                and authorized_digest is None
                and row["enabled"] == 0
            )
        elif authorization_state == "trusted":
            valid_authorization = (
                _record_has_bundled_trust(record)
                and activation_policy == "implicit"
                and authorized_digest == row["digest"]
            )
        else:
            valid_authorization = (
                not _record_has_bundled_trust(record)
                and activation_policy in {"explicit_only", "implicit"}
                and authorized_digest == row["digest"]
            )
        if not valid_authorization:
            raise SkillStoreCorruptionError(
                f"Installed skill '{row['catalog_id']}' has inconsistent authorization state"
            )
        return InstalledSkill(
            catalog_id=row["catalog_id"],
            market_id=row["market_id"],
            name=row["name"],
            title=row["title"],
            version=row["version"],
            digest=row["digest"],
            enabled=bool(row["enabled"]),
            authorization_state=authorization_state,
            activation_policy=activation_policy,
            authorized_digest=authorized_digest,
            registry_revision=registry_revision,
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


def _record_has_bundled_trust(record: dict[str, Any]) -> bool:
    provenance = record.get("provenance")
    name = record.get("name")
    return (
        isinstance(provenance, dict)
        and isinstance(name, str)
        and record.get("catalog_id") == f"agent-skill:ambient-agent:{name}"
        and provenance.get("trust") == "bundled"
        and provenance.get("verified") is True
        and provenance.get("source") == f"bundled://ambient-agent/{name}"
    )


def _is_content_hash_update(
    installed_record: dict[str, Any],
    candidate_record: dict[str, Any],
) -> bool:
    """Allow an explicit immutable-revision update without inventing SemVer.

    Both records must be owned by the same content-addressed catalog source.
    A byte change under one revision is never an update: it falls through to
    the existing same-version integrity conflict.
    """

    installed_source = installed_record.get("catalog_source")
    candidate_source = candidate_record.get("catalog_source")
    if not isinstance(installed_source, dict) or not isinstance(candidate_source, dict):
        return False
    if (
        installed_source.get("update_strategy") != "content_hash"
        or candidate_source.get("update_strategy") != "content_hash"
        or installed_source.get("id") != candidate_source.get("id")
        or installed_source.get("kind") != candidate_source.get("kind")
    ):
        return False
    installed_revision = installed_source.get("source_revision")
    candidate_revision = candidate_source.get("source_revision")
    installed_hash = installed_source.get("upstream_hash")
    candidate_hash = candidate_source.get("upstream_hash")
    return (
        isinstance(installed_revision, str)
        and isinstance(candidate_revision, str)
        and installed_revision != candidate_revision
        and isinstance(installed_hash, str)
        and _DIGEST_PATTERN.fullmatch(installed_hash) is not None
        and isinstance(candidate_hash, str)
        and _DIGEST_PATTERN.fullmatch(candidate_hash) is not None
    )


def _record_uses_content_hash(record: dict[str, Any]) -> bool:
    source = record.get("catalog_source")
    return (
        isinstance(source, dict)
        and source.get("update_strategy") == "content_hash"
    )


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
