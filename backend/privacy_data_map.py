from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer

from backend.app_manifest import (
    MAX_MANIFEST_BYTES,
    AppManifest,
    ManifestValidationError,
    validate_app_id,
)
from backend.graph_db import GraphSchemaReadError
from backend.models import LLMAuditLog

PRIVACY_DATA_MAP_CONTRACT_VERSION = 1
PLATFORM_NODE_ID = "platform:ambient-agent"
MAX_PROJECTION_TEXT_CODEPOINTS = 200
MAX_UNIQUE_RECORDED_MODEL_TARGETS = 512
MAX_OBSERVED_FLOW_GROUPS = 2_048
MAX_SCHEMA_IDS_SCANNED = 8_192
MAX_AUDIT_LINE_BYTES = 1_048_576
MAX_AUDIT_BYTES_SCANNED = 268_435_456
MAX_AUDIT_PHYSICAL_LINES = 1_000_000
MAX_APP_DIRECTORY_ENTRIES_SCANNED = 8_192
MAX_APP_CANDIDATES_SCANNED = 4_096
MAX_APP_NODES = 2_048
MAX_SCHEMA_NODES = 4_096
MAX_SCHEMA_REFERENCES_SCANNED = 8_192
MAX_DECLARED_ASSOCIATIONS = 8_192
MAX_SCHEMA_ID_MATERIALIZED_CODEPOINTS = 129
_AUDIT_DRAIN_CHUNK_BYTES = 65_536

_STAGE_ALIASES = {
    "chat": "chat",
    "route": "route",
    "plan": "plan",
    "mutation": "mutation",
    "verify": "verify",
    "session_title": "title",
    "title": "title",
}
_REJECTED_UNICODE_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
_NODE_KIND_PRECEDENCE = {
    "platform": 0,
    "recorded_model_target": 1,
    "app": 2,
    "schema": 3,
}
_SAFE_SCHEMA_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_PROJECTION_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class PrivacyDataMapError(RuntimeError):
    """Base class for sanitized Privacy Data Map projection failures."""


class PrivacyDataMapProjectionError(PrivacyDataMapError):
    """Raised when metadata cannot be projected without violating the contract."""


class PrivacyDataMapResourceLimitError(PrivacyDataMapError):
    """Raised when a fixed V1 projection resource limit is exceeded."""


class PrivacyDataMapIdCollisionError(PrivacyDataMapError):
    """Raised when one derived ID maps to more than one canonical tuple."""


class AuditProjectionSourceError(PrivacyDataMapError):
    """Raised when the Audit Log cannot be read to normal exhaustion."""


class AppDeclarationSourceError(PrivacyDataMapError):
    """Raised when App declarations cannot be read safely."""


class GraphSchemaSourceError(PrivacyDataMapError):
    """Raised when graph schema IDs cannot be read safely."""


class _AuditSourceInitiallyMissing(Exception):
    """Internal sentinel for the one valid missing-source transition."""


class SourceError(StrEnum):
    UNREADABLE = "unreadable"


class AuditProjectionRecord(StrictModel):
    timestamp: datetime
    provider: str
    model: str
    stage: str


class AuditSourceHealth(StrictModel):
    valid_record_count: int = Field(default=0, ge=0)
    malformed_json_count: int = Field(default=0, ge=0)
    structurally_invalid_record_count: int = Field(default=0, ge=0)
    projection_ineligible_record_count: int = Field(default=0, ge=0)
    oversized_line_count: int = Field(default=0, ge=0)


class AuditProjectionStream(Protocol):
    def iter_records(self) -> Iterator[AuditProjectionRecord]: ...

    def health_after_exhaustion(self) -> AuditSourceHealth: ...


class WorkspaceAuditProjectionStream:
    def __init__(
        self,
        workspace_dir: str | Path,
        *,
        open_binary: Callable[[], BinaryIO] | None = None,
    ):
        self._workspace_path = Path(workspace_dir).absolute()
        self._workspace_root = self._workspace_path.resolve(strict=False)
        self._audit_path = self._workspace_path / "audit_logs.jsonl"
        self._open_binary = open_binary
        self._state: Literal["unused", "consuming", "exhausted", "failed"] = "unused"
        self._health: AuditSourceHealth | None = None

    def iter_records(self) -> Iterator[AuditProjectionRecord]:
        if self._state != "unused":
            raise PrivacyDataMapProjectionError
        self._state = "consuming"
        return self._consume()

    def health_after_exhaustion(self) -> AuditSourceHealth:
        if self._state != "exhausted" or self._health is None:
            raise PrivacyDataMapProjectionError
        return self._health

    def _consume(self) -> Iterator[AuditProjectionRecord]:
        counters = {
            "valid_record_count": 0,
            "malformed_json_count": 0,
            "structurally_invalid_record_count": 0,
            "projection_ineligible_record_count": 0,
            "oversized_line_count": 0,
        }
        bytes_scanned = 0
        physical_lines = 0
        source_prefix_remaining: int | None = None

        def read_bounded(source: BinaryIO, size: int) -> bytes:
            nonlocal bytes_scanned, source_prefix_remaining
            if source_prefix_remaining is not None:
                if source_prefix_remaining <= 0:
                    return b""
                size = min(size, source_prefix_remaining)
            remaining = MAX_AUDIT_BYTES_SCANNED - bytes_scanned
            chunk = source.readline(min(size, remaining + 1))
            if not isinstance(chunk, bytes):
                raise TypeError("Audit source must be opened in binary mode")
            if len(chunk) > size:
                raise TypeError("Audit source exceeded the requested read bound")
            bytes_scanned += len(chunk)
            if source_prefix_remaining is not None:
                source_prefix_remaining -= len(chunk)
            if bytes_scanned > MAX_AUDIT_BYTES_SCANNED:
                raise PrivacyDataMapResourceLimitError
            return chunk

        try:
            try:
                source_context = (
                    self._open_binary() if self._open_binary is not None else self._open_validated_audit_file()
                )
            except _AuditSourceInitiallyMissing:
                self._health = AuditSourceHealth()
                self._state = "exhausted"
                return

            with source_context as source:
                try:
                    source_prefix_remaining = os.fstat(source.fileno()).st_size
                except (AttributeError, OSError, TypeError, ValueError):
                    source_prefix_remaining = None

                while True:
                    line = read_bounded(source, MAX_AUDIT_LINE_BYTES + 1)
                    if not line:
                        break

                    oversized = not line.endswith(b"\n") and len(line) > MAX_AUDIT_LINE_BYTES
                    if oversized:
                        while not line.endswith(b"\n"):
                            line = read_bounded(source, _AUDIT_DRAIN_CHUNK_BYTES)
                            if not line:
                                break
                        line_payload = b""
                    else:
                        line_payload = line[:-1] if line.endswith(b"\n") else line

                    physical_lines += 1
                    if physical_lines > MAX_AUDIT_PHYSICAL_LINES:
                        raise PrivacyDataMapResourceLimitError
                    if oversized:
                        counters["oversized_line_count"] += 1
                        continue
                    if not line_payload.strip(b" \t\r"):
                        continue

                    try:
                        decoded = json.loads(line_payload)
                    except (ValueError, RecursionError, UnicodeDecodeError):
                        counters["malformed_json_count"] += 1
                        continue
                    if not isinstance(decoded, dict):
                        counters["structurally_invalid_record_count"] += 1
                        continue
                    try:
                        LLMAuditLog.model_validate(decoded)
                    except ValidationError:
                        counters["structurally_invalid_record_count"] += 1
                        continue

                    try:
                        projection_record = _project_audit_metadata(decoded)
                    except PrivacyDataMapProjectionError:
                        counters["projection_ineligible_record_count"] += 1
                        continue

                    counters["valid_record_count"] += 1
                    line = b""
                    line_payload = b""
                    decoded = None
                    yield projection_record
        except OSError:
            self._state = "failed"
            raise AuditProjectionSourceError from None
        except BaseException:
            self._state = "failed"
            raise
        else:
            self._health = AuditSourceHealth(**counters)
            self._state = "exhausted"

    def _open_validated_audit_file(self) -> BinaryIO:
        if _is_link_or_junction(self._workspace_path) or _is_link_or_junction(self._audit_path):
            raise OSError

        try:
            source_stat = self._audit_path.stat(follow_symlinks=False)
        except FileNotFoundError:
            raise _AuditSourceInitiallyMissing from None
        resolved_path = self._audit_path.resolve(strict=True)
        if resolved_path.parent != self._workspace_root or not stat.S_ISREG(source_stat.st_mode):
            raise OSError

        source = self._audit_path.open("rb")
        try:
            opened_stat = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or (source_stat.st_dev, source_stat.st_ino) != (opened_stat.st_dev, opened_stat.st_ino)
                or _is_link_or_junction(self._audit_path)
                or self._audit_path.resolve(strict=True) != resolved_path
            ):
                raise OSError
        except BaseException:
            source.close()
            raise
        return source


class AppDeclaration(StrictModel):
    app_id: str
    schema_refs: tuple[str, ...]


class AppDeclarationSnapshot(StrictModel):
    declarations: tuple[AppDeclaration, ...]
    invalid_app_count: int = Field(ge=0)
    source_error: SourceError | None = None


class AppDeclarationSnapshotReader:
    def __init__(self, apps_dir: str | Path):
        self._apps_dir = Path(apps_dir).absolute()

    def read(self) -> AppDeclarationSnapshot:
        try:
            if _is_link_or_junction(self._apps_dir):
                return self._unreadable_snapshot()
            try:
                root_stat = self._apps_dir.stat(follow_symlinks=False)
            except FileNotFoundError:
                return AppDeclarationSnapshot(declarations=(), invalid_app_count=0)
            if not stat.S_ISDIR(root_stat.st_mode):
                return self._unreadable_snapshot()

            apps_root = self._apps_dir.resolve(strict=True)
            root_identity = _filesystem_identity(root_stat)
            resolved_root_stat = apps_root.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(resolved_root_stat.st_mode)
                or _filesystem_identity(resolved_root_stat) != root_identity
            ):
                return self._unreadable_snapshot()

            physical_entry_count = 0
            candidates: list[tuple[Path, tuple[int, int] | None]] = []
            with os.scandir(self._apps_dir) as entries:
                for entry in entries:
                    physical_entry_count += 1
                    if physical_entry_count > MAX_APP_DIRECTORY_ENTRIES_SCANNED:
                        raise PrivacyDataMapResourceLimitError
                    if entry.name.startswith("."):
                        continue
                    candidate = self._apps_dir / entry.name
                    if not (
                        entry.is_dir(follow_symlinks=False) or entry.is_symlink() or _is_link_or_junction(candidate)
                    ):
                        continue
                    try:
                        candidate_identity = _filesystem_identity(candidate.stat(follow_symlinks=False))
                    except OSError:
                        candidate_identity = None
                    candidates.append((candidate, candidate_identity))
                    if len(candidates) > MAX_APP_CANDIDATES_SCANNED:
                        raise PrivacyDataMapResourceLimitError
            self._verify_apps_root(apps_root, root_identity)
        except PrivacyDataMapResourceLimitError:
            raise
        except OSError:
            return self._unreadable_snapshot()

        declarations: list[AppDeclaration] = []
        invalid_app_count = 0
        for candidate, candidate_identity in sorted(candidates, key=lambda item: item[0].name):
            try:
                self._verify_apps_root(apps_root, root_identity)
            except OSError:
                return self._unreadable_snapshot()

            try:
                manifest = self._read_candidate_manifest(
                    candidate,
                    candidate_identity,
                    apps_root=apps_root,
                )
            except (OSError, ManifestValidationError):
                invalid_app_count += 1
            else:
                declarations.append(
                    AppDeclaration(
                        app_id=manifest.id,
                        schema_refs=manifest.schema_refs,
                    )
                )

            try:
                self._verify_apps_root(apps_root, root_identity)
            except OSError:
                return self._unreadable_snapshot()

        return AppDeclarationSnapshot(
            declarations=tuple(declarations),
            invalid_app_count=invalid_app_count,
        )

    def _verify_apps_root(
        self,
        apps_root: Path,
        expected_identity: tuple[int, int],
    ) -> None:
        if _is_link_or_junction(self._apps_dir):
            raise OSError
        current_stat = self._apps_dir.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(current_stat.st_mode)
            or _filesystem_identity(current_stat) != expected_identity
            or self._apps_dir.resolve(strict=True) != apps_root
        ):
            raise OSError

    @staticmethod
    def _read_candidate_manifest(
        candidate: Path,
        expected_identity: tuple[int, int] | None,
        *,
        apps_root: Path,
    ) -> AppManifest:
        if expected_identity is None or _is_link_or_junction(candidate):
            raise ManifestValidationError("App directory must not be a link")

        candidate_stat = candidate.stat(follow_symlinks=False)
        if not stat.S_ISDIR(candidate_stat.st_mode) or _filesystem_identity(candidate_stat) != expected_identity:
            raise ManifestValidationError("App directory identity changed")

        resolved_candidate = candidate.resolve(strict=True)
        if resolved_candidate.parent != apps_root:
            raise ManifestValidationError("App directory must remain below the Apps root")
        resolved_candidate_stat = resolved_candidate.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(resolved_candidate_stat.st_mode)
            or _filesystem_identity(resolved_candidate_stat) != expected_identity
        ):
            raise ManifestValidationError("App directory identity changed")

        manifest = _read_manifest_once(
            candidate / "manifest.json",
            expected_app_id=candidate.name,
            expected_parent=resolved_candidate,
        )

        if _is_link_or_junction(candidate):
            raise ManifestValidationError("App directory must not be a link")
        final_candidate_stat = candidate.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(final_candidate_stat.st_mode)
            or _filesystem_identity(final_candidate_stat) != expected_identity
            or candidate.resolve(strict=True) != resolved_candidate
        ):
            raise ManifestValidationError("App directory identity changed")
        return manifest

    @staticmethod
    def _unreadable_snapshot() -> AppDeclarationSnapshot:
        return AppDeclarationSnapshot(
            declarations=(),
            invalid_app_count=0,
            source_error=SourceError.UNREADABLE,
        )


class SchemaIdSnapshot(StrictModel):
    schema_ids: tuple[str, ...]
    unsafe_schema_id_count: int = Field(ge=0)
    source_error: SourceError | None = None


class GraphSchemaIdSource(Protocol):
    def list_schema_ids(
        self,
        *,
        limit: int,
        max_id_codepoints: int,
    ) -> list[str]: ...


class GraphSchemaSnapshotReader:
    def __init__(self, source: GraphSchemaIdSource):
        self._source = source

    def read(self) -> SchemaIdSnapshot:
        try:
            schema_ids = self._source.list_schema_ids(
                limit=MAX_SCHEMA_IDS_SCANNED + 1,
                max_id_codepoints=MAX_SCHEMA_ID_MATERIALIZED_CODEPOINTS,
            )
        except GraphSchemaReadError:
            return SchemaIdSnapshot(
                schema_ids=(),
                unsafe_schema_id_count=0,
                source_error=SourceError.UNREADABLE,
            )

        if len(schema_ids) > MAX_SCHEMA_IDS_SCANNED:
            raise PrivacyDataMapResourceLimitError
        if any(not isinstance(schema_id, str) for schema_id in schema_ids):
            return SchemaIdSnapshot(
                schema_ids=(),
                unsafe_schema_id_count=0,
                source_error=SourceError.UNREADABLE,
            )

        safe_ids = tuple(schema_id for schema_id in schema_ids if _SAFE_SCHEMA_ID.fullmatch(schema_id))
        return SchemaIdSnapshot(
            schema_ids=safe_ids,
            unsafe_schema_id_count=len(schema_ids) - len(safe_ids),
        )


class ObservedWindow(StrictModel):
    from_: datetime = Field(alias="from")
    to: datetime

    @field_serializer("from_", "to")
    def _serialize_timestamp(self, value: datetime) -> str:
        return _serialize_utc_timestamp(value)


class PrivacyMapScope(StrictModel):
    kind: Literal["workspace"] = "workspace"
    observed_window: ObservedWindow | None


class SourceHealth(StrictModel):
    status: Literal["healthy", "degraded"]
    valid_audit_records: int = Field(ge=0)
    malformed_json_records: int = Field(ge=0)
    structurally_invalid_audit_records: int = Field(ge=0)
    projection_ineligible_audit_records: int = Field(ge=0)
    oversized_audit_lines: int = Field(ge=0)
    invalid_app_declarations: int = Field(ge=0)
    unsafe_schema_ids: int = Field(ge=0)
    missing_schema_references: int = Field(ge=0)


class CoverageChannel(StrictModel):
    id: Literal[
        "llm",
        "mcp",
        "http_agent",
        "coding_agent_acp",
        "provider_management",
        "isolated_widget_runtime",
    ]
    observation: Literal["partial", "not_instrumented"]


class Coverage(StrictModel):
    status: Literal["partial"] = "partial"
    channels: tuple[CoverageChannel, ...]


class PlatformNode(StrictModel):
    id: Literal["platform:ambient-agent"] = PLATFORM_NODE_ID
    kind: Literal["platform"] = "platform"
    label: Literal["Ambient Agent"] = "Ambient Agent"


class RecordedModelTargetNode(StrictModel):
    id: str
    kind: Literal["recorded_model_target"] = "recorded_model_target"
    label: str
    location: Literal["unknown"] = "unknown"


class AppNode(StrictModel):
    id: str
    kind: Literal["app"] = "app"
    label: str


class SchemaNode(StrictModel):
    id: str
    kind: Literal["schema"] = "schema"
    label: str


PrivacyMapNode = PlatformNode | RecordedModelTargetNode | AppNode | SchemaNode


class ObservedFlow(StrictModel):
    id: str
    evidence: Literal["observed"] = "observed"
    source_node_id: Literal["platform:ambient-agent"] = PLATFORM_NODE_ID
    destination_node_id: str
    stage: Literal["chat", "route", "plan", "mutation", "verify", "title", "other"]
    count: int = Field(ge=1)
    first_observed_at: datetime
    last_observed_at: datetime

    @field_serializer("first_observed_at", "last_observed_at")
    def _serialize_timestamp(self, value: datetime) -> str:
        return _serialize_utc_timestamp(value)


class DeclaredAssociation(StrictModel):
    id: str
    evidence: Literal["declared"] = "declared"
    app_node_id: str
    schema_node_id: str


class PrivacyMapWarning(StrictModel):
    code: Literal[
        "invalid_app_declarations",
        "malformed_json_records",
        "missing_schema_references",
        "oversized_audit_lines",
        "projection_ineligible_audit_records",
        "structurally_invalid_audit_records",
        "unsafe_schema_ids",
    ]
    count: int = Field(ge=1)


class PrivacyDataMapResponse(StrictModel):
    contract_version: Literal[1] = PRIVACY_DATA_MAP_CONTRACT_VERSION
    generated_at: datetime
    scope: PrivacyMapScope
    source_health: SourceHealth
    coverage: Coverage
    nodes: tuple[PrivacyMapNode, ...]
    observed_flows: tuple[ObservedFlow, ...]
    declared_associations: tuple[DeclaredAssociation, ...]
    warnings: tuple[PrivacyMapWarning, ...]

    @field_serializer("generated_at")
    def _serialize_generated_at(self, value: datetime) -> str:
        return _serialize_utc_timestamp(value)


class _ObservedFlowAggregate:
    __slots__ = ("count", "first_observed_at", "last_observed_at")

    def __init__(self, timestamp: datetime):
        self.count = 1
        self.first_observed_at = timestamp
        self.last_observed_at = timestamp

    def add(self, timestamp: datetime) -> None:
        self.count += 1
        self.first_observed_at = min(self.first_observed_at, timestamp)
        self.last_observed_at = max(self.last_observed_at, timestamp)


class PrivacyDataMapService:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        digest: Callable[[bytes], str] | None = None,
    ):
        self._clock = clock
        self._digest = digest or (lambda value: hashlib.sha256(value).hexdigest())

    def build(
        self,
        audit_stream: AuditProjectionStream,
        app_snapshot: AppDeclarationSnapshot,
        schema_snapshot: SchemaIdSnapshot,
    ) -> PrivacyDataMapResponse:
        generated_at = _canonical_utc(self._clock())
        if app_snapshot.source_error is not None:
            raise AppDeclarationSourceError
        if schema_snapshot.source_error is not None:
            raise GraphSchemaSourceError

        model_nodes: dict[str, RecordedModelTargetNode] = {}
        flow_aggregates: dict[tuple[str, str], _ObservedFlowAggregate] = {}
        canonical_ids: dict[str, tuple[str, ...]] = {}

        for record in audit_stream.iter_records():
            timestamp = _canonical_utc(record.timestamp)
            provider = _normalize_projection_text(record.provider)
            model = _normalize_projection_text(record.model)
            stage = _normalize_stage(record.stage)
            model_target_id = self._derived_id(
                "model_target",
                ("privacy-map-v1", "recorded_model_target", provider, model),
                canonical_ids,
            )
            if model_target_id not in model_nodes:
                if len(model_nodes) >= MAX_UNIQUE_RECORDED_MODEL_TARGETS:
                    raise PrivacyDataMapResourceLimitError
                model_nodes[model_target_id] = RecordedModelTargetNode(
                    id=model_target_id,
                    label=f"{provider} · {model}",
                )

            flow_key = (model_target_id, stage)
            aggregate = flow_aggregates.get(flow_key)
            if aggregate is None:
                if len(flow_aggregates) >= MAX_OBSERVED_FLOW_GROUPS:
                    raise PrivacyDataMapResourceLimitError
                flow_aggregates[flow_key] = _ObservedFlowAggregate(timestamp)
            else:
                aggregate.add(timestamp)

        audit_health = audit_stream.health_after_exhaustion()
        if audit_health.valid_record_count != sum(aggregate.count for aggregate in flow_aggregates.values()):
            raise PrivacyDataMapProjectionError
        (
            app_nodes,
            schema_nodes,
            declared_associations,
            missing_schema_references,
        ) = self._project_declarations(
            app_snapshot,
            schema_snapshot,
            canonical_ids,
        )

        observed_flows = tuple(
            ObservedFlow(
                id=self._derived_id(
                    "flow",
                    (
                        "privacy-map-v1",
                        "observed_flow",
                        PLATFORM_NODE_ID,
                        model_target_id,
                        stage,
                    ),
                    canonical_ids,
                ),
                destination_node_id=model_target_id,
                stage=stage,
                count=aggregate.count,
                first_observed_at=aggregate.first_observed_at,
                last_observed_at=aggregate.last_observed_at,
            )
            for (model_target_id, stage), aggregate in sorted(
                flow_aggregates.items(),
                key=lambda item: (item[0][0], item[0][1]),
            )
        )
        observed_timestamps = [
            timestamp for flow in observed_flows for timestamp in (flow.first_observed_at, flow.last_observed_at)
        ]
        observed_window = (
            ObservedWindow(from_=min(observed_timestamps), to=max(observed_timestamps)) if observed_timestamps else None
        )
        source_health, warnings = _source_health(
            audit_health,
            app_snapshot,
            schema_snapshot,
            missing_schema_references=missing_schema_references,
        )
        nodes: tuple[PrivacyMapNode, ...] = (
            PlatformNode(),
            *sorted(
                (
                    *model_nodes.values(),
                    *app_nodes.values(),
                    *schema_nodes.values(),
                ),
                key=lambda node: (_NODE_KIND_PRECEDENCE[node.kind], node.id),
            ),
        )

        return PrivacyDataMapResponse(
            generated_at=generated_at,
            scope=PrivacyMapScope(observed_window=observed_window),
            source_health=source_health,
            coverage=_coverage(),
            nodes=nodes,
            observed_flows=observed_flows,
            declared_associations=declared_associations,
            warnings=warnings,
        )

    def _project_declarations(
        self,
        app_snapshot: AppDeclarationSnapshot,
        schema_snapshot: SchemaIdSnapshot,
        canonical_ids: dict[str, tuple[str, ...]],
    ) -> tuple[
        dict[str, AppNode],
        dict[str, SchemaNode],
        tuple[DeclaredAssociation, ...],
        int,
    ]:
        if len(schema_snapshot.schema_ids) != len(set(schema_snapshot.schema_ids)):
            raise PrivacyDataMapProjectionError
        if any(not _SAFE_SCHEMA_ID.fullmatch(schema_id) for schema_id in schema_snapshot.schema_ids):
            raise PrivacyDataMapProjectionError

        registered_schema_ids = set(schema_snapshot.schema_ids)
        app_nodes: dict[str, AppNode] = {}
        schema_nodes: dict[str, SchemaNode] = {}
        declared_associations: list[DeclaredAssociation] = []
        seen_app_ids: set[str] = set()
        missing_schema_references = 0
        schema_references_scanned = 0

        for declaration in sorted(
            app_snapshot.declarations,
            key=lambda item: item.app_id,
        ):
            try:
                validate_app_id(declaration.app_id)
            except ManifestValidationError:
                raise PrivacyDataMapProjectionError from None
            if declaration.app_id in seen_app_ids:
                raise PrivacyDataMapProjectionError
            seen_app_ids.add(declaration.app_id)
            schema_references_scanned += len(declaration.schema_refs)
            if schema_references_scanned > MAX_SCHEMA_REFERENCES_SCANNED:
                raise PrivacyDataMapResourceLimitError
            if len(declaration.schema_refs) != len(set(declaration.schema_refs)):
                raise PrivacyDataMapProjectionError
            if len(app_nodes) >= MAX_APP_NODES:
                raise PrivacyDataMapResourceLimitError

            app_node_id = f"app:{declaration.app_id}"
            app_nodes[app_node_id] = AppNode(
                id=app_node_id,
                label=declaration.app_id,
            )
            for schema_id in sorted(declaration.schema_refs):
                if schema_id not in registered_schema_ids:
                    missing_schema_references += 1
                    continue

                schema_node_id = f"schema:{schema_id}"
                if schema_node_id not in schema_nodes:
                    if len(schema_nodes) >= MAX_SCHEMA_NODES:
                        raise PrivacyDataMapResourceLimitError
                    schema_nodes[schema_node_id] = SchemaNode(
                        id=schema_node_id,
                        label=schema_id,
                    )
                if len(declared_associations) >= MAX_DECLARED_ASSOCIATIONS:
                    raise PrivacyDataMapResourceLimitError
                declared_associations.append(
                    DeclaredAssociation(
                        id=self._derived_id(
                            "declaration",
                            (
                                "privacy-map-v1",
                                "declared_association",
                                declaration.app_id,
                                schema_id,
                            ),
                            canonical_ids,
                        ),
                        app_node_id=app_node_id,
                        schema_node_id=schema_node_id,
                    )
                )

        return (
            app_nodes,
            schema_nodes,
            tuple(
                sorted(
                    declared_associations,
                    key=lambda association: (
                        association.app_node_id,
                        association.schema_node_id,
                        association.id,
                    ),
                )
            ),
            missing_schema_references,
        )

    def _derived_id(
        self,
        prefix: Literal["model_target", "flow", "declaration"],
        canonical_tuple: tuple[str, ...],
        canonical_ids: dict[str, tuple[str, ...]],
    ) -> str:
        serialized = json.dumps(
            canonical_tuple,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        derived_id = f"{prefix}:{self._digest(serialized)}"
        existing = canonical_ids.get(derived_id)
        if existing is not None and existing != canonical_tuple:
            raise PrivacyDataMapIdCollisionError
        canonical_ids[derived_id] = canonical_tuple
        return derived_id


def _project_audit_metadata(decoded: dict[str, object]) -> AuditProjectionRecord:
    timestamp_value = decoded.get("timestamp")
    provider = decoded.get("provider")
    model = decoded.get("model")
    stage = decoded.get("stage", "chat")
    if (
        not isinstance(timestamp_value, str)
        or not _PROJECTION_TIMESTAMP.fullmatch(timestamp_value)
        or timestamp_value.endswith("-00:00")
        or not isinstance(provider, str)
        or not isinstance(model, str)
        or not isinstance(stage, str)
    ):
        raise PrivacyDataMapProjectionError
    try:
        timestamp = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
    except ValueError:
        raise PrivacyDataMapProjectionError from None

    return AuditProjectionRecord(
        timestamp=_canonical_utc(timestamp),
        provider=_normalize_projection_text(provider),
        model=_normalize_projection_text(model),
        stage=_normalize_stage(stage),
    )


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True

    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        return bool(is_junction())
    if os.name != "nt":
        return False

    try:
        return path.lstat().st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except (AttributeError, OSError, ValueError):
        return False


def _filesystem_identity(source_stat: os.stat_result) -> tuple[int, int]:
    return source_stat.st_dev, source_stat.st_ino


def _open_manifest_binary(path: Path) -> BinaryIO:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _read_manifest_once(
    path: Path,
    *,
    expected_app_id: str,
    expected_parent: Path,
) -> AppManifest:
    if _is_link_or_junction(path):
        raise ManifestValidationError("Manifest path must not be a link")

    source_stat = path.stat(follow_symlinks=False)
    source_identity = _filesystem_identity(source_stat)
    if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size > MAX_MANIFEST_BYTES:
        raise ManifestValidationError("Manifest must be a bounded regular file")

    resolved_path = path.resolve(strict=True)
    if resolved_path.parent != expected_parent:
        raise ManifestValidationError("Manifest must remain inside its App directory")

    with _open_manifest_binary(path) as source:
        opened_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(opened_stat.st_mode) or _filesystem_identity(opened_stat) != source_identity:
            raise ManifestValidationError("Manifest identity changed")
        payload = source.read(MAX_MANIFEST_BYTES + 1)
        if not isinstance(payload, bytes) or len(payload) > MAX_MANIFEST_BYTES:
            raise ManifestValidationError("Manifest exceeds its maximum size")

    if _is_link_or_junction(path):
        raise ManifestValidationError("Manifest path must not be a link")
    final_stat = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(final_stat.st_mode)
        or _filesystem_identity(final_stat) != source_identity
        or path.resolve(strict=True) != resolved_path
    ):
        raise ManifestValidationError("Manifest identity changed")

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise ManifestValidationError("manifest must be readable UTF-8 containing valid JSON") from None
    return AppManifest.from_dict(decoded, expected_app_id=expected_app_id)


def _canonical_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PrivacyDataMapProjectionError
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError):
        raise PrivacyDataMapProjectionError


def _serialize_utc_timestamp(value: datetime) -> str:
    canonical = _canonical_utc(value)
    timestamp = (
        f"{canonical.year:04d}-{canonical.month:02d}-{canonical.day:02d}"
        f"T{canonical.hour:02d}:{canonical.minute:02d}:{canonical.second:02d}"
    )
    if canonical.microsecond:
        timestamp += f".{canonical.microsecond:06d}".rstrip("0")
    return f"{timestamp}Z"


def _normalize_projection_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(character) in _REJECTED_UNICODE_CATEGORIES for character in normalized):
        raise PrivacyDataMapProjectionError
    normalized = normalized.strip()
    if not normalized or len(normalized) > MAX_PROJECTION_TEXT_CODEPOINTS:
        raise PrivacyDataMapProjectionError
    return normalized


def _normalize_stage(value: str) -> Literal["chat", "route", "plan", "mutation", "verify", "title", "other"]:
    if not value.strip():
        raise PrivacyDataMapProjectionError
    return _STAGE_ALIASES.get(value, "other")


def _source_health(
    audit_health: AuditSourceHealth,
    app_snapshot: AppDeclarationSnapshot,
    schema_snapshot: SchemaIdSnapshot,
    *,
    missing_schema_references: int,
) -> tuple[SourceHealth, tuple[PrivacyMapWarning, ...]]:
    counts = {
        "invalid_app_declarations": app_snapshot.invalid_app_count,
        "malformed_json_records": audit_health.malformed_json_count,
        "missing_schema_references": missing_schema_references,
        "oversized_audit_lines": audit_health.oversized_line_count,
        "projection_ineligible_audit_records": audit_health.projection_ineligible_record_count,
        "structurally_invalid_audit_records": audit_health.structurally_invalid_record_count,
        "unsafe_schema_ids": schema_snapshot.unsafe_schema_id_count,
    }
    degraded = any(counts.values())
    health = SourceHealth(
        status="degraded" if degraded else "healthy",
        valid_audit_records=audit_health.valid_record_count,
        malformed_json_records=audit_health.malformed_json_count,
        structurally_invalid_audit_records=audit_health.structurally_invalid_record_count,
        projection_ineligible_audit_records=audit_health.projection_ineligible_record_count,
        oversized_audit_lines=audit_health.oversized_line_count,
        invalid_app_declarations=app_snapshot.invalid_app_count,
        unsafe_schema_ids=schema_snapshot.unsafe_schema_id_count,
        missing_schema_references=missing_schema_references,
    )
    warnings = tuple(PrivacyMapWarning(code=code, count=count) for code, count in sorted(counts.items()) if count)
    return health, warnings


def _coverage() -> Coverage:
    return Coverage(
        channels=(
            CoverageChannel(id="llm", observation="partial"),
            CoverageChannel(id="mcp", observation="not_instrumented"),
            CoverageChannel(id="http_agent", observation="not_instrumented"),
            CoverageChannel(id="coding_agent_acp", observation="not_instrumented"),
            CoverageChannel(id="provider_management", observation="not_instrumented"),
            CoverageChannel(id="isolated_widget_runtime", observation="not_instrumented"),
        )
    )
