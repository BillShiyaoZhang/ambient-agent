import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from io import BytesIO

import pytest
from pydantic import ValidationError

import backend.privacy_data_map as privacy_data_map
from backend.graph_db import GraphSchemaReadError
from backend.privacy_data_map import (
    MAX_SCHEMA_IDS_SCANNED,
    AppDeclaration,
    AppDeclarationSnapshot,
    AppDeclarationSnapshotReader,
    AppDeclarationSourceError,
    AuditProjectionRecord,
    AuditProjectionSourceError,
    AuditSourceHealth,
    GraphSchemaSourceError,
    GraphSchemaSnapshotReader,
    PrivacyDataMapProjectionError,
    PrivacyDataMapIdCollisionError,
    PrivacyDataMapResourceLimitError,
    PrivacyDataMapService,
    SchemaIdSnapshot,
    SourceError,
    WorkspaceAuditProjectionStream,
)


class InMemoryAuditProjectionStream:
    def __init__(self, records: list[AuditProjectionRecord], health: AuditSourceHealth):
        self._records = records
        self._health = health
        self._exhausted = False

    def iter_records(self):
        if self._exhausted:
            raise AssertionError("Audit projection streams are one-shot")
        try:
            yield from self._records
        finally:
            self._exhausted = True

    def health_after_exhaustion(self) -> AuditSourceHealth:
        if not self._exhausted:
            raise AssertionError("Health is unavailable before stream exhaustion")
        return self._health


class FailIfConsumedAuditProjectionStream:
    def iter_records(self):
        raise AssertionError("Audit stream must not be consumed")

    def health_after_exhaustion(self):
        raise AssertionError("Audit health must not be requested")


def empty_app_snapshot() -> AppDeclarationSnapshot:
    return AppDeclarationSnapshot(declarations=(), invalid_app_count=0)


def empty_schema_snapshot() -> SchemaIdSnapshot:
    return SchemaIdSnapshot(schema_ids=(), unsafe_schema_id_count=0)


def empty_audit_stream() -> InMemoryAuditProjectionStream:
    return InMemoryAuditProjectionStream(records=[], health=AuditSourceHealth())


def _create_windows_junction(link, target):
    if os.name != "nt":
        pytest.skip("NTFS junctions are only available on Windows")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"NTFS junctions are unavailable in this environment: {result.stderr.strip()}")


class StubSchemaIdSource:
    def __init__(self, schema_ids=None, error=None):
        self.schema_ids = schema_ids or []
        self.error = error
        self.requested_limits = []
        self.requested_max_id_codepoints = []

    def list_schema_ids(self, *, limit, max_id_codepoints):
        self.requested_limits.append(limit)
        self.requested_max_id_codepoints.append(max_id_codepoints)
        if self.error is not None:
            raise self.error
        return self.schema_ids[:limit]


def test_projection_record_rejects_raw_audit_payload_fields():
    with pytest.raises(ValidationError, match="prompt"):
        AuditProjectionRecord(
            timestamp=datetime(2026, 7, 19, tzinfo=UTC),
            provider="OpenAI",
            model="gpt-example",
            stage="chat",
            prompt="raw-canary-must-not-cross-the-boundary",
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-19T08:30:00+08:60",
        "2026-07-19T08:30:00+24:00",
        "2026-07-19T08:30:00.1234567Z",
        "2026-07-19 08:30:00Z",
        "2026-07-19T08:30:00z",
        "2026-07-19T08:30:00-00:00",
    ],
)
def test_projection_metadata_rejects_timestamps_outside_the_exact_grammar(timestamp):
    with pytest.raises(PrivacyDataMapProjectionError):
        privacy_data_map._project_audit_metadata(
            {
                "timestamp": timestamp,
                "provider": "OpenAI",
                "model": "gpt-example",
                "stage": "chat",
            }
        )


def _audit_line(**overrides) -> bytes:
    payload = {
        "id": 1,
        "timestamp": "2026-07-19T08:30:00+08:00",
        "provider": "OpenAI",
        "model": "gpt-example",
        "prompt": "raw-prompt-canary",
        "response": "raw-response-canary",
        "stage": "chat",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _write_manifest(apps_dir, app_id, *, schema_refs=(), capabilities=()):
    app_dir = apps_dir / app_id
    app_dir.mkdir(parents=True)
    manifest = {
        "manifest_version": 2,
        "id": app_id,
        "title": app_id,
        "description": "",
        "app_version": "0.1.0",
        "intents": [],
        "schema_refs": list(schema_refs),
        "capabilities": list(capabilities),
    }
    (app_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return app_dir


def test_workspace_audit_projection_stream_classifies_records_without_retaining_payloads(tmp_path):
    audit_file = tmp_path / "audit_logs.jsonl"
    audit_file.write_bytes(
        b"\n"
        + _audit_line()
        + b"{not-json}\n"
        + b"[]\n"
        + _audit_line(prompt=None)
        + _audit_line(timestamp="2026-07-19T08:30:00")
        + _audit_line(stage="")
        + b"\xff\n"
    )
    stream = WorkspaceAuditProjectionStream(tmp_path)

    records = list(stream.iter_records())

    assert records == [
        AuditProjectionRecord(
            timestamp=datetime(2026, 7, 19, 0, 30, tzinfo=UTC),
            provider="OpenAI",
            model="gpt-example",
            stage="chat",
        )
    ]
    assert stream.health_after_exhaustion() == AuditSourceHealth(
        valid_record_count=1,
        malformed_json_count=2,
        structurally_invalid_record_count=2,
        projection_ineligible_record_count=2,
        oversized_line_count=0,
    )
    retained_state = repr(vars(stream))
    assert "raw-prompt-canary" not in retained_state
    assert "raw-response-canary" not in retained_state


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (
            "0001-01-01T23:59:59Z",
            datetime(1, 1, 1, 23, 59, 59, tzinfo=UTC),
        ),
        (
            "2026-07-19T08:30:00.1+08:00",
            datetime(2026, 7, 19, 0, 30, 0, 100_000, tzinfo=UTC),
        ),
        (
            "2026-07-19T08:30:00.123456Z",
            datetime(2026, 7, 19, 8, 30, 0, 123_456, tzinfo=UTC),
        ),
        (
            "9999-12-31T00:00:00Z",
            datetime(9999, 12, 31, tzinfo=UTC),
        ),
    ],
)
def test_workspace_audit_projection_stream_accepts_timestamp_grammar_boundaries(
    tmp_path,
    timestamp,
    expected,
):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(_audit_line(timestamp=timestamp)),
    )

    assert [record.timestamp for record in stream.iter_records()] == [expected]
    assert stream.health_after_exhaustion() == AuditSourceHealth(valid_record_count=1)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-19T08:30:00",
        "2026-07-19 08:30:00Z",
        "2026-07-19T08:30:00z",
        "2026-07-19T08:30:00.1234567Z",
        "2026-07-19T08:30:00-00:00",
        "0001-01-01T00:00:00+23:59",
        "9999-12-31T23:59:59-23:59",
    ],
)
def test_workspace_audit_projection_stream_marks_legacy_compatible_invalid_timestamps_ineligible(
    tmp_path,
    timestamp,
):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(_audit_line(timestamp=timestamp)),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(
        projection_ineligible_record_count=1,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": ""},
        {"provider": "   "},
        {"model": ""},
        {"model": "   "},
        {"stage": ""},
        {"stage": " \t "},
    ],
)
def test_workspace_audit_projection_stream_marks_empty_projection_metadata_ineligible(
    tmp_path,
    overrides,
):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(_audit_line(**overrides)),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(
        projection_ineligible_record_count=1,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": None},
        {"model": None},
        {"stage": None},
        {"stage": 1},
    ],
)
def test_workspace_audit_projection_stream_keeps_structural_type_failures_distinct(
    tmp_path,
    overrides,
):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(_audit_line(**overrides)),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(
        structurally_invalid_record_count=1,
    )


def test_workspace_audit_projection_stream_defaults_a_missing_stage_to_chat(tmp_path):
    payload = json.loads(_audit_line())
    del payload["stage"]
    line = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(line),
    )

    assert [record.stage for record in stream.iter_records()] == ["chat"]
    assert stream.health_after_exhaustion() == AuditSourceHealth(valid_record_count=1)


def test_workspace_audit_projection_stream_releases_raw_payload_before_yield(tmp_path):
    (tmp_path / "audit_logs.jsonl").write_bytes(_audit_line())
    stream = WorkspaceAuditProjectionStream(tmp_path)
    iterator = stream.iter_records()

    assert next(iterator).provider == "OpenAI"

    suspended_state = repr(iterator.gi_frame.f_locals)
    assert "raw-prompt-canary" not in suspended_state
    assert "raw-response-canary" not in suspended_state
    with pytest.raises(StopIteration):
        next(iterator)


def test_valid_prompt_and_response_content_cannot_influence_the_full_projection(tmp_path):
    def clock():
        return datetime(2026, 7, 20, 8, 30, tzinfo=UTC)

    payloads = []

    for prompt, response in (
        ("first-sensitive-prompt-canary", "first-sensitive-response-canary"),
        ("completely different private input", "unrelated private output"),
    ):
        stream = WorkspaceAuditProjectionStream(
            tmp_path,
            open_binary=lambda prompt=prompt, response=response: BytesIO(_audit_line(prompt=prompt, response=response)),
        )
        payloads.append(
            PrivacyDataMapService(clock=clock)
            .build(
                stream,
                empty_app_snapshot(),
                empty_schema_snapshot(),
            )
            .model_dump(mode="json", by_alias=True)
        )

    assert payloads[0] == payloads[1]


def test_workspace_audit_projection_stream_missing_file_is_a_valid_empty_source(tmp_path):
    stream = WorkspaceAuditProjectionStream(tmp_path)

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth()


def test_workspace_audit_projection_stream_does_not_treat_injected_file_not_found_as_an_empty_source(
    tmp_path,
):
    def fail_open():
        raise FileNotFoundError("injected source disappeared")

    stream = WorkspaceAuditProjectionStream(tmp_path, open_binary=fail_open)

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""
    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()


def test_workspace_audit_projection_stream_fails_if_source_disappears_after_initial_stat(
    tmp_path,
    monkeypatch,
):
    audit_path = tmp_path / "audit_logs.jsonl"
    audit_path.write_bytes(_audit_line())
    original_resolve = privacy_data_map.Path.resolve

    def remove_before_resolve(path, *args, **kwargs):
        if path == audit_path and kwargs.get("strict") is True:
            audit_path.unlink()
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(privacy_data_map.Path, "resolve", remove_before_resolve)
    stream = WorkspaceAuditProjectionStream(tmp_path)

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""


def test_workspace_audit_projection_stream_fails_if_source_disappears_before_open(
    tmp_path,
    monkeypatch,
):
    audit_path = tmp_path / "audit_logs.jsonl"
    audit_path.write_bytes(_audit_line())
    original_open = privacy_data_map.Path.open

    def remove_before_open(path, *args, **kwargs):
        if path == audit_path:
            audit_path.unlink()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(privacy_data_map.Path, "open", remove_before_open)
    stream = WorkspaceAuditProjectionStream(tmp_path)

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""


def test_workspace_audit_projection_stream_captures_absolute_workspace_at_construction(tmp_path, monkeypatch):
    original_cwd = tmp_path / "original"
    replacement_cwd = tmp_path / "replacement"
    original_workspace = original_cwd / "workspace"
    replacement_workspace = replacement_cwd / "workspace"
    original_workspace.mkdir(parents=True)
    replacement_workspace.mkdir(parents=True)
    (original_workspace / "audit_logs.jsonl").write_bytes(_audit_line(provider="Original"))
    (replacement_workspace / "audit_logs.jsonl").write_bytes(_audit_line(provider="Replacement"))
    monkeypatch.chdir(original_cwd)
    stream = WorkspaceAuditProjectionStream("workspace")

    monkeypatch.chdir(replacement_cwd)

    assert [record.provider for record in stream.iter_records()] == ["Original"]


def test_workspace_audit_projection_stream_rejects_audit_link_or_junction(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit_logs.jsonl"
    audit_path.write_bytes(_audit_line())
    original_link_check = privacy_data_map._is_link_or_junction

    def classify_audit_path_as_link(path):
        return path == audit_path.resolve() or original_link_check(path)

    monkeypatch.setattr(
        privacy_data_map,
        "_is_link_or_junction",
        classify_audit_path_as_link,
    )
    stream = WorkspaceAuditProjectionStream(tmp_path)

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""
    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()


def test_workspace_audit_projection_stream_rejects_real_windows_workspace_junction(tmp_path):
    workspace_target = tmp_path / "workspace-target"
    workspace_target.mkdir()
    (workspace_target / "audit_logs.jsonl").write_bytes(_audit_line())
    workspace_junction = tmp_path / "workspace-junction"
    _create_windows_junction(workspace_junction, workspace_target)
    stream = WorkspaceAuditProjectionStream(workspace_junction)

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""


def test_workspace_audit_projection_stream_rejects_non_regular_audit_path(tmp_path):
    (tmp_path / "audit_logs.jsonl").mkdir()
    stream = WorkspaceAuditProjectionStream(tmp_path)

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""


def test_workspace_audit_projection_stream_sanitizes_open_failures(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit_logs.jsonl"
    audit_path.write_bytes(_audit_line())
    original_open = privacy_data_map.Path.open

    def fail_audit_open(path, *args, **kwargs):
        if path == audit_path.resolve():
            raise PermissionError("sensitive-open-failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(privacy_data_map.Path, "open", fail_audit_open)
    stream = WorkspaceAuditProjectionStream(tmp_path)

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""


def test_workspace_audit_projection_stream_enforces_one_shot_lifecycle(tmp_path):
    (tmp_path / "audit_logs.jsonl").write_bytes(_audit_line())
    stream = WorkspaceAuditProjectionStream(tmp_path)

    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()

    assert len(list(stream.iter_records())) == 1

    with pytest.raises(PrivacyDataMapProjectionError):
        list(stream.iter_records())


class _RecordingBinarySource(BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.readline_sizes: list[int] = []

    def readline(self, size: int = -1) -> bytes:
        self.readline_sizes.append(size)
        return super().readline(size)


def test_workspace_audit_projection_stream_drains_oversized_lines_without_decoding(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_LINE_BYTES", 32)
    source = _RecordingBinarySource(b"x" * 100 + b"\n")
    stream = WorkspaceAuditProjectionStream(tmp_path, open_binary=lambda: source)

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(oversized_line_count=1)
    assert source.readline_sizes[0] == 33
    assert all(size <= 65_536 for size in source.readline_sizes)


@pytest.mark.parametrize("terminator", [b"", b"\n"])
def test_workspace_audit_projection_stream_accepts_exact_line_byte_limit(tmp_path, monkeypatch, terminator):
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_LINE_BYTES", 8)
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b"x" * 8 + terminator),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(malformed_json_count=1)


@pytest.mark.parametrize("terminator", [b"", b"\n"])
def test_workspace_audit_projection_stream_counts_line_over_byte_limit_as_oversized(tmp_path, monkeypatch, terminator):
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_LINE_BYTES", 8)
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b"x" * 9 + terminator),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(oversized_line_count=1)


def test_workspace_audit_projection_stream_counts_cr_as_part_of_the_crlf_payload_limit(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_LINE_BYTES", 8)

    exact_stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b"x" * 7 + b"\r\n"),
    )
    over_stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b"x" * 8 + b"\r\n"),
    )

    assert list(exact_stream.iter_records()) == []
    assert exact_stream.health_after_exhaustion() == AuditSourceHealth(malformed_json_count=1)
    assert list(over_stream.iter_records()) == []
    assert over_stream.health_after_exhaustion() == AuditSourceHealth(oversized_line_count=1)


def test_workspace_audit_projection_stream_drains_multiple_chunks_then_reads_valid_record(tmp_path, monkeypatch):
    valid_line = _audit_line()
    line_limit = len(valid_line) - 1
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_LINE_BYTES", line_limit)
    monkeypatch.setattr(privacy_data_map, "_AUDIT_DRAIN_CHUNK_BYTES", 3)
    source = _RecordingBinarySource(b"x" * (line_limit + 10) + b"\n" + valid_line)
    stream = WorkspaceAuditProjectionStream(tmp_path, open_binary=lambda: source)

    assert [record.provider for record in stream.iter_records()] == ["OpenAI"]
    assert stream.health_after_exhaustion() == AuditSourceHealth(
        valid_record_count=1,
        oversized_line_count=1,
    )
    assert source.readline_sizes.count(3) >= 4


def test_workspace_audit_projection_stream_counts_oversized_whitespace_before_blank_handling(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_LINE_BYTES", 8)
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b" " * 9 + b"\n"),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(oversized_line_count=1)


def test_workspace_audit_projection_stream_uses_exact_jsonl_blank_whitespace(tmp_path):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b" \t\r\n\v\n\f\n\xc2\xa0\n"),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(malformed_json_count=3)


def test_workspace_audit_projection_stream_counts_parser_recursion_as_malformed(tmp_path, monkeypatch):
    def fail_with_recursion(_payload):
        raise RecursionError("sensitive-parser-state")

    monkeypatch.setattr(privacy_data_map.json, "loads", fail_with_recursion)
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b"{}\n"),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(malformed_json_count=1)


def test_workspace_audit_projection_stream_counts_oversized_json_integer_as_malformed(tmp_path):
    oversized_integer = b"9" * 5_000
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b'{"oversized_integer":' + oversized_integer + b"}\n"),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth(malformed_json_count=1)


class _AppendAfterFirstRead:
    def __init__(self, source, append_path, appended_line):
        self._source = source
        self._append_path = append_path
        self._appended_line = appended_line
        self._read_count = 0

    def __enter__(self):
        self._source.__enter__()
        return self

    def __exit__(self, *args):
        return self._source.__exit__(*args)

    def fileno(self):
        return self._source.fileno()

    def readline(self, size=-1):
        line = self._source.readline(size)
        self._read_count += 1
        if self._read_count == 1:
            with self._append_path.open("ab") as target:
                target.write(self._appended_line)
        return line


def test_workspace_audit_projection_stream_defers_bytes_appended_after_open_boundary(tmp_path):
    audit_path = tmp_path / "audit_logs.jsonl"
    audit_path.write_bytes(_audit_line(provider="Initial"))

    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: _AppendAfterFirstRead(
            audit_path.open("rb"),
            audit_path,
            _audit_line(provider="Appended"),
        ),
    )

    assert [record.provider for record in stream.iter_records()] == ["Initial"]
    assert stream.health_after_exhaustion() == AuditSourceHealth(valid_record_count=1)
    assert b"Appended" in audit_path.read_bytes()


def test_workspace_audit_projection_stream_accepts_exact_global_scan_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_BYTES_SCANNED", 6)
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_PHYSICAL_LINES", 3)
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(b"\r\n\r\n\r\n"),
    )

    assert list(stream.iter_records()) == []
    assert stream.health_after_exhaustion() == AuditSourceHealth()


def test_workspace_audit_projection_stream_accepts_crlf_and_eof_without_lf(tmp_path):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(
            _audit_line(provider="CRLF").removesuffix(b"\n") + b"\r\n" + _audit_line(provider="EOF").removesuffix(b"\n")
        ),
    )

    assert [record.provider for record in stream.iter_records()] == ["CRLF", "EOF"]
    assert stream.health_after_exhaustion() == AuditSourceHealth(valid_record_count=2)


@pytest.mark.parametrize(
    ("payload", "limit_name", "limit"),
    [
        (b"    ", "MAX_AUDIT_BYTES_SCANNED", 3),
        (b"\n\n\n", "MAX_AUDIT_PHYSICAL_LINES", 2),
    ],
)
def test_workspace_audit_projection_stream_fails_closed_on_scan_limits(
    tmp_path, monkeypatch, payload, limit_name, limit
):
    monkeypatch.setattr(privacy_data_map, limit_name, limit)
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: BytesIO(payload),
    )

    with pytest.raises(PrivacyDataMapResourceLimitError):
        list(stream.iter_records())
    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()


class _FailingAuditSource(BytesIO):
    def __init__(self, first_line: bytes):
        super().__init__(first_line)
        self._read_count = 0

    def readline(self, size: int = -1) -> bytes:
        self._read_count += 1
        if self._read_count > 1:
            raise OSError("sensitive-source-path-and-error")
        return super().readline(size)


def test_workspace_audit_projection_stream_sanitizes_midstream_io_failures(tmp_path):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: _FailingAuditSource(_audit_line()),
    )

    iterator = stream.iter_records()
    assert next(iterator).provider == "OpenAI"
    with pytest.raises(AuditProjectionSourceError) as exc_info:
        next(iterator)

    assert str(exc_info.value) == ""
    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()


class _FailingDrainSource(BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self._read_count = 0

    def readline(self, size: int = -1) -> bytes:
        self._read_count += 1
        if self._read_count == 3:
            raise OSError("sensitive-drain-failure")
        return super().readline(size)


def test_workspace_audit_projection_stream_sanitizes_drain_io_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy_data_map, "MAX_AUDIT_LINE_BYTES", 8)
    monkeypatch.setattr(privacy_data_map, "_AUDIT_DRAIN_CHUNK_BYTES", 3)
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: _FailingDrainSource(b"x" * 20 + b"\n"),
    )

    with pytest.raises(AuditProjectionSourceError) as exc_info:
        list(stream.iter_records())

    assert str(exc_info.value) == ""
    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()


def test_workspace_audit_projection_stream_closes_abandoned_source_and_withholds_health(
    tmp_path,
):
    source = BytesIO(_audit_line() + _audit_line(provider="Second"))
    stream = WorkspaceAuditProjectionStream(tmp_path, open_binary=lambda: source)
    iterator = stream.iter_records()

    assert next(iterator).provider == "OpenAI"
    iterator.close()

    assert source.closed
    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()


class _TextReturningAuditSource(BytesIO):
    def readline(self, size: int = -1):
        return "not-bytes"


def test_workspace_audit_projection_stream_does_not_hide_non_binary_programming_errors(
    tmp_path,
):
    stream = WorkspaceAuditProjectionStream(
        tmp_path,
        open_binary=lambda: _TextReturningAuditSource(),
    )

    with pytest.raises(TypeError, match="binary mode"):
        list(stream.iter_records())
    with pytest.raises(PrivacyDataMapProjectionError):
        stream.health_after_exhaustion()


def test_app_declaration_reader_projects_valid_manifests_in_deterministic_order(tmp_path):
    apps_dir = tmp_path / "apps"
    _write_manifest(apps_dir, "zeta-app", schema_refs=("Task",))
    _write_manifest(apps_dir, "alpha-app", schema_refs=("Event", "Task"))

    snapshot = AppDeclarationSnapshotReader(apps_dir).read()

    assert snapshot == AppDeclarationSnapshot(
        declarations=(
            AppDeclaration(app_id="alpha-app", schema_refs=("Event", "Task")),
            AppDeclaration(app_id="zeta-app", schema_refs=("Task",)),
        ),
        invalid_app_count=0,
    )


def test_manifest_capabilities_are_not_projected_as_privacy_map_evidence(tmp_path):
    apps_dir = tmp_path / "apps"
    _write_manifest(
        apps_dir,
        "forecast-app",
        schema_refs=("Task",),
        capabilities=(
            {
                "id": "network.request",
                "scope": {
                    "sources": {
                        "forecast-capability-canary": {
                            "base_url": "https://api.open-meteo.com",
                            "paths": ["/v1/forecast"],
                            "methods": ["GET"],
                            "response_limit": 1_048_576,
                        }
                    }
                },
            },
        ),
    )

    app_snapshot = AppDeclarationSnapshotReader(apps_dir).read()
    response = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
        empty_audit_stream(),
        app_snapshot,
        SchemaIdSnapshot(schema_ids=("Task",), unsafe_schema_id_count=0),
    )
    payload = response.model_dump(mode="json", by_alias=True)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert app_snapshot == AppDeclarationSnapshot(
        declarations=(AppDeclaration(app_id="forecast-app", schema_refs=("Task",)),),
        invalid_app_count=0,
    )
    assert payload["observed_flows"] == []
    assert payload["declared_associations"] == [
        {
            "id": "declaration:7b5cea16a02d22cdfa16c05735038464cb3cc99b8c253d951f028a8f547a08df",
            "evidence": "declared",
            "app_node_id": "app:forecast-app",
            "schema_node_id": "schema:Task",
        }
    ]
    assert "network.request" not in serialized
    assert "forecast-capability-canary" not in serialized


def test_app_declaration_reader_is_non_mutating_and_does_not_migrate_metadata(tmp_path):
    apps_dir = tmp_path / "apps"
    legacy_dir = apps_dir / "legacy-app"
    legacy_dir.mkdir(parents=True)
    metadata_path = legacy_dir / "metadata.json"
    metadata_path.write_text('{"id":"legacy-app"}', encoding="utf-8")
    before = {path.relative_to(apps_dir): path.read_bytes() for path in apps_dir.rglob("*") if path.is_file()}

    snapshot = AppDeclarationSnapshotReader(apps_dir).read()

    after = {path.relative_to(apps_dir): path.read_bytes() for path in apps_dir.rglob("*") if path.is_file()}
    assert snapshot == AppDeclarationSnapshot(declarations=(), invalid_app_count=1)
    assert after == before
    assert not (legacy_dir / "manifest.json").exists()


def test_app_declaration_reader_keeps_valid_apps_when_one_manifest_is_invalid(tmp_path):
    apps_dir = tmp_path / "apps"
    _write_manifest(apps_dir, "valid-app", schema_refs=("Task",))
    invalid_dir = apps_dir / "invalid-app"
    invalid_dir.mkdir()
    (invalid_dir / "manifest.json").write_text("{", encoding="utf-8")

    snapshot = AppDeclarationSnapshotReader(apps_dir).read()

    assert snapshot == AppDeclarationSnapshot(
        declarations=(AppDeclaration(app_id="valid-app", schema_refs=("Task",)),),
        invalid_app_count=1,
    )


def test_app_declaration_reader_missing_root_is_a_valid_empty_source(tmp_path):
    snapshot = AppDeclarationSnapshotReader(tmp_path / "missing-apps").read()

    assert snapshot == AppDeclarationSnapshot(declarations=(), invalid_app_count=0)


def test_app_declaration_reader_rejects_apps_root_link_or_junction(tmp_path, monkeypatch):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    original_link_check = privacy_data_map._is_link_or_junction

    def classify_apps_root_as_link(path):
        return path == apps_dir or original_link_check(path)

    monkeypatch.setattr(
        privacy_data_map,
        "_is_link_or_junction",
        classify_apps_root_as_link,
    )

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=0,
        source_error=SourceError.UNREADABLE,
    )


def test_app_declaration_reader_rejects_real_windows_apps_root_junction(tmp_path):
    apps_target = tmp_path / "apps-target"
    _write_manifest(apps_target, "linked-app", schema_refs=("Task",))
    apps_junction = tmp_path / "apps-junction"
    _create_windows_junction(apps_junction, apps_target)

    assert AppDeclarationSnapshotReader(apps_junction).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=0,
        source_error=SourceError.UNREADABLE,
    )


def test_app_declaration_reader_sanitizes_root_enumeration_failure(tmp_path, monkeypatch):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()

    def fail_scandir(_path):
        raise PermissionError("sensitive-apps-root")

    monkeypatch.setattr(os, "scandir", fail_scandir)

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=0,
        source_error=SourceError.UNREADABLE,
    )


def test_app_declaration_reader_fails_before_manifest_reads_at_candidate_limit(tmp_path, monkeypatch):
    apps_dir = tmp_path / "apps"
    for app_id in ("app-one", "app-two", "app-three"):
        _write_manifest(apps_dir, app_id)
    monkeypatch.setattr(privacy_data_map, "MAX_APP_CANDIDATES_SCANNED", 2)

    def unexpected_manifest_read(*_args, **_kwargs):
        raise AssertionError("Manifest reads must not start before the candidate bound is known")

    monkeypatch.setattr(privacy_data_map.AppManifest, "read", unexpected_manifest_read)

    with pytest.raises(PrivacyDataMapResourceLimitError):
        AppDeclarationSnapshotReader(apps_dir).read()


@pytest.mark.parametrize("entry_kind", ["ordinary_file", "hidden_directory"])
def test_app_declaration_reader_counts_every_physical_root_entry_before_filtering(
    tmp_path,
    monkeypatch,
    entry_kind,
):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    for index in range(3):
        path = apps_dir / (f"entry-{index}.txt" if entry_kind == "ordinary_file" else f".hidden-{index}")
        if entry_kind == "ordinary_file":
            path.write_text("not an App", encoding="utf-8")
        else:
            path.mkdir()
    monkeypatch.setattr(privacy_data_map, "MAX_APP_DIRECTORY_ENTRIES_SCANNED", 2, raising=False)

    with pytest.raises(PrivacyDataMapResourceLimitError):
        AppDeclarationSnapshotReader(apps_dir).read()


def test_app_declaration_reader_checks_physical_entry_bound_before_any_manifest_open(
    tmp_path,
    monkeypatch,
):
    apps_dir = tmp_path / "apps"
    _write_manifest(apps_dir, "valid-app", schema_refs=("Task",))
    (apps_dir / ".hidden").mkdir()
    (apps_dir / "ordinary.txt").write_text("not an App", encoding="utf-8")
    monkeypatch.setattr(privacy_data_map, "MAX_APP_DIRECTORY_ENTRIES_SCANNED", 2, raising=False)

    def unexpected_manifest_open(*_args, **_kwargs):
        raise AssertionError("Manifest reads must not start before the physical entry bound is known")

    monkeypatch.setattr(
        privacy_data_map,
        "_open_manifest_binary",
        unexpected_manifest_open,
        raising=False,
    )

    with pytest.raises(PrivacyDataMapResourceLimitError):
        AppDeclarationSnapshotReader(apps_dir).read()


class _ReplaceAfterScandir:
    def __init__(self, iterator, replace):
        self._iterator = iterator
        self._replace = replace

    def __enter__(self):
        self._iterator.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        result = self._iterator.__exit__(exc_type, exc, traceback)
        if exc_type is None:
            self._replace()
        return result

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iterator)


def test_app_declaration_reader_rejects_apps_root_replaced_after_enumeration(
    tmp_path,
    monkeypatch,
):
    apps_dir = tmp_path / "apps"
    _write_manifest(apps_dir, "stable-app", schema_refs=("Task",))
    original_scandir = privacy_data_map.os.scandir

    def replace_root():
        apps_dir.rename(tmp_path / "apps-original")
        _write_manifest(apps_dir, "replacement-app", schema_refs=("Event",))

    def replacing_scandir(path):
        iterator = original_scandir(path)
        if privacy_data_map.Path(path) == apps_dir:
            return _ReplaceAfterScandir(iterator, replace_root)
        return iterator

    monkeypatch.setattr(privacy_data_map.os, "scandir", replacing_scandir)

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=0,
        source_error=SourceError.UNREADABLE,
    )


def test_app_declaration_reader_rechecks_apps_root_link_status_after_enumeration(
    tmp_path,
    monkeypatch,
):
    apps_dir = tmp_path / "apps"
    _write_manifest(apps_dir, "stable-app", schema_refs=("Task",))
    original_link_check = privacy_data_map._is_link_or_junction
    root_checks = 0

    def root_becomes_a_link(path):
        nonlocal root_checks
        if path == apps_dir:
            root_checks += 1
            return root_checks > 1
        return original_link_check(path)

    monkeypatch.setattr(privacy_data_map, "_is_link_or_junction", root_becomes_a_link)

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=0,
        source_error=SourceError.UNREADABLE,
    )


def test_app_declaration_reader_rejects_candidate_replaced_after_enumeration(
    tmp_path,
    monkeypatch,
):
    apps_dir = tmp_path / "apps"
    app_dir = _write_manifest(apps_dir, "stable-app", schema_refs=("Task",))
    original_scandir = privacy_data_map.os.scandir

    def replace_candidate():
        app_dir.rename(apps_dir / "stable-app-original")
        _write_manifest(apps_dir, "stable-app", schema_refs=("Event",))

    def replacing_scandir(path):
        iterator = original_scandir(path)
        if privacy_data_map.Path(path) == apps_dir:
            return _ReplaceAfterScandir(iterator, replace_candidate)
        return iterator

    monkeypatch.setattr(privacy_data_map.os, "scandir", replacing_scandir)

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=1,
    )


def test_app_declaration_reader_rejects_manifest_replaced_between_stat_and_open(
    tmp_path,
    monkeypatch,
):
    apps_dir = tmp_path / "apps"
    app_dir = _write_manifest(apps_dir, "stable-app", schema_refs=("Task",))
    manifest_path = app_dir / "manifest.json"
    original_path_open = privacy_data_map.Path.open
    original_os_open = privacy_data_map.os.open
    replaced = False

    def replace_manifest():
        nonlocal replaced
        if replaced:
            return
        replaced = True
        manifest_path.rename(app_dir / "manifest-original.json")
        replacement = {
            "manifest_version": 1,
            "id": "stable-app",
            "title": "stable-app",
            "description": "",
            "app_version": "0.1.0",
            "intents": [],
            "schema_refs": ["Event"],
        }
        manifest_path.write_text(json.dumps(replacement), encoding="utf-8")

    def replacing_path_open(path, *args, **kwargs):
        if path == manifest_path:
            replace_manifest()
        return original_path_open(path, *args, **kwargs)

    def replacing_os_open(path, *args, **kwargs):
        if privacy_data_map.Path(path) == manifest_path:
            replace_manifest()
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(privacy_data_map.Path, "open", replacing_path_open)
    monkeypatch.setattr(privacy_data_map.os, "open", replacing_os_open)

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=1,
    )


def test_app_declaration_reader_rejects_directory_symlink_escape(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    outside = tmp_path / "outside"
    _write_manifest(outside, "escaped-app", schema_refs=("Task",))
    link = apps_dir / "escaped-app"
    try:
        link.symlink_to(outside / "escaped-app", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks are unavailable in this environment: {exc}")

    snapshot = AppDeclarationSnapshotReader(apps_dir).read()

    assert snapshot == AppDeclarationSnapshot(declarations=(), invalid_app_count=1)


def test_app_declaration_reader_rejects_real_windows_app_directory_junction(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    outside = tmp_path / "outside"
    _write_manifest(outside, "linked-app", schema_refs=("Task",))
    _create_windows_junction(apps_dir / "linked-app", outside / "linked-app")

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=1,
    )


@pytest.mark.parametrize("linked_part", ["app_directory", "manifest"])
def test_app_declaration_reader_always_exercises_link_and_junction_rejection(tmp_path, monkeypatch, linked_part):
    apps_dir = tmp_path / "apps"
    app_dir = _write_manifest(apps_dir, "linked-app", schema_refs=("Task",))
    linked_path = app_dir if linked_part == "app_directory" else app_dir / "manifest.json"
    original_link_check = privacy_data_map._is_link_or_junction

    def classify_selected_path_as_link(path):
        return path == linked_path or original_link_check(path)

    monkeypatch.setattr(
        privacy_data_map,
        "_is_link_or_junction",
        classify_selected_path_as_link,
    )

    assert AppDeclarationSnapshotReader(apps_dir).read() == AppDeclarationSnapshot(
        declarations=(),
        invalid_app_count=1,
    )


def test_service_aggregates_observed_flows_with_known_answer_ids_and_canonical_utc():
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 18, 8, 15, tzinfo=UTC),
                provider="OpenAI",
                model="gpt-example",
                stage="chat",
            ),
            AuditProjectionRecord(
                timestamp=datetime(
                    2026,
                    7,
                    19,
                    8,
                    tzinfo=timezone(timedelta(hours=8)),
                ),
                provider="OpenAI",
                model="gpt-example",
                stage="chat",
            ),
        ],
        health=AuditSourceHealth(valid_record_count=2),
    )
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    result = service.build(stream, empty_app_snapshot(), empty_schema_snapshot())
    payload = result.model_dump(mode="json")

    model_target_id = "model_target:08e047f9d63d6fbf056b4b083099d8d7fb0e9e691f0117529809d1288f13aa87"
    assert payload["generated_at"] == "2026-07-19T00:00:00Z"
    assert payload["scope"]["observed_window"] == {
        "from": "2026-07-18T08:15:00Z",
        "to": "2026-07-19T00:00:00Z",
    }
    assert payload["nodes"] == [
        {
            "id": "platform:ambient-agent",
            "kind": "platform",
            "label": "Ambient Agent",
        },
        {
            "id": model_target_id,
            "kind": "recorded_model_target",
            "label": "OpenAI · gpt-example",
            "location": "unknown",
        },
    ]
    assert payload["observed_flows"] == [
        {
            "id": "flow:e7c84975c42780296fc744f62a816704e204e7c7db118324fcd38db763d33580",
            "evidence": "observed",
            "source_node_id": "platform:ambient-agent",
            "destination_node_id": model_target_id,
            "stage": "chat",
            "count": 2,
            "first_observed_at": "2026-07-18T08:15:00Z",
            "last_observed_at": "2026-07-19T00:00:00Z",
        }
    ]
    assert payload["source_health"]["valid_audit_records"] == 2
    assert payload["source_health"]["status"] == "healthy"
    assert payload["coverage"]["status"] == "partial"
    assert [channel["id"] for channel in payload["coverage"]["channels"]] == [
        "llm",
        "mcp",
        "http_agent",
        "coding_agent_acp",
        "provider_management",
        "isolated_widget_runtime",
    ]
    assert [channel["observation"] for channel in payload["coverage"]["channels"]] == [
        "partial",
        "not_instrumented",
        "not_instrumented",
        "not_instrumented",
        "not_instrumented",
        "not_instrumented",
    ]
    assert payload["declared_associations"] == []


def test_service_serializes_fractional_utc_timestamps_canonically():
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, 0, 0, 0, 120000, tzinfo=UTC),
                provider="OpenAI",
                model="gpt-example",
                stage="chat",
            )
        ],
        health=AuditSourceHealth(valid_record_count=1),
    )
    result = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 20, 8, 30, 0, 123400, tzinfo=UTC)).build(
        stream, empty_app_snapshot(), empty_schema_snapshot()
    )

    payload = result.model_dump(mode="json", by_alias=True)

    assert payload["generated_at"] == "2026-07-20T08:30:00.1234Z"
    assert payload["scope"]["observed_window"] == {
        "from": "2026-07-19T00:00:00.12Z",
        "to": "2026-07-19T00:00:00.12Z",
    }
    assert payload["observed_flows"][0]["first_observed_at"] == "2026-07-19T00:00:00.12Z"
    assert payload["observed_flows"][0]["last_observed_at"] == "2026-07-19T00:00:00.12Z"
    assert payload["warnings"] == []
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden_key in (
        '"prompt"',
        '"response"',
        '"content"',
        '"payload"',
        '"credentials"',
        '"token"',
        '"api_key"',
        '"headers"',
    ):
        assert forbidden_key not in serialized
    assert "raw-canary-must-not-cross-the-boundary" not in serialized


@pytest.mark.parametrize(
    ("limit_name", "records"),
    [
        (
            "MAX_UNIQUE_RECORDED_MODEL_TARGETS",
            [
                AuditProjectionRecord(
                    timestamp=datetime(2026, 7, 19, hour, tzinfo=UTC),
                    provider="OpenAI",
                    model=f"model-{hour}",
                    stage="chat",
                )
                for hour in range(2)
            ],
        ),
        (
            "MAX_OBSERVED_FLOW_GROUPS",
            [
                AuditProjectionRecord(
                    timestamp=datetime(2026, 7, 19, hour, tzinfo=UTC),
                    provider="OpenAI",
                    model="shared-model",
                    stage=stage,
                )
                for hour, stage in enumerate(("chat", "route"))
            ],
        ),
    ],
)
def test_service_accepts_exact_observed_cardinality_boundaries(monkeypatch, limit_name, records):
    monkeypatch.setattr(privacy_data_map, limit_name, 2)
    stream = InMemoryAuditProjectionStream(
        records=records,
        health=AuditSourceHealth(valid_record_count=2),
    )

    result = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
        stream,
        empty_app_snapshot(),
        empty_schema_snapshot(),
    )

    expected_model_targets = 2 if limit_name == "MAX_UNIQUE_RECORDED_MODEL_TARGETS" else 1
    assert sum(node.kind == "recorded_model_target" for node in result.nodes) == expected_model_targets
    assert len(result.observed_flows) == 2


@pytest.mark.parametrize(
    ("limit_name", "records"),
    [
        (
            "MAX_UNIQUE_RECORDED_MODEL_TARGETS",
            [
                AuditProjectionRecord(
                    timestamp=datetime(2026, 7, 19, hour, tzinfo=UTC),
                    provider="OpenAI",
                    model=f"model-{hour}",
                    stage="chat",
                )
                for hour in range(3)
            ],
        ),
        (
            "MAX_OBSERVED_FLOW_GROUPS",
            [
                AuditProjectionRecord(
                    timestamp=datetime(2026, 7, 19, hour, tzinfo=UTC),
                    provider="OpenAI",
                    model="shared-model",
                    stage=stage,
                )
                for hour, stage in enumerate(("chat", "route", "plan"))
            ],
        ),
    ],
)
def test_service_fails_closed_when_observed_cardinality_limit_is_crossed(monkeypatch, limit_name, records):
    monkeypatch.setattr(privacy_data_map, limit_name, 2)
    stream = InMemoryAuditProjectionStream(
        records=records,
        health=AuditSourceHealth(valid_record_count=3),
    )

    with pytest.raises(PrivacyDataMapResourceLimitError):
        PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
            stream,
            empty_app_snapshot(),
            empty_schema_snapshot(),
        )


def test_service_normalizes_provider_model_and_stage_before_grouping():
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, 1, tzinfo=UTC),
                provider="  ＯｐｅｎＡＩ  ",
                model=" ｇｐｔ-example ",
                stage="session_title",
            ),
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, 2, tzinfo=UTC),
                provider="OpenAI",
                model="gpt-example",
                stage="title",
            ),
        ],
        health=AuditSourceHealth(valid_record_count=2),
    )

    result = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
        stream,
        empty_app_snapshot(),
        empty_schema_snapshot(),
    )

    model_targets = [node for node in result.nodes if node.kind == "recorded_model_target"]
    assert [node.label for node in model_targets] == ["OpenAI · gpt-example"]
    assert len(result.observed_flows) == 1
    assert result.observed_flows[0].stage == "title"
    assert result.observed_flows[0].count == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", ""),
        ("provider", " "),
        ("model", ""),
        ("model", " "),
        ("provider", "x" * 201),
        ("model", "x" * 201),
        ("provider", "x\x00y"),
        ("provider", "\tOpenAI"),
        ("provider", "OpenAI\n"),
        ("provider", "x\u0085y"),
        ("model", "\u0085gpt-example"),
        ("provider", "x\u200by"),
        ("provider", "x\u202ey"),
        ("provider", "x\u2066y"),
        ("provider", "x\ufeffy"),
        ("provider", "x\ud800y"),
        ("provider", "x\ue000y"),
        ("provider", "x\u0378y"),
        ("provider", "x\u2028y"),
        ("provider", "x\u2029y"),
    ],
)
def test_service_rejects_empty_oversized_or_unsafe_provider_model_values(field, value):
    values = {
        "provider": "OpenAI",
        "model": "gpt-example",
    }
    values[field] = value
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, tzinfo=UTC),
                provider=values["provider"],
                model=values["model"],
                stage="chat",
            )
        ],
        health=AuditSourceHealth(valid_record_count=1),
    )

    with pytest.raises(PrivacyDataMapProjectionError):
        PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
            stream,
            empty_app_snapshot(),
            empty_schema_snapshot(),
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x" * 200, "x" * 200),
        ("\ufb01" * 100, "fi" * 100),
    ],
)
def test_service_accepts_projection_text_at_the_normalized_codepoint_limit(value, expected):
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, tzinfo=UTC),
                provider=value,
                model="gpt-example",
                stage="chat",
            )
        ],
        health=AuditSourceHealth(valid_record_count=1),
    )

    result = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
        stream,
        empty_app_snapshot(),
        empty_schema_snapshot(),
    )

    model_target = next(node for node in result.nodes if node.kind == "recorded_model_target")
    assert model_target.label == f"{expected} · gpt-example"


@pytest.mark.parametrize(
    "stage",
    ["CHAT", "tool_call", " chat ", "unknown-stage"],
)
def test_service_maps_each_unknown_nonempty_stage_to_other(stage):
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, tzinfo=UTC),
                provider="OpenAI",
                model="gpt-example",
                stage=stage,
            )
        ],
        health=AuditSourceHealth(valid_record_count=1),
    )

    result = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
        stream,
        empty_app_snapshot(),
        empty_schema_snapshot(),
    )

    assert result.observed_flows[0].stage == "other"


@pytest.mark.parametrize("stage", ["", " ", "\t", "\u2003"])
def test_service_rejects_empty_or_whitespace_only_stages(stage):
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, tzinfo=UTC),
                provider="OpenAI",
                model="gpt-example",
                stage=stage,
            )
        ],
        health=AuditSourceHealth(valid_record_count=1),
    )

    with pytest.raises(PrivacyDataMapProjectionError):
        PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
            stream,
            empty_app_snapshot(),
            empty_schema_snapshot(),
        )


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("chat", "chat"),
        ("route", "route"),
        ("plan", "plan"),
        ("mutation", "mutation"),
        ("verify", "verify"),
        ("session_title", "title"),
        ("title", "title"),
    ],
)
def test_service_normalizes_each_supported_stage(stage, expected):
    stream = InMemoryAuditProjectionStream(
        records=[
            AuditProjectionRecord(
                timestamp=datetime(2026, 7, 19, tzinfo=UTC),
                provider="OpenAI",
                model="gpt-example",
                stage=stage,
            )
        ],
        health=AuditSourceHealth(valid_record_count=1),
    )

    result = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).build(
        stream,
        empty_app_snapshot(),
        empty_schema_snapshot(),
    )

    assert result.observed_flows[0].stage == expected


def test_service_projects_only_registered_referenced_declarations_with_known_answer_id():
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))
    app_snapshot = AppDeclarationSnapshot(
        declarations=(
            AppDeclaration(app_id="zeta-app", schema_refs=()),
            AppDeclaration(
                app_id="morning-planner",
                schema_refs=("Task", "Missing"),
            ),
        ),
        invalid_app_count=0,
    )
    schema_snapshot = SchemaIdSnapshot(
        schema_ids=("Unreferenced", "Task"),
        unsafe_schema_id_count=0,
    )

    payload = service.build(
        empty_audit_stream(),
        app_snapshot,
        schema_snapshot,
    ).model_dump(mode="json")

    assert payload["nodes"] == [
        {
            "id": "platform:ambient-agent",
            "kind": "platform",
            "label": "Ambient Agent",
        },
        {
            "id": "app:morning-planner",
            "kind": "app",
            "label": "morning-planner",
        },
        {
            "id": "app:zeta-app",
            "kind": "app",
            "label": "zeta-app",
        },
        {
            "id": "schema:Task",
            "kind": "schema",
            "label": "Task",
        },
    ]
    assert payload["declared_associations"] == [
        {
            "id": "declaration:2269092a9528a054ba56ff10ffd0fdb523a98d1ebb9ec8356a174abd1d300475",
            "evidence": "declared",
            "app_node_id": "app:morning-planner",
            "schema_node_id": "schema:Task",
        }
    ]
    assert payload["source_health"]["status"] == "degraded"
    assert payload["source_health"]["missing_schema_references"] == 1
    assert payload["warnings"] == [
        {
            "code": "missing_schema_references",
            "count": 1,
        }
    ]
    declaration_payload = payload["declared_associations"][0]
    for forbidden_semantic in (
        "count",
        "first_observed_at",
        "last_observed_at",
        "source_node_id",
        "destination_node_id",
        "direction",
        "permission",
    ):
        assert forbidden_semantic not in declaration_payload


def test_service_counts_each_missing_app_schema_reference_pair():
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))
    app_snapshot = AppDeclarationSnapshot(
        declarations=(
            AppDeclaration(app_id="alpha-app", schema_refs=("Missing",)),
            AppDeclaration(app_id="beta-app", schema_refs=("Missing",)),
        ),
        invalid_app_count=0,
    )

    result = service.build(
        empty_audit_stream(),
        app_snapshot,
        empty_schema_snapshot(),
    )

    assert result.source_health.missing_schema_references == 2
    assert result.warnings == (
        privacy_data_map.PrivacyMapWarning(
            code="missing_schema_references",
            count=2,
        ),
    )
    assert result.declared_associations == ()
    assert all(node.kind != "schema" for node in result.nodes)


@pytest.mark.parametrize(
    ("app_snapshot", "schema_snapshot"),
    [
        (
            AppDeclarationSnapshot(
                declarations=(
                    AppDeclaration(app_id="duplicate-app", schema_refs=()),
                    AppDeclaration(app_id="duplicate-app", schema_refs=()),
                ),
                invalid_app_count=0,
            ),
            empty_schema_snapshot(),
        ),
        (
            empty_app_snapshot(),
            SchemaIdSnapshot(
                schema_ids=("Task", "Task"),
                unsafe_schema_id_count=0,
            ),
        ),
        (
            AppDeclarationSnapshot(
                declarations=(
                    AppDeclaration(
                        app_id="duplicate-ref-app",
                        schema_refs=("Task", "Task"),
                    ),
                ),
                invalid_app_count=0,
            ),
            SchemaIdSnapshot(schema_ids=("Task",), unsafe_schema_id_count=0),
        ),
        (
            AppDeclarationSnapshot(
                declarations=(AppDeclaration(app_id="../unsafe", schema_refs=()),),
                invalid_app_count=0,
            ),
            empty_schema_snapshot(),
        ),
        (
            empty_app_snapshot(),
            SchemaIdSnapshot(
                schema_ids=("unsafe/schema",),
                unsafe_schema_id_count=0,
            ),
        ),
    ],
)
def test_service_rejects_corrupt_internal_declaration_snapshots(
    app_snapshot,
    schema_snapshot,
):
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(PrivacyDataMapProjectionError):
        service.build(
            empty_audit_stream(),
            app_snapshot,
            schema_snapshot,
        )


@pytest.mark.parametrize(
    ("limit_name", "app_snapshot", "schema_snapshot"),
    [
        (
            "MAX_APP_NODES",
            AppDeclarationSnapshot(
                declarations=(
                    AppDeclaration(app_id="alpha-app", schema_refs=()),
                    AppDeclaration(app_id="beta-app", schema_refs=()),
                ),
                invalid_app_count=0,
            ),
            empty_schema_snapshot(),
        ),
        (
            "MAX_SCHEMA_NODES",
            AppDeclarationSnapshot(
                declarations=(
                    AppDeclaration(
                        app_id="schema-app",
                        schema_refs=("Event", "Task"),
                    ),
                ),
                invalid_app_count=0,
            ),
            SchemaIdSnapshot(
                schema_ids=("Event", "Task"),
                unsafe_schema_id_count=0,
            ),
        ),
        (
            "MAX_DECLARED_ASSOCIATIONS",
            AppDeclarationSnapshot(
                declarations=(
                    AppDeclaration(
                        app_id="association-app",
                        schema_refs=("Event", "Task"),
                    ),
                ),
                invalid_app_count=0,
            ),
            SchemaIdSnapshot(
                schema_ids=("Event", "Task"),
                unsafe_schema_id_count=0,
            ),
        ),
    ],
)
def test_service_fails_closed_when_declaration_cardinality_limit_is_crossed(
    monkeypatch,
    limit_name,
    app_snapshot,
    schema_snapshot,
):
    monkeypatch.setattr(privacy_data_map, limit_name, 1)
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(PrivacyDataMapResourceLimitError):
        service.build(
            empty_audit_stream(),
            app_snapshot,
            schema_snapshot,
        )


def test_service_accepts_exact_declaration_cardinality_boundaries(monkeypatch):
    monkeypatch.setattr(privacy_data_map, "MAX_APP_NODES", 2)
    monkeypatch.setattr(privacy_data_map, "MAX_SCHEMA_NODES", 2)
    monkeypatch.setattr(privacy_data_map, "MAX_DECLARED_ASSOCIATIONS", 2)
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    result = service.build(
        empty_audit_stream(),
        AppDeclarationSnapshot(
            declarations=(
                AppDeclaration(app_id="alpha-app", schema_refs=("Event",)),
                AppDeclaration(app_id="beta-app", schema_refs=("Task",)),
            ),
            invalid_app_count=0,
        ),
        SchemaIdSnapshot(
            schema_ids=("Event", "Task"),
            unsafe_schema_id_count=0,
        ),
    )

    assert len([node for node in result.nodes if node.kind == "app"]) == 2
    assert len([node for node in result.nodes if node.kind == "schema"]) == 2
    assert len(result.declared_associations) == 2


def test_service_accepts_exact_schema_reference_scan_limit_including_missing_refs(monkeypatch):
    monkeypatch.setattr(privacy_data_map, "MAX_SCHEMA_REFERENCES_SCANNED", 2)
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    result = service.build(
        empty_audit_stream(),
        AppDeclarationSnapshot(
            declarations=(
                AppDeclaration(
                    app_id="reference-app",
                    schema_refs=("MissingOne", "MissingTwo"),
                ),
            ),
            invalid_app_count=0,
        ),
        empty_schema_snapshot(),
    )

    assert result.source_health.missing_schema_references == 2
    assert result.declared_associations == ()


def test_service_fails_closed_when_total_schema_reference_scan_limit_is_crossed(monkeypatch):
    monkeypatch.setattr(privacy_data_map, "MAX_SCHEMA_REFERENCES_SCANNED", 2)
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(PrivacyDataMapResourceLimitError):
        service.build(
            empty_audit_stream(),
            AppDeclarationSnapshot(
                declarations=(
                    AppDeclaration(
                        app_id="reference-app",
                        schema_refs=("MissingOne", "MissingTwo", "MissingThree"),
                    ),
                ),
                invalid_app_count=0,
            ),
            empty_schema_snapshot(),
        )


def test_service_preserves_app_declaration_source_failure_category():
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(AppDeclarationSourceError) as exc_info:
        service.build(
            empty_audit_stream(),
            AppDeclarationSnapshot(
                declarations=(),
                invalid_app_count=0,
                source_error=SourceError.UNREADABLE,
            ),
            empty_schema_snapshot(),
        )

    assert str(exc_info.value) == ""


def test_service_rejects_app_source_failure_before_consuming_audit():
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(AppDeclarationSourceError):
        service.build(
            FailIfConsumedAuditProjectionStream(),
            AppDeclarationSnapshot(
                declarations=(),
                invalid_app_count=0,
                source_error=SourceError.UNREADABLE,
            ),
            empty_schema_snapshot(),
        )


def test_service_preserves_graph_schema_source_failure_category():
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(GraphSchemaSourceError) as exc_info:
        service.build(
            empty_audit_stream(),
            empty_app_snapshot(),
            SchemaIdSnapshot(
                schema_ids=(),
                unsafe_schema_id_count=0,
                source_error=SourceError.UNREADABLE,
            ),
        )

    assert str(exc_info.value) == ""


def test_service_rejects_schema_source_failure_before_consuming_audit():
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(GraphSchemaSourceError):
        service.build(
            FailIfConsumedAuditProjectionStream(),
            empty_app_snapshot(),
            SchemaIdSnapshot(
                schema_ids=(),
                unsafe_schema_id_count=0,
                source_error=SourceError.UNREADABLE,
            ),
        )


def test_service_gives_app_source_failure_precedence_when_both_snapshots_fail():
    service = PrivacyDataMapService(clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))

    with pytest.raises(AppDeclarationSourceError):
        service.build(
            FailIfConsumedAuditProjectionStream(),
            AppDeclarationSnapshot(
                declarations=(),
                invalid_app_count=0,
                source_error=SourceError.UNREADABLE,
            ),
            SchemaIdSnapshot(
                schema_ids=(),
                unsafe_schema_id_count=0,
                source_error=SourceError.UNREADABLE,
            ),
        )


def test_service_rejects_declared_association_digest_collisions():
    service = PrivacyDataMapService(
        clock=lambda: datetime(2026, 7, 19, tzinfo=UTC),
        digest=lambda _value: "collision",
    )

    with pytest.raises(PrivacyDataMapIdCollisionError):
        service.build(
            empty_audit_stream(),
            AppDeclarationSnapshot(
                declarations=(
                    AppDeclaration(
                        app_id="collision-app",
                        schema_refs=("Event", "Task"),
                    ),
                ),
                invalid_app_count=0,
            ),
            SchemaIdSnapshot(
                schema_ids=("Event", "Task"),
                unsafe_schema_id_count=0,
            ),
        )


def test_schema_reader_requests_a_sentinel_row_and_projects_only_exact_safe_ids():
    source = StubSchemaIdSource(
        schema_ids=[
            "Event",
            "Task",
            " leading-space",
            "unsafe/slash",
            "",
        ]
    )

    snapshot = GraphSchemaSnapshotReader(source).read()

    assert source.requested_limits == [MAX_SCHEMA_IDS_SCANNED + 1]
    assert source.requested_max_id_codepoints == [privacy_data_map.MAX_SCHEMA_ID_MATERIALIZED_CODEPOINTS]
    assert snapshot == SchemaIdSnapshot(
        schema_ids=("Event", "Task"),
        unsafe_schema_id_count=3,
    )


def test_schema_reader_treats_non_string_registry_identities_as_source_corruption():
    source = StubSchemaIdSource(schema_ids=["Task", None])

    snapshot = GraphSchemaSnapshotReader(source).read()

    assert snapshot == SchemaIdSnapshot(
        schema_ids=(),
        unsafe_schema_id_count=0,
        source_error=SourceError.UNREADABLE,
    )


def test_schema_reader_fails_closed_when_the_scan_limit_has_an_extra_row():
    source = StubSchemaIdSource(schema_ids=[f"Schema{index}" for index in range(MAX_SCHEMA_IDS_SCANNED + 1)])

    with pytest.raises(PrivacyDataMapResourceLimitError):
        GraphSchemaSnapshotReader(source).read()


def test_schema_reader_sanitizes_storage_neutral_schema_source_failures():
    source = StubSchemaIdSource(error=GraphSchemaReadError())

    snapshot = GraphSchemaSnapshotReader(source).read()

    assert snapshot == SchemaIdSnapshot(
        schema_ids=(),
        unsafe_schema_id_count=0,
        source_error=SourceError.UNREADABLE,
    )


def test_schema_reader_requires_adapters_to_translate_storage_specific_failures():
    source = StubSchemaIdSource(error=sqlite3.OperationalError("adapter contract violation"))

    with pytest.raises(sqlite3.OperationalError, match="adapter contract violation"):
        GraphSchemaSnapshotReader(source).read()


def test_schema_reader_does_not_hide_unexpected_programming_errors():
    source = StubSchemaIdSource(error=RuntimeError("programming bug"))

    with pytest.raises(RuntimeError, match="programming bug"):
        GraphSchemaSnapshotReader(source).read()
