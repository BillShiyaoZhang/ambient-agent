import contextlib
import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse

import backend.coding_agent as coding_agent_module
import backend.coding_agent_acp as acp_module
from backend.app_manifest import MAX_MANIFEST_BYTES, AppManifest
from backend.coding_agent_acp import (
    CodingAgentACPInputError,
    CodingAgentDraftError,
    CodingAgentStagedResult,
    run_coding_agent_acp,
    run_opencode_agent_acp,
)
from backend.coding_agent_repair import RepairDirective, app_spec_declaration_rules


def manifest_template(**changes):
    result = {
        "manifest_version": 2,
        "id": "seed-app",
        "title": "批准的应用",
        "description": "Manifest seed",
        "app_version": "0.1.0",
        "intents": ["展示信息"],
        "schema_refs": ["SoftwareApplication"],
        "capabilities": [],
        "app_spec": {
            "spec_version": 1,
            "types": ["custom:seed"],
            "features": [{"id": "custom:seed.display", "status": "implemented", "surfaces": ["ui"]}],
        },
    }
    result.update(changes)
    return result


@pytest.fixture
def acp_session(tmp_path, monkeypatch):
    apps = tmp_path / "apps"
    monkeypatch.setenv("APPS_DIR", str(apps))
    connection = AsyncMock()
    connection.initialize.return_value = InitializeResponse(protocolVersion=1)
    connection.new_session.return_value = NewSessionResponse(session_id="seed-session")
    state = SimpleNamespace(connection=connection, apps=apps, spawned=[], prompts=[], before_prompt=None)

    async def generate(*, prompt, **kwargs):
        stage = Path(connection.new_session.call_args.kwargs["cwd"])
        state.prompts.append(prompt[0].text)
        if state.before_prompt is not None:
            state.before_prompt(stage)
        (stage / "controller.js").write_text(
            "const { Text } = ambient.components; export default function App() { "
            'return ambient.html`<${Text} text="Content" />`; }',
            encoding="utf-8",
        )
        return PromptResponse(stop_reason="end_turn")

    connection.prompt.side_effect = generate

    @contextlib.asynccontextmanager
    async def spawn(client, *args, **kwargs):
        state.spawned.append(Path(kwargs["cwd"]))
        yield connection, MagicMock(returncode=0)

    monkeypatch.setattr(acp_module, "spawn_agent_process", spawn)
    state.launch = SimpleNamespace(
        agent_id="codex", agent_name="Codex", argv=("test-acp",), environment={}, timeout_seconds=5.0
    )
    return state


@pytest.mark.asyncio
async def test_new_generation_starts_with_canonical_approved_manifest_and_no_controller(acp_session):
    template = manifest_template(backend_type="code", mcp_server=None, agent_url=None)

    def inspect_initial_artifacts(stage):
        manifest = AppManifest.read(stage / "manifest.json", expected_app_id="seed-app")
        assert manifest.to_dict() == AppManifest.from_dict(template, expected_app_id="seed-app").to_dict()
        assert not (stage / "controller.js").exists()
        assert not (stage / "README.md").exists()

    acp_session.before_prompt = inspect_initial_artifacts
    result = await run_coding_agent_acp(
        "seed-app", "Build the approved UI", launch=acp_session.launch, promote=False, manifest_template=template
    )

    assert isinstance(result, CodingAgentStagedResult)
    assert result.repair_attempts == 0
    assert app_spec_declaration_rules() in acp_session.prompts[0]
    assert not (acp_session.apps / "seed-app").exists()


@pytest.mark.asyncio
async def test_valid_mapping_template_is_supported(acp_session):
    template: Mapping = MappingProxyType(manifest_template())
    result = await run_coding_agent_acp(
        "seed-app", "Build", launch=acp_session.launch, promote=False, manifest_template=template
    )
    assert AppManifest.read(result.staging_dir / "manifest.json", expected_app_id="seed-app").title == "批准的应用"


def compact_manifest_bytes(template):
    canonical = AppManifest.from_dict(template, expected_app_id="seed-app").to_dict()
    return (json.dumps(canonical, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def large_valid_template():
    template = manifest_template()
    template["app_spec"]["features"] = [
        {"id": f"custom:seed.feature-{i}", "status": "planned", "surfaces": [], "notes": "x" * 2000} for i in range(31)
    ]
    return template


@pytest.mark.parametrize("compact", [False, True])
def test_manifest_atomic_write_uses_requested_json_format(tmp_path, compact):
    template = manifest_template()
    manifest = AppManifest.from_dict(template, expected_app_id="seed-app")
    path = tmp_path / "manifest.json"
    manifest.write_atomic(path, compact=compact)
    if compact:
        expected = compact_manifest_bytes(template)
    else:
        expected = (json.dumps(template, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert path.read_bytes() == expected
    assert AppManifest.read(path, expected_app_id="seed-app").to_dict() == template


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fresh", "existing", "retained"])
async def test_compact_manifest_below_limit_remains_valid_when_pretty_format_exceeds_limit(acp_session, source):
    template = large_valid_template()
    contents = compact_manifest_bytes(template)
    assert len(contents) < MAX_MANIFEST_BYTES
    pretty = (json.dumps(template, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    assert len(pretty) > MAX_MANIFEST_BYTES
    retained = None
    if source == "existing":
        directory = acp_session.apps / "seed-app"
    elif source == "retained":
        directory = acp_session.apps / f".seed-app.staging-{'b' * 32}"
        retained = CodingAgentStagedResult("", "seed-app", directory, acp_session.apps / "seed-app")
    if source != "fresh":
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_bytes(contents)

    def inspect_initial_manifest(stage):
        assert (stage / "manifest.json").read_bytes() == contents
        assert AppManifest.read(stage / "manifest.json", expected_app_id="seed-app").to_dict() == template

    acp_session.before_prompt = inspect_initial_manifest
    result = await run_coding_agent_acp(
        "seed-app",
        "Build",
        launch=acp_session.launch,
        promote=False,
        staged_result=retained,
        manifest_template=template,
    )
    assert (result.staging_dir / "manifest.json").read_bytes() == contents
    assert result.repair_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("byte_size", [MAX_MANIFEST_BYTES, MAX_MANIFEST_BYTES + 1])
async def test_seed_manifest_enforces_exact_compact_utf8_size_boundary(acp_session, byte_size):
    template = large_valid_template()
    template["description"] += "x" * (byte_size - len(compact_manifest_bytes(template)))
    contents = compact_manifest_bytes(template)
    assert len(contents) == byte_size
    if byte_size > MAX_MANIFEST_BYTES:
        with pytest.raises(CodingAgentACPInputError, match="maximum size"):
            await run_coding_agent_acp(
                "seed-app", "Build", launch=acp_session.launch, promote=False, manifest_template=template
            )
        assert acp_session.spawned == []
        assert not acp_session.apps.exists()
    else:
        result = await run_coding_agent_acp(
            "seed-app", "Build", launch=acp_session.launch, promote=False, manifest_template=template
        )
        assert (result.staging_dir / "manifest.json").read_bytes() == contents
        assert AppManifest.read(result.staging_dir / "manifest.json", expected_app_id="seed-app").to_dict() == template


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["shape", "id", "mapping", "size"])
async def test_invalid_template_is_rejected_before_staging_or_process(acp_session, invalid):
    template = manifest_template()
    if invalid == "shape":
        template["app_spec"]["types"] = [{"id": "custom:seed"}]
    elif invalid == "id":
        template["id"] = "another-app"
    elif invalid == "mapping":
        template = [template]
    else:
        template["app_spec"]["features"] = [
            {"id": f"custom:seed.feature-{i}", "status": "planned", "surfaces": [], "notes": "文" * 2000}
            for i in range(12)
        ]
    with pytest.raises(CodingAgentACPInputError):
        await run_coding_agent_acp(
            "seed-app", "Build", launch=acp_session.launch, promote=False, manifest_template=template
        )
    assert acp_session.spawned == []
    acp_session.connection.prompt.assert_not_called()
    assert not acp_session.apps.exists()


@pytest.mark.asyncio
async def test_existing_app_manifest_and_private_data_are_preserved(acp_session):
    live = acp_session.apps / "seed-app"
    live.mkdir(parents=True)
    existing_bytes = json.dumps(manifest_template(title="Existing title"), ensure_ascii=False).encode()
    (live / "manifest.json").write_bytes(existing_bytes)
    (live / "data").mkdir()
    (live / "data" / "private.txt").write_text("retained app data", encoding="utf-8")

    def inspect_initial_artifacts(stage):
        assert (stage / "manifest.json").read_bytes() == existing_bytes
        assert (stage / "data" / "private.txt").read_text() == "retained app data"

    acp_session.before_prompt = inspect_initial_artifacts
    result = await run_coding_agent_acp(
        "seed-app", "Modify", launch=acp_session.launch, promote=False, manifest_template=manifest_template()
    )
    assert (result.staging_dir / "manifest.json").read_bytes() == existing_bytes
    assert (live / "manifest.json").read_bytes() == existing_bytes
    assert not (live / "controller.js").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("retained_contents", [None, b'{"app_spec":{"types":[{"id":"custom:seed"}]}}'])
async def test_retained_draft_is_never_seeded_or_overwritten(acp_session, retained_contents):
    stage = acp_session.apps / f".seed-app.staging-{'a' * 32}"
    stage.mkdir(parents=True)
    if retained_contents is not None:
        (stage / "manifest.json").write_bytes(retained_contents)
    retained = CodingAgentStagedResult("", "seed-app", stage, acp_session.apps / "seed-app")

    def inspect_initial_artifacts(directory):
        assert directory == stage
        if retained_contents is None:
            assert not (directory / "manifest.json").exists()
        else:
            assert (directory / "manifest.json").read_bytes() == retained_contents
        (directory / "manifest.json").write_text(json.dumps(manifest_template()), encoding="utf-8")

    acp_session.before_prompt = inspect_initial_artifacts
    result = await run_coding_agent_acp(
        "seed-app",
        "Repair retained draft",
        launch=acp_session.launch,
        promote=False,
        staged_result=retained,
        manifest_template=manifest_template(),
    )
    assert result.staging_dir == stage


@pytest.mark.asyncio
async def test_without_template_the_model_still_creates_manifest(acp_session):
    def create_manifest(stage):
        assert not (stage / "manifest.json").exists()
        (stage / "manifest.json").write_text(json.dumps(manifest_template()), encoding="utf-8")

    acp_session.before_prompt = create_manifest
    result = await run_coding_agent_acp("seed-app", "Build", launch=acp_session.launch, promote=False)
    assert result.repair_attempts == 0


@pytest.mark.asyncio
async def test_manifest_seed_cannot_substitute_for_generated_controller(acp_session):
    acp_session.connection.prompt.side_effect = None
    acp_session.connection.prompt.return_value = PromptResponse(stop_reason="end_turn")
    with pytest.raises(CodingAgentDraftError) as captured:
        await run_coding_agent_acp(
            "seed-app",
            "Build",
            launch=acp_session.launch,
            promote=False,
            manifest_template=manifest_template(),
            repair_decider=lambda _finding, _history: RepairDirective("human", "No controller was generated"),
        )
    retained = captured.value.staged_result
    assert "required controller.js" in str(captured.value)
    assert (retained.staging_dir / "manifest.json").is_file()
    assert not (retained.staging_dir / "controller.js").exists()
    assert not (acp_session.apps / "seed-app").exists()


@pytest.mark.asyncio
async def test_failed_seed_write_retains_draft_without_partial_manifest_or_model_call(acp_session, monkeypatch):
    def fail_serialization(*args, **kwargs):
        args[1].write('{"manifest_version":')
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", fail_serialization)
    with pytest.raises(CodingAgentDraftError) as captured:
        await run_coding_agent_acp(
            "seed-app", "Build", launch=acp_session.launch, promote=False, manifest_template=manifest_template()
        )
    assert captured.value.error_code == "CodingAgentACPStartupError"
    assert captured.value.repair_action == "operator"
    assert acp_session.spawned == []
    assert list(captured.value.staged_result.staging_dir.iterdir()) == []
    assert not (acp_session.apps / "seed-app").exists()


@pytest.mark.asyncio
async def test_existing_unsafe_manifest_link_blocks_seeding_before_process(acp_session, monkeypatch):
    live = acp_session.apps / "seed-app"
    live.mkdir(parents=True)
    manifest = live / "manifest.json"
    existing_bytes = b"private external contents"
    manifest.write_bytes(existing_bytes)
    is_link = acp_module._is_link_or_junction
    monkeypatch.setattr(acp_module, "_is_link_or_junction", lambda path: path == manifest or is_link(path))

    with pytest.raises(CodingAgentACPInputError, match="unsafe link"):
        await run_coding_agent_acp(
            "seed-app", "Modify", launch=acp_session.launch, promote=False, manifest_template=manifest_template()
        )
    assert acp_session.spawned == []
    assert manifest.read_bytes() == existing_bytes
    assert list(acp_session.apps.iterdir()) == [live]


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["registry", "opencode_compatibility"])
async def test_coding_runner_entrypoints_forward_structured_manifest(monkeypatch, entrypoint):
    template = manifest_template()
    invocation = AsyncMock(return_value="staged")
    if entrypoint == "registry":
        monkeypatch.setattr(coding_agent_module, "run_coding_agent_acp", invocation)
        runtime = SimpleNamespace(
            acp_launch=MagicMock(return_value=SimpleNamespace(agent_id="codex")), ensure_coding_ready=AsyncMock()
        )
        result = await coding_agent_module.run_coding_agent(
            "seed-app", "Build", coding_agent="codex", runtime=runtime, manifest_template=template
        )
    else:
        monkeypatch.setattr(acp_module, "run_coding_agent_acp", invocation)
        monkeypatch.setattr(acp_module, "_opencode_runtime_env", lambda: {})
        result = await run_opencode_agent_acp("seed-app", "Build", manifest_template=template)
    assert result == "staged"
    assert invocation.await_args.kwargs["manifest_template"] is template
