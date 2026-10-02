import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse

from backend.app_manifest import AppManifest
from backend.coding_agent_acp import (
    CodingAgentArtifactError,
    CodingAgentDraftError,
    CodingAgentStagedResult,
    run_coding_agent_acp,
    validate_coding_agent_staging,
)
from backend.coding_agent_repair import finding_from_exception


def manifest_data():
    return {
        "manifest_version": 2,
        "id": "weather-app",
        "title": "Weather",
        "description": "Draft UI",
        "app_version": "0.1.0",
        "intents": [],
        "schema_refs": [],
        "capabilities": [],
        "app_spec": {
            "spec_version": 1,
            "types": [{"id": "custom:weather", "title": {"zh": "天气"}}],
            "features": [{"id": "custom:weather.display", "status": "partial", "surfaces": ["ui"]}],
        },
    }


def write_artifacts(directory, manifest):
    (directory / "controller.js").write_text(
        "const { Text } = ambient.components; export default function App() { "
        'return ambient.html`<${Text} text="Draft" />`; }',
        encoding="utf-8",
    )
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_manifest_shape_diagnostic_survives_artifact_and_repair_boundaries(tmp_path):
    staging = tmp_path / f".weather-app.staging-{'a' * 32}"
    staging.mkdir()
    manifest = manifest_data()
    manifest["app_spec"]["types"][0]["description"] = "private-payload-should-not-leak"
    write_artifacts(staging, manifest)
    staged = CodingAgentStagedResult("", "weather-app", staging, tmp_path / "weather-app")

    with pytest.raises(CodingAgentArtifactError) as captured:
        validate_coding_agent_staging(staged)

    error = captured.value
    finding = finding_from_exception(error, attempt=1, artifact_revision="object-types")
    assert error.path == "app_spec.types[0]"
    assert finding.expected == "non-empty type ID string (max 200 characters)"
    assert finding.observed == "object"
    assert finding.locations == ("manifest.json:$.app_spec.types[0]",)
    assert finding.repairability == "code_only"
    assert "private-payload-should-not-leak" not in json.dumps(finding.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["fix_shape", "rename_only"])
async def test_real_manifest_validation_guides_same_session_shape_repair(tmp_path, monkeypatch, repair):
    connection = AsyncMock()
    connection.initialize.return_value = InitializeResponse(protocolVersion=1)
    connection.new_session.return_value = NewSessionResponse(session_id="manifest-repair")
    prompts = []

    async def generate(*, prompt, **kwargs):
        text = prompt[0].text
        prompts.append(text)
        stage = Path(connection.new_session.call_args.kwargs["cwd"])
        if len(prompts) == 1:
            write_artifacts(stage, manifest_data())
        else:
            finding, _ = json.JSONDecoder().raw_decode(text.split("[STRUCTURED REPAIR FINDING]\n", 1)[1])
            assert finding["observed"] == "object"
            assert "type ID string" in finding["expected"]
            assert finding["locations"] == ["manifest.json:$.app_spec.types[0]"]
            assert '"types":["custom:weather"]' in text.replace(" ", "")
            manifest = json.loads((stage / "manifest.json").read_text())
            if repair == "fix_shape":
                manifest["app_spec"]["types"] = [item["id"] for item in manifest["app_spec"]["types"]]
            else:
                manifest["app_spec"]["types"][0]["id"] = "custom:weather-app"
                manifest["app_spec"]["features"][0]["id"] = "custom:weather-app.display"
            (stage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return PromptResponse(stop_reason="end_turn")

    connection.prompt.side_effect = generate

    @contextlib.asynccontextmanager
    async def spawn(client, *args, **kwargs):
        yield connection, MagicMock(returncode=0)

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", spawn)
    monkeypatch.setenv("APPS_DIR", str(tmp_path))
    launch = SimpleNamespace(
        agent_id="opencode", agent_name="OpenCode", argv=("test-acp",), environment={}, timeout_seconds=5.0
    )
    if repair == "fix_shape":
        result = await run_coding_agent_acp("weather-app", "Create the draft UI", launch=launch, promote=False)
        assert result.repair_attempts == 1
        assert AppManifest.read(result.staging_dir / "manifest.json", expected_app_id="weather-app").app_spec.types == (
            "custom:weather",
        )
    else:
        with pytest.raises(CodingAgentDraftError) as captured:
            await run_coding_agent_acp("weather-app", "Create the draft UI", launch=launch, promote=False)
        result = captured.value.staged_result
        assert captured.value.repair_action == "human"
        assert result.repair_findings[0]["signature"] == result.repair_findings[1]["signature"]
        assert result.staging_dir.is_dir()
    assert len(prompts) == 2
    assert not (tmp_path / "weather-app").exists()
