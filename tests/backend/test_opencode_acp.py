import asyncio
import contextlib
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp import text_block
from acp.exceptions import RequestError
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    CreateTerminalResponse,
    DeniedOutcome,
    EnvVariable,
    FileEditToolCallContent,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    ReadTextFileResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    ToolCall,
    ToolCallProgress,
    ToolCallStart,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

from backend.coding_agent_acp import (
    CodingAgentDraftError,
    FastAPIACPClient,
    OpenCodeACPInputError,
    OpenCodeACPProtocolError,
    OpenCodeACPStartupError,
    OpenCodeACPTimeoutError,
    OpenCodeArtifactError,
    OpenCodeStagedResult,
    PermissionPolicyManager,
    cleanup_orphaned_opencode_staging,
    discard_opencode_staging,
    promote_opencode_staging,
    recover_interrupted_opencode_promotions,
    run_opencode_agent_acp,
    validate_opencode_promotion,
    validate_opencode_staging,
)
from backend.coding_agent_repair import finding_from_exception


def _write_manifest(
    directory: Path,
    app_id: str,
    *,
    capabilities: list[dict] | None = None,
    schema_refs: list[str] | None = None,
) -> None:
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "id": app_id,
                "title": app_id.replace("-", " ").title(),
                "description": "",
                "app_version": "0.1.0",
                "intents": [],
                "schema_refs": schema_refs or [],
                "capabilities": capabilities or [],
            }
        ),
        encoding="utf-8",
    )


def test_orphan_staging_cleanup_only_removes_old_unreferenced_directories(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    old = time.time() - 10_000

    orphan = apps_dir / f".orphan-app.staging-{'a' * 32}"
    referenced = apps_dir / f".active-app.staging-{'b' * 32}"
    recent = apps_dir / f".recent-app.staging-{'c' * 32}"
    malformed = apps_dir / ".not-an-app.staging-not-a-uuid"
    regular_file = apps_dir / f".file-app.staging-{'d' * 32}"
    for directory in (orphan, referenced, recent, malformed):
        directory.mkdir()
        (directory / "controller.js").write_text("export default null;", encoding="utf-8")
    regular_file.write_text("must stay", encoding="utf-8")
    for path in (orphan, referenced, malformed, regular_file):
        os.utime(path, (old, old))

    removed = cleanup_orphaned_opencode_staging(
        apps_dir,
        referenced_staging_paths={referenced},
        grace_seconds=300,
        now_epoch=old + 1_000,
    )

    assert removed == [orphan]
    assert not orphan.exists()
    assert referenced.is_dir()
    assert recent.is_dir()
    assert malformed.is_dir()
    assert regular_file.read_text(encoding="utf-8") == "must stay"


def test_orphan_staging_cleanup_skips_symlinks_and_out_of_root_references(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    linked = apps_dir / f".linked-app.staging-{'a' * 32}"
    linked.symlink_to(outside, target_is_directory=True)

    orphan = apps_dir / f".orphan-app.staging-{'b' * 32}"
    orphan.mkdir()
    old = time.time() - 10_000
    os.utime(orphan, (old, old))

    removed = cleanup_orphaned_opencode_staging(
        apps_dir,
        referenced_staging_paths={tmp_path / orphan.name},
        grace_seconds=300,
        now_epoch=old + 1_000,
    )

    assert removed == [orphan]
    assert linked.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "untouched"


@pytest.mark.parametrize("grace_seconds", [-1, float("nan"), float("inf")])
def test_orphan_staging_cleanup_rejects_unsafe_grace(grace_seconds, tmp_path):
    with pytest.raises(ValueError, match="grace_seconds"):
        cleanup_orphaned_opencode_staging(
            tmp_path,
            referenced_staging_paths=set(),
            grace_seconds=grace_seconds,
        )


def test_promotion_journal_restores_previous_live_after_interrupted_swap(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app_id = "journal-app"
    staging = apps_dir / f".{app_id}.staging-{'a' * 32}"
    backup = apps_dir / f".{app_id}.backup-{'b' * 32}"
    staging.mkdir()
    backup.mkdir()
    (staging / "controller.js").write_text("export default 'new';", encoding="utf-8")
    (backup / "controller.js").write_text("export default 'old';", encoding="utf-8")
    journal = apps_dir / f".ambient-promotion-{app_id}-{'c' * 32}.json"
    journal.write_text(
        json.dumps(
            {
                "app_id": app_id,
                "live_name": app_id,
                "staging_name": staging.name,
                "backup_name": backup.name,
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_interrupted_opencode_promotions(apps_dir)

    assert recovered == [{"app_id": app_id, "action": "restored_previous_live"}]
    assert (apps_dir / app_id / "controller.js").read_text(encoding="utf-8") == "export default 'old';"
    assert staging.is_dir()
    assert not journal.exists()


def test_promotion_journal_finalizes_new_live_after_completed_swap(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app_id = "journal-app"
    live = apps_dir / app_id
    backup = apps_dir / f".{app_id}.backup-{'b' * 32}"
    live.mkdir()
    backup.mkdir()
    (live / "controller.js").write_text("export default 'new';", encoding="utf-8")
    (backup / "controller.js").write_text("export default 'old';", encoding="utf-8")
    journal = apps_dir / f".ambient-promotion-{app_id}-{'c' * 32}.json"
    journal.write_text(
        json.dumps(
            {
                "app_id": app_id,
                "live_name": app_id,
                "staging_name": f".{app_id}.staging-{'a' * 32}",
                "backup_name": backup.name,
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_interrupted_opencode_promotions(apps_dir)

    assert recovered == [{"app_id": app_id, "action": "finalized_new_live"}]
    assert (live / "controller.js").read_text(encoding="utf-8") == "export default 'new';"
    assert not backup.exists()
    assert not journal.exists()


def test_promotion_marker_recovers_exact_committed_artifact(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    live_dir = apps_dir / "demo-app"
    staging_dir = apps_dir / f".demo-app.staging-{'a' * 32}"
    staging_dir.mkdir()
    controller = staging_dir / "controller.js"
    controller.write_text("export default function App() { return null; }", encoding="utf-8")
    _write_manifest(staging_dir, "demo-app")
    artifact_hash = hashlib.sha256(controller.read_bytes()).hexdigest()
    (staging_dir / ".ambient-promotion.json").write_text(
        json.dumps({"run_id": "run-1", "artifact_hash": artifact_hash}),
        encoding="utf-8",
    )
    result = OpenCodeStagedResult("ok", "demo-app", staging_dir, live_dir)

    promote_opencode_staging(result)

    assert validate_opencode_promotion(result, "run-1") == live_dir / "controller.js"
    assert validate_opencode_promotion(result, "other-run") is None
    (live_dir / "controller.js").write_text(
        "export default function App() { return 1; }",
        encoding="utf-8",
    )
    with pytest.raises(OpenCodeACPProtocolError, match="promotion marker"):
        validate_opencode_promotion(result, "run-1")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("export default function App( {", "syntax/runtime/security"),
        (
            "export default async function App() { await fetch('https://example.invalid'); return null; }",
            "Forbidden host or network global: fetch",
        ),
        (
            "export default function App() { return window.document.body; }",
            "Forbidden host or network global: window",
        ),
        (
            "const { Badge } = ambient.components; "
            "export default function App() { return ambient.html`<${Badge} />`; }",
            "Unknown ambient.components primitive: Badge",
        ),
        (
            "const { useLayoutEffect } = ambient.react; "
            "export default function App() { useLayoutEffect(() => {}); return null; }",
            "Unknown ambient.react hook: useLayoutEffect",
        ),
    ],
)
def test_staging_verifier_rejects_invalid_or_host_capable_controller(tmp_path, source, message):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    staging_dir = apps_dir / f".secure-app.staging-{'a' * 32}"
    staging_dir.mkdir()
    (staging_dir / "controller.js").write_text(source, encoding="utf-8")
    _write_manifest(staging_dir, "secure-app")
    result = OpenCodeStagedResult("", "secure-app", staging_dir, apps_dir / "secure-app")

    with pytest.raises(OpenCodeArtifactError, match=message):
        validate_opencode_staging(result)


def test_staging_verifier_requires_ambient_net_source_to_be_declared(tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    staging_dir = apps_dir / f".weather-app.staging-{'a' * 32}"
    staging_dir.mkdir()
    (staging_dir / "controller.js").write_text(
        'export default async function App() { await ambient.net.request("forecast", { path: "/v1/forecast" }); return null; }',
        encoding="utf-8",
    )
    _write_manifest(staging_dir, "weather-app")
    result = OpenCodeStagedResult("", "weather-app", staging_dir, apps_dir / "weather-app")

    with pytest.raises(OpenCodeArtifactError, match=r"network\.request"):
        validate_opencode_staging(result)


@pytest.mark.asyncio
async def test_client_fs_operations(tmp_path):
    # 1. Setup target workspace dir
    workspace_root = tmp_path / "app_workspace"
    workspace_root.mkdir()

    # Write a test file
    test_file_path = workspace_root / "controller.js"
    test_file_path.write_text("hello html", encoding="utf-8")

    # 2. Instantiate client
    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=lambda x: None)

    # Test read_text_file
    read_resp = await client.read_text_file(session_id="sess", path="controller.js")
    assert isinstance(read_resp, ReadTextFileResponse)
    assert read_resp.content == "hello html"

    # Test write_text_file
    write_resp = await client.write_text_file(session_id="sess", path="README.md", content="notes")
    assert isinstance(write_resp, WriteTextFileResponse)
    assert (workspace_root / "README.md").exists()
    assert (workspace_root / "README.md").read_text(encoding="utf-8") == "notes"


@pytest.mark.asyncio
async def test_client_request_permission():
    client = FastAPIACPClient(workspace_root=Path("."), on_update_callback=lambda x: None)

    options = [
        PermissionOption(option_id="opt-allow", name="Allow", kind="allow_once"),
        PermissionOption(option_id="opt-deny", name="Deny", kind="reject_always"),
    ]

    tool_call = MagicMock(kind="read", raw_input={"path": "controller.js"}, content=None)
    resp = await client.request_permission(session_id="sess", tool_call=tool_call, options=options)
    assert isinstance(resp, RequestPermissionResponse)
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.option_id == "opt-allow"
    assert resp.outcome.outcome == "selected"


def _codex_file_edit_start(tool_call_id: str, *paths: str) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=tool_call_id,
        title="Editing files",
        kind="edit",
        status="pending",
        content=[
            FileEditToolCallContent(
                type="diff",
                path=path,
                old_text=None,
                new_text=f"generated content for {path}",
                field_meta={"kind": "add"},
            )
            for path in paths
        ],
        locations=None,
        raw_input=None,
    )


def _codex_file_operation_start(
    tool_call_id: str,
    path: str,
    *,
    kind: str,
    old_text: str | None,
    new_text: str,
) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=tool_call_id,
        title="Editing files",
        kind="edit",
        status="pending",
        content=[
            FileEditToolCallContent(
                type="diff",
                path=path,
                old_text=old_text,
                new_text=new_text,
                field_meta={"kind": kind},
            )
        ],
        locations=None,
        raw_input=None,
    )


def _pathless_file_permission(tool_call_id: str) -> ToolCall:
    return ToolCall(
        tool_call_id=tool_call_id,
        title="File edit: ",
        kind="edit",
        status="pending",
        content=None,
        locations=None,
        raw_input=None,
    )


def _file_permission_options() -> list[PermissionOption]:
    return [
        PermissionOption(option_id="opt-allow", name="Allow", kind="allow_once"),
        PermissionOption(option_id="opt-deny", name="Deny", kind="reject_once"),
    ]


def _codex_permission_kwargs(tool_call_id: str) -> dict:
    return {"codex": {"params": {"itemId": tool_call_id, "grantRoot": None}}}


@pytest.mark.asyncio
async def test_pathless_codex_file_permission_uses_preceding_tool_call_content(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-1", "controller.js"),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-1"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-1"),
    )

    assert isinstance(response.outcome, AllowedOutcome)
    assert response.outcome.option_id == "opt-allow"


@pytest.mark.asyncio
async def test_pathless_codex_file_permission_requires_codex_metadata(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-no-meta", "controller.js"),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-no-meta"),
        options=_file_permission_options(),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_existing_file_update_with_matching_old_text_is_allowed(tmp_path):
    old_text = "export default function App() { return null; }\n"
    new_text = "export default function App() { return 'updated'; }\n"
    controller = tmp_path / "controller.js"
    controller.write_text(old_text, encoding="utf-8")
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_operation_start(
            "tool-update",
            "controller.js",
            kind="update",
            old_text=old_text,
            new_text=new_text,
        ),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-update"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-update"),
    )

    assert isinstance(response.outcome, AllowedOutcome)
    assert controller.read_text(encoding="utf-8") == old_text


@pytest.mark.asyncio
async def test_update_for_missing_target_is_denied_as_ambiguous_move(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_operation_start(
            "tool-move",
            "controller.js",
            kind="update",
            old_text="content from an unreported source",
            new_text="content at the destination",
        ),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-move"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-move"),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_add_for_existing_allowed_file_is_denied(tmp_path):
    controller = tmp_path / "controller.js"
    controller.write_text("existing controller", encoding="utf-8")
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_operation_start(
            "tool-add",
            "controller.js",
            kind="add",
            old_text=None,
            new_text="replacement controller",
        ),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-add"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-add"),
    )

    assert isinstance(response.outcome, DeniedOutcome)
    assert controller.read_text(encoding="utf-8") == "existing controller"


@pytest.mark.asyncio
async def test_pathless_file_permission_without_cached_tool_call_is_denied(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("missing"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("missing"),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_pathless_permission_cache_requires_exact_session_and_tool_call_id(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess-a",
        update=_codex_file_edit_start("tool-1", "controller.js"),
    )

    wrong_session, wrong_tool = await asyncio.gather(
        client.request_permission(
            session_id="sess-b",
            tool_call=_pathless_file_permission("tool-1"),
            options=_file_permission_options(),
            **_codex_permission_kwargs("tool-1"),
        ),
        client.request_permission(
            session_id="sess-a",
            tool_call=_pathless_file_permission("tool-2"),
            options=_file_permission_options(),
            **_codex_permission_kwargs("tool-2"),
        ),
    )
    correct = await client.request_permission(
        session_id="sess-a",
        tool_call=_pathless_file_permission("tool-1"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-1"),
    )

    assert isinstance(wrong_session.outcome, DeniedOutcome)
    assert isinstance(wrong_tool.outcome, DeniedOutcome)
    assert isinstance(correct.outcome, AllowedOutcome)


@pytest.mark.asyncio
async def test_pathless_file_permission_cache_is_consumed_once(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-1", "controller.js"),
    )

    first = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-1"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-1"),
    )
    replay = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-1"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-1"),
    )

    assert isinstance(first.outcome, AllowedOutcome)
    assert isinstance(replay.outcome, DeniedOutcome)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("paths", "expected_outcome"),
    [
        (("controller.js", "manifest.json", "README.md"), AllowedOutcome),
        (("controller.js", "secrets.txt", "manifest.json"), DeniedOutcome),
    ],
)
async def test_pathless_batch_file_permission_validates_every_path_atomically(
    tmp_path,
    paths,
    expected_outcome,
):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-batch", *paths),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-batch"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-batch"),
    )

    assert isinstance(response.outcome, expected_outcome)


@pytest.mark.asyncio
async def test_pathless_permission_cache_does_not_cross_sessions_with_same_tool_id(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await asyncio.gather(
        client.session_update(
            session_id="sess-safe",
            update=_codex_file_edit_start("shared-tool", "controller.js"),
        ),
        client.session_update(
            session_id="sess-unsafe",
            update=_codex_file_edit_start("shared-tool", "secrets.txt"),
        ),
    )

    safe, unsafe = await asyncio.gather(
        client.request_permission(
            session_id="sess-safe",
            tool_call=_pathless_file_permission("shared-tool"),
            options=_file_permission_options(),
            **_codex_permission_kwargs("shared-tool"),
        ),
        client.request_permission(
            session_id="sess-unsafe",
            tool_call=_pathless_file_permission("shared-tool"),
            options=_file_permission_options(),
            **_codex_permission_kwargs("shared-tool"),
        ),
    )

    assert isinstance(safe.outcome, AllowedOutcome)
    assert isinstance(unsafe.outcome, DeniedOutcome)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
async def test_completed_or_failed_tool_call_update_clears_cached_file_permission(tmp_path, terminal_status):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-1", "controller.js"),
    )
    await client.session_update(
        session_id="sess",
        update=ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="tool-1",
            kind="edit",
            status=terminal_status,
            title="Editing files",
        ),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-1"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-1"),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_pathless_cached_diff_without_operation_kind_is_denied(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=ToolCallStart(
            session_update="tool_call",
            tool_call_id="tool-no-kind",
            title="Editing files",
            kind="edit",
            status="pending",
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path="controller.js",
                    old_text=None,
                    new_text="generated content",
                )
            ],
        ),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-no-kind"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-no-kind"),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_mutation_permission_does_not_infer_partial_path_from_title(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    tool_call = ToolCall(
        tool_call_id="tool-title",
        title="Edit controller.js and secrets.txt",
        kind="edit",
        status="pending",
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=tool_call,
        options=_file_permission_options(),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_read_permission_does_not_infer_path_from_title(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    tool_call = ToolCall(
        tool_call_id="tool-read-title",
        title="Read controller.js and ../../secret.env",
        kind="read",
        status="pending",
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=tool_call,
        options=_file_permission_options(),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_kind", ["edit", "delete", "move"])
async def test_direct_mutation_permission_requires_operation_evidence(tmp_path, tool_kind):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    tool_call = ToolCall(
        tool_call_id=f"tool-{tool_kind}",
        title=f"{tool_kind} controller.js",
        kind=tool_kind,
        status="pending",
        raw_input={"path": "controller.js"},
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=tool_call,
        options=_file_permission_options(),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_file_permission_never_selects_persistent_allow_option(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    tool_call = ToolCall(
        tool_call_id="tool-read",
        title="Read controller.js",
        kind="read",
        status="pending",
        raw_input={"path": "controller.js"},
    )
    options = [
        PermissionOption(option_id="always", name="Always", kind="allow_always"),
        PermissionOption(option_id="once", name="Once", kind="allow_once"),
    ]

    selected_once = await client.request_permission(
        session_id="sess",
        tool_call=tool_call,
        options=options,
    )
    persistent_only = await client.request_permission(
        session_id="sess",
        tool_call=tool_call,
        options=options[:1],
    )

    assert isinstance(selected_once.outcome, AllowedOutcome)
    assert selected_once.outcome.option_id == "once"
    assert isinstance(persistent_only.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_conflicting_duplicate_tool_call_evidence_is_denied(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-reused", "controller.js"),
    )
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-reused", "manifest.json"),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-reused"),
        options=_file_permission_options(),
        **_codex_permission_kwargs("tool-reused"),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "codex_meta",
    [
        {"params": {"itemId": "different-tool", "grantRoot": None}},
        {"params": {"itemId": "tool-meta", "grantRoot": "/tmp"}},
        {"params": "malformed"},
    ],
)
async def test_codex_file_permission_metadata_must_match_without_root_grant(tmp_path, codex_meta):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    await client.session_update(
        session_id="sess",
        update=_codex_file_edit_start("tool-meta", "controller.js"),
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=_pathless_file_permission("tool-meta"),
        options=_file_permission_options(),
        codex=codex_meta,
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_file_permission_with_unknown_raw_input_fields_is_denied(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    tool_call = ToolCall(
        tool_call_id="tool-raw-input",
        title="Editing files",
        kind="edit",
        status="pending",
        raw_input={"path": "controller.js", "unreportedPath": "secrets.txt"},
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=tool_call,
        options=_file_permission_options(),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_file_permission_rejects_json_encoded_raw_input(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    tool_call = ToolCall(
        tool_call_id="tool-json-string",
        title="Read files",
        kind="read",
        status="pending",
        raw_input='{"path":"secrets.txt","path":"controller.js"}',
    )

    response = await client.request_permission(
        session_id="sess",
        tool_call=tool_call,
        options=_file_permission_options(),
    )

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_client_terminal_operations(tmp_path, monkeypatch):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    monkeypatch.setattr(PermissionPolicyManager, "validate_argv", lambda self, argv: True)
    create_resp = await client.create_terminal(
        session_id="sess", command=sys.executable, args=["-c", "print('hello_terminal')"]
    )
    assert isinstance(create_resp, CreateTerminalResponse)
    terminal_id = create_resp.terminal_id
    assert terminal_id is not None

    # Wait for execution and read output
    await asyncio.sleep(0.5)

    output_resp = await client.terminal_output(session_id="sess", terminal_id=terminal_id)
    assert isinstance(output_resp, TerminalOutputResponse)
    assert "hello_terminal" in output_resp.output

    exit_resp = await client.wait_for_terminal_exit(session_id="sess", terminal_id=terminal_id)
    assert isinstance(exit_resp, WaitForTerminalExitResponse)
    assert exit_resp.exit_code == 0

    # Release terminal
    release_resp = await client.release_terminal(session_id="sess", terminal_id=terminal_id)
    assert release_resp is not None


@pytest.mark.asyncio
async def test_client_session_update_callbacks():
    callback_outputs = []

    def on_update(text):
        callback_outputs.append(text)

    client = FastAPIACPClient(workspace_root=Path("."), on_update_callback=on_update)

    # Create chunks
    msg_chunk = AgentMessageChunk(
        session_update="agent_message_chunk", content=text_block("hello world"), message_id="msg-1"
    )

    await client.session_update(session_id="sess", update=msg_chunk)
    assert len(callback_outputs) == 1
    assert callback_outputs[0] == "hello world"

    tool_chunk = ToolCallStart(
        session_update="tool_call", tool_call_id="tc-1", title="write_text_file", kind="edit", status="pending"
    )
    await client.session_update(session_id="sess", update=tool_chunk)
    assert len(callback_outputs) == 2
    assert "write_text_file" in callback_outputs[1]


@pytest.mark.asyncio
async def test_client_session_update_awaits_async_callable_callback():
    callback_outputs = []

    class AsyncUpdateCallback:
        async def __call__(self, text):
            callback_outputs.append(text)

    client = FastAPIACPClient(workspace_root=Path("."), on_update_callback=AsyncUpdateCallback())
    msg_chunk = AgentMessageChunk(
        session_update="agent_message_chunk", content=text_block("streamed"), message_id="msg-1"
    )

    await client.session_update(session_id="sess", update=msg_chunk)

    assert callback_outputs == ["streamed"]


@pytest.mark.asyncio
async def test_run_opencode_agent_acp(monkeypatch, tmp_path):
    # Mock spawn_agent_process
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))

    async def generate_controller(*args, **kwargs):
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text("export default function App() { return null; }", encoding="utf-8")
        _write_manifest(staging_dir, "weather-card")
        return PromptResponse(stop_reason="end_turn")

    mock_conn.prompt = AsyncMock(side_effect=generate_controller)

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        # Trigger on_connect to wire connection
        to_client.on_connect(mock_conn)

        # Simulate some agent updates to test streaming
        msg_chunk = AgentMessageChunk(
            session_update="agent_message_chunk", content=text_block("OpenCode response text"), message_id="msg-1"
        )
        await to_client.session_update(session_id="sess-xyz", update=msg_chunk)

        yield mock_conn, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    updates = []

    def on_update(text):
        updates.append(text)

    result_log = await run_opencode_agent_acp(
        app_id="weather-card", instruction="Make it glassmorphic", on_update=on_update
    )

    assert "OpenCode response text" in result_log
    assert len(updates) > 0
    assert "OpenCode response text" in updates[-1]
    assert (tmp_path / "weather-card" / "controller.js").exists()
    assert list(tmp_path.glob(".weather-card.staging-*")) == []

    # Assert handshake & prompt called
    mock_conn.initialize.assert_called_once()
    mock_conn.new_session.assert_called_once()
    mock_conn.prompt.assert_called_once()


@pytest.mark.asyncio
async def test_acp_repairs_contract_validation_failure_in_the_same_session(monkeypatch, tmp_path):
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-repair"))

    async def generate_controller(*args, **kwargs):
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(
            "export default function App() { return null; }",
            encoding="utf-8",
        )
        _write_manifest(staging_dir, "weather-card")
        return PromptResponse(stop_reason="end_turn")

    mock_conn.prompt = AsyncMock(side_effect=generate_controller)

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    validations = 0

    def validate_contract(_result):
        nonlocal validations
        validations += 1
        if validations == 1:
            raise OpenCodeArtifactError(
                "Staged App capabilities differ from the approved Runtime Contract",
                code="runtime_contract_mismatch",
                stage="runtime_contract",
            )

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    result = await run_opencode_agent_acp(
        app_id="weather-card",
        instruction=(
            "build\n\n[APPROVED RUNTIME CONTRACT — REFERENCE ONLY]\n"
            '{"app_id":"weather-card","capabilities":[]}\n\n[SYSTEM CAPABILITIES]\n...'
        ),
        promote=False,
        artifact_validator=validate_contract,
    )

    assert isinstance(result, OpenCodeStagedResult)
    assert result.repair_attempts == 1
    assert result.repair_findings[0]["code"] == "runtime_contract_mismatch"
    assert validations == 2
    assert mock_conn.new_session.await_count == 1
    assert mock_conn.prompt.await_count == 2
    assert {call.kwargs["session_id"] for call in mock_conn.prompt.await_args_list} == {"sess-repair"}
    repair_prompt = mock_conn.prompt.await_args_list[1].kwargs["prompt"][0].text
    assert "runtime_contract_mismatch" in repair_prompt
    assert "never add or broaden capabilities" in repair_prompt
    assert "APPROVED RUNTIME CONTRACT" in repair_prompt


@pytest.mark.asyncio
async def test_acp_keeps_repairing_distinct_findings_past_three_turns(monkeypatch, tmp_path):
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-many-repairs"))
    prompts = 0

    async def generate_controller(*args, **kwargs):
        nonlocal prompts
        prompts += 1
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(
            f"export default function App() {{ return null; }}\n// revision {prompts}",
            encoding="utf-8",
        )
        _write_manifest(staging_dir, "weather-card")
        return PromptResponse(stop_reason="end_turn")

    mock_conn.prompt = AsyncMock(side_effect=generate_controller)

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    validations = 0

    def validate_contract(_result):
        nonlocal validations
        validations += 1
        if validations <= 4:
            raise OpenCodeArtifactError(
                f"Distinct verifier issue {validations}",
                code="runtime_contract_mismatch",
                stage="runtime_contract",
            )

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    result = await run_opencode_agent_acp(
        app_id="weather-card",
        instruction="build",
        promote=False,
        artifact_validator=validate_contract,
    )

    assert isinstance(result, OpenCodeStagedResult)
    assert result.repair_attempts == 4
    assert len(result.repair_findings) == 4
    assert validations == 5
    assert mock_conn.prompt.await_count == 5


@pytest.mark.asyncio
async def test_acp_restores_findings_and_stops_same_error_across_run_retries(monkeypatch, tmp_path):
    staging_dir = tmp_path / ".weather-card.staging-00000000000000000000000000000000"
    staging_dir.mkdir()
    (staging_dir / "controller.js").write_text(
        "export default function App() { return null; }",
        encoding="utf-8",
    )
    _write_manifest(staging_dir, "weather-card")
    verifier_error = OpenCodeArtifactError(
        "Unexpected token (980:3)",
        code="widget_verification_failed",
        stage="static_verify",
    )
    previous = finding_from_exception(verifier_error, attempt=1, artifact_revision="previous-revision")
    retained = OpenCodeStagedResult(
        output="previous run",
        app_id="weather-card",
        staging_dir=staging_dir,
        live_dir=tmp_path / "weather-card",
        repair_attempts=1,
        repair_findings=(previous.to_dict(),),
        artifact_hash="previous-revision",
    )
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-resumed-repair"))
    mock_conn.prompt = AsyncMock(return_value=PromptResponse(stop_reason="end_turn"))

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    def validate_contract(_result):
        raise OpenCodeArtifactError(
            "Unexpected token (980:3)",
            code="widget_verification_failed",
            stage="static_verify",
        )

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    with pytest.raises(CodingAgentDraftError) as captured:
        await run_opencode_agent_acp(
            app_id="weather-card",
            instruction="repair the retained draft",
            promote=False,
            staged_result=retained,
            artifact_validator=validate_contract,
        )

    assert mock_conn.prompt.await_count == 1
    assert captured.value.repair_action == "human"
    assert "same verifier finding" in captured.value.repair_reason
    assert captured.value.staged_result.repair_attempts == 1
    assert len(captured.value.staged_result.repair_findings) == 2
    assert captured.value.staged_result.repair_findings[-1]["attempt"] == 2


@pytest.mark.asyncio
async def test_acp_does_not_send_operator_failure_back_to_coding_agent(monkeypatch, tmp_path):
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-operator"))

    async def generate_controller(*args, **kwargs):
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(
            "export default function App() { return null; }",
            encoding="utf-8",
        )
        _write_manifest(staging_dir, "weather-card")
        return PromptResponse(stop_reason="end_turn")

    mock_conn.prompt = AsyncMock(side_effect=generate_controller)

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    def unavailable_verifier(_result):
        raise OpenCodeArtifactError(
            "Widget syntax/runtime verifier is unavailable",
            code="widget_verifier_unavailable",
            stage="static_verify",
        )

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    with pytest.raises(CodingAgentDraftError) as captured:
        await run_opencode_agent_acp(
            app_id="weather-card",
            instruction="build",
            promote=False,
            artifact_validator=unavailable_verifier,
        )

    assert mock_conn.prompt.await_count == 1
    assert captured.value.repair_action == "operator"
    assert captured.value.finding is not None
    assert captured.value.finding["code"] == "widget_verifier_unavailable"


@pytest.mark.asyncio
async def test_run_opencode_agent_acp_timeout(monkeypatch, tmp_path):
    # Mock spawn_agent_process
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))

    async def hanging_prompt(*args, **kwargs):
        await asyncio.sleep(5.0)
        return PromptResponse(stop_reason="end_turn")

    mock_conn.prompt = hanging_prompt

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    monkeypatch.setenv("OPENCODE_TIMEOUT", "0.01")

    with pytest.raises(OpenCodeACPTimeoutError, match=r"timed out after 0\.01 seconds"):
        await run_opencode_agent_acp(
            app_id="weather-card", instruction="Make it glassmorphic", on_update=lambda x: None
        )
    assert not (tmp_path / "weather-card").exists()


@pytest.mark.asyncio
async def test_client_directory_traversal(tmp_path):
    # Setup target workspace dir
    workspace_root = tmp_path / "app_workspace"
    workspace_root.mkdir()

    # 2. Instantiate client
    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=lambda x: None)

    # Test read_text_file outside workspace
    with pytest.raises(RequestError) as excinfo:
        await client.read_text_file(session_id="sess", path="../conftest.py")
    assert "Directory traversal attempt blocked" in getattr(excinfo.value, "data", "")

    # Test write_text_file outside workspace
    with pytest.raises(RequestError) as excinfo:
        await client.write_text_file(session_id="sess", path="../unsafe.txt", content="unsafe")
    assert "Directory traversal attempt blocked" in getattr(excinfo.value, "data", "")


def test_permission_policy_manager(tmp_path):
    # Write custom configuration file
    config_file = tmp_path / "custom_policy.json"
    import json

    policy_data = {
        "policy_mode": "interactive",
        "files": {"allowed_filenames": ["controller.js", "manifest.json", "README.md"]},
        "commands": {
            "allowed_commands": ["npm test", "npm run build"],
            "allowed_prefixes": ["npm install "],
            "blocklist": ["rm -rf", "curl"],
        },
    }
    config_file.write_text(json.dumps(policy_data), encoding="utf-8")

    mgr = PermissionPolicyManager(config_path=str(config_file))

    # Test file paths
    assert mgr.validate_file_path("controller.js", tmp_path) is True
    assert mgr.validate_file_path("manifest.json", tmp_path) is True
    assert mgr.validate_file_path("nested/controller.js", tmp_path) is False
    assert mgr.validate_file_path("unsafe.exe", tmp_path) is False
    assert mgr.validate_file_path("../conftest.py", tmp_path) is False  # Directory traversal blocked

    # Test commands
    assert mgr.validate_command("npm run build") is True
    assert mgr.validate_command("npm install lodash") is False
    assert mgr.validate_command("rm -rf /") is False
    assert mgr.validate_command("curl http://unsafe.site") is False
    assert mgr.validate_command("python script.py") is False  # Not whitelisted
    assert mgr.validate_command("echo safe; touch escaped") is False
    assert mgr.validate_command("npm installer") is False


@pytest.mark.asyncio
async def test_out_of_policy_permission_fails_closed_without_callback(tmp_path, monkeypatch):
    # Mock PermissionPolicyManager to deny execution by default
    from backend.coding_agent_acp import PermissionPolicyManager

    monkeypatch.setattr(PermissionPolicyManager, "validate_argv", lambda self, argv: False)

    workspace_root = tmp_path / "app_workspace"
    workspace_root.mkdir()

    callback_payloads = []

    async def on_update(payload):
        if isinstance(payload, dict):
            callback_payloads.append(payload)

    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=on_update)

    # Mock a tool call for create_terminal
    class MockToolCall:
        kind = "execute"
        raw_input = {"command": "npm install lodash", "args": []}
        title = "run command"
        content = None

    options = [PermissionOption(option_id="opt-allow", name="Allow", kind="allow_always")]

    resp = await client.request_permission(session_id="sess", tool_call=MockToolCall(), options=options)

    assert callback_payloads == []
    assert isinstance(resp, RequestPermissionResponse)
    assert isinstance(resp.outcome, DeniedOutcome)
    assert resp.outcome.outcome == "cancelled"


@pytest.mark.asyncio
async def test_unknown_tool_kind_is_denied(tmp_path):
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    options = [PermissionOption(option_id="opt-allow", name="Allow", kind="allow_always")]
    tool_call = MagicMock(kind="network_probe", raw_input={}, content=None, title="probe")

    response = await client.request_permission(session_id="sess", tool_call=tool_call, options=options)

    assert isinstance(response.outcome, DeniedOutcome)


@pytest.mark.asyncio
async def test_path_jail_rejects_sibling_and_symlink_escape(tmp_path):
    workspace_root = tmp_path / "app"
    sibling = tmp_path / "app-evil"
    workspace_root.mkdir()
    sibling.mkdir()
    (sibling / "payload.js").write_text("secret", encoding="utf-8")
    (workspace_root / "linked.js").symlink_to(sibling / "payload.js")
    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=lambda x: None)

    with pytest.raises(RequestError):
        await client.read_text_file(session_id="sess", path=str(sibling / "payload.js"))
    with pytest.raises(RequestError):
        await client.read_text_file(session_id="sess", path="linked.js")
    with pytest.raises(RequestError):
        await client.write_text_file(session_id="sess", path="linked.js", content="overwritten")

    assert (sibling / "payload.js").read_text(encoding="utf-8") == "secret"


@pytest.mark.asyncio
async def test_terminal_rejects_shell_syntax_and_cwd_escape(tmp_path):
    workspace_root = tmp_path / "app"
    sibling = tmp_path / "sibling"
    workspace_root.mkdir()
    sibling.mkdir()
    client = FastAPIACPClient(workspace_root=workspace_root, on_update_callback=lambda x: None)

    with pytest.raises(RequestError):
        await client.create_terminal(session_id="sess", command="echo safe; touch escaped")
    with pytest.raises(RequestError):
        await client.create_terminal(session_id="sess", command="echo", args=["safe"], cwd=str(sibling))

    escaped = workspace_root / "escaped-from-arg"
    with pytest.raises(RequestError):
        await client.create_terminal(session_id="sess", command="echo", args=[f"safe; touch {escaped}"])
    assert not (workspace_root / "escaped").exists()
    assert not escaped.exists()


@pytest.mark.asyncio
async def test_terminal_kill_terminates_isolated_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(PermissionPolicyManager, "validate_argv", lambda self, argv: True)
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    response = await client.create_terminal(
        session_id="sess",
        command=sys.executable,
        args=["-c", "import time; time.sleep(30)"],
    )

    await client.kill_terminal(session_id="sess", terminal_id=response.terminal_id)

    assert client.terminals[response.terminal_id].returncode is not None
    await client.release_terminal(session_id="sess", terminal_id=response.terminal_id)


@pytest.mark.asyncio
async def test_terminal_uses_environment_allowlist_and_output_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(PermissionPolicyManager, "validate_argv", lambda self, argv: True)
    monkeypatch.setenv("AMBIENT_TEST_SECRET", "must-not-leak")
    client = FastAPIACPClient(workspace_root=tmp_path, on_update_callback=lambda x: None)
    code = (
        "import os; print(os.getenv('AMBIENT_TEST_SECRET', 'missing')); print(os.getenv('NODE_ENV')); print('x' * 100)"
    )

    response = await client.create_terminal(
        session_id="sess",
        command=sys.executable,
        args=["-c", code],
        env=[EnvVariable(name="NODE_ENV", value="test")],
        output_byte_limit=32,
    )
    await client.wait_for_terminal_exit(session_id="sess", terminal_id=response.terminal_id)
    output = await client.terminal_output(session_id="sess", terminal_id=response.terminal_id)

    assert output.output.startswith("missing\ntest\n")
    assert len(output.output.encode()) == 32
    assert output.truncated is True
    await client.release_terminal(session_id="sess", terminal_id=response.terminal_id)

    with pytest.raises(RequestError) as excinfo:
        await client.create_terminal(
            session_id="sess",
            command=sys.executable,
            args=["-c", "pass"],
            env=[EnvVariable(name="OPENAI_API_KEY", value="secret")],
        )
    assert "Environment variable is not allowed" in getattr(excinfo.value, "data", "")


@pytest.mark.asyncio
async def test_invalid_app_id_is_rejected_before_filesystem_access(monkeypatch, tmp_path):
    monkeypatch.setenv("APPS_DIR", str(tmp_path / "apps"))

    with pytest.raises(OpenCodeACPInputError, match="invalid app_id"):
        await run_opencode_agent_acp(app_id="../escaped-app", instruction="unsafe")

    assert not (tmp_path / "escaped-app").exists()
    assert not (tmp_path / "apps").exists()


@pytest.mark.asyncio
async def test_acp_startup_failure_is_typed_and_cleans_staging(monkeypatch, tmp_path):
    @contextlib.asynccontextmanager
    async def broken_spawn(*args, **kwargs):
        raise FileNotFoundError("opencode missing")
        yield  # pragma: no cover

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", broken_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    with pytest.raises(OpenCodeACPStartupError, match="Unable to start"):
        await run_opencode_agent_acp(app_id="weather-card", instruction="build")

    assert not (tmp_path / "weather-card").exists()
    assert list(tmp_path.glob(".weather-card.staging-*")) == []


@pytest.mark.asyncio
async def test_acp_protocol_failure_leaves_existing_app_untouched(monkeypatch, tmp_path):
    live_dir = tmp_path / "weather-card"
    live_dir.mkdir()
    old_source = "export default function OldApp() { return null; }"
    (live_dir / "controller.js").write_text(old_source, encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))

    async def failed_prompt(*args, **kwargs):
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(
            "export default function NewApp() { return null; }", encoding="utf-8"
        )
        raise RuntimeError("malformed ACP response")

    mock_conn.prompt = failed_prompt

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    with pytest.raises(OpenCodeACPProtocolError, match="protocol failed"):
        await run_opencode_agent_acp(app_id="weather-card", instruction="modify")

    assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_source
    assert list(tmp_path.glob(".weather-card.staging-*")) == []


@pytest.mark.asyncio
async def test_durable_acp_protocol_failure_transfers_generated_draft(monkeypatch, tmp_path):
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))

    async def failed_prompt(*args, **kwargs):
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(
            "export default function GeneratedDraft() { return null; }",
            encoding="utf-8",
        )
        raise RuntimeError("malformed ACP response")

    mock_conn.prompt = failed_prompt

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    with pytest.raises(CodingAgentDraftError) as captured:
        await run_opencode_agent_acp(
            app_id="weather-card",
            instruction="modify",
            promote=False,
        )

    draft = captured.value.staged_result
    assert captured.value.error_code == "CodingAgentACPProtocolError"
    assert draft.staging_dir.is_dir()
    assert "GeneratedDraft" in (draft.staging_dir / "controller.js").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_acp_missing_artifact_fails_closed(monkeypatch, tmp_path):
    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))
    mock_conn.prompt = AsyncMock(return_value=PromptResponse(stop_reason="end_turn"))

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    with pytest.raises(OpenCodeArtifactError, match=r"required controller\.js"):
        await run_opencode_agent_acp(app_id="weather-card", instruction="build")

    assert not (tmp_path / "weather-card").exists()


@pytest.mark.asyncio
async def test_acp_can_retain_validate_and_promote_staging(monkeypatch, tmp_path):
    live_dir = tmp_path / "weather-card"
    live_dir.mkdir()
    old_source = "export default function OldApp() { return null; }"
    new_source = "export default function NewApp() { return null; }"
    (live_dir / "controller.js").write_text(old_source, encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))

    async def generate_controller(*args, **kwargs):
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(new_source, encoding="utf-8")
        _write_manifest(staging_dir, "weather-card")
        return PromptResponse(stop_reason="end_turn")

    mock_conn.prompt = generate_controller

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    result = await run_opencode_agent_acp(app_id="weather-card", instruction="modify", promote=False)

    assert isinstance(result, OpenCodeStagedResult)
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_source
    assert validate_opencode_staging(result).read_text(encoding="utf-8") == new_source
    promoted_path = promote_opencode_staging(result)
    assert promoted_path == live_dir
    assert (live_dir / "controller.js").read_text(encoding="utf-8") == new_source
    assert not result.staging_dir.exists()


@pytest.mark.asyncio
async def test_discard_retained_staging_preserves_live_app(monkeypatch, tmp_path):
    live_dir = tmp_path / "weather-card"
    live_dir.mkdir()
    old_source = "export default function OldApp() { return null; }"
    (live_dir / "controller.js").write_text(old_source, encoding="utf-8")

    mock_conn = AsyncMock()
    mock_conn.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    mock_conn.new_session = AsyncMock(return_value=NewSessionResponse(session_id="sess-xyz"))

    async def generate_controller(*args, **kwargs):
        staging_dir = Path(mock_conn.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(
            "export default function DiscardedApp() { return null; }", encoding="utf-8"
        )
        _write_manifest(staging_dir, "weather-card")
        return PromptResponse(stop_reason="end_turn")

    mock_conn.prompt = generate_controller

    @contextlib.asynccontextmanager
    async def mock_spawn(to_client, command, *args, **kwargs):
        to_client.on_connect(mock_conn)
        yield mock_conn, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", mock_spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))

    result = await run_opencode_agent_acp(app_id="weather-card", instruction="modify", promote=False)
    assert isinstance(result, OpenCodeStagedResult)
    discard_opencode_staging(result)
    discard_opencode_staging(result)

    assert (live_dir / "controller.js").read_text(encoding="utf-8") == old_source
    assert not result.staging_dir.exists()
