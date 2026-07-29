import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse

import backend.coding_agent as coding_agent_module
import backend.main as main_module
from backend.coding_agent_runtime import CodingAgentRuntime
from backend.coding_agent_acp import CodingAgentArtifactError, CodingAgentStagedResult


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_runtime_builds_native_and_bridged_acp_launch_descriptors(tmp_path, monkeypatch):
    opencode = _executable(tmp_path / "opencode")
    codex = _executable(tmp_path / "codex")
    codex_acp = _executable(tmp_path / "codex-acp")
    monkeypatch.setenv("OPENCODE_COMMAND", opencode)
    monkeypatch.setenv("CODEX_COMMAND", codex)
    monkeypatch.setenv("CODEX_ACP_COMMAND", codex_acp)
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret-must-not-leak")
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    native = runtime.acp_launch("opencode")
    bridged = runtime.acp_launch("codex", native_model="gpt-test")

    assert native.agent_id == "opencode"
    assert native.argv == (opencode, "acp")
    assert native.transport == "native"
    assert bridged.agent_id == "codex"
    assert bridged.argv == (codex_acp,)
    assert bridged.transport == "bridge"
    assert bridged.environment["CODEX_PATH"] == codex
    assert json.loads(bridged.environment["CODEX_CONFIG"]) == {"model": "gpt-test"}
    assert bridged.environment["INITIAL_AGENT_MODE"] == "agent"
    assert bridged.environment["NO_BROWSER"] == "1"
    assert "OPENAI_API_KEY" not in bridged.environment


@pytest.mark.asyncio
async def test_runtime_does_not_report_agent_available_when_its_acp_bridge_is_missing(tmp_path, monkeypatch):
    codex = _executable(tmp_path / "codex")
    monkeypatch.setenv("CODEX_COMMAND", codex)
    monkeypatch.setenv("CODEX_ACP_COMMAND", str(tmp_path / "missing-codex-acp"))
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    status = await runtime.status("codex")

    assert status["installed"] is True
    assert status["available"] is False
    assert "ACP" in status["status_detail"]


@pytest.mark.asyncio
async def test_all_registered_agents_dispatch_through_the_same_acp_runner(tmp_path, monkeypatch):
    launches = {
        agent_id: SimpleNamespace(
            agent_id=agent_id,
            agent_name=agent_id.title(),
            argv=(f"/{agent_id}-acp",),
            environment={},
            timeout_seconds=12.0,
            transport="native" if agent_id == "opencode" else "bridge",
        )
        for agent_id in ("opencode", "codex")
    }
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    monkeypatch.setattr(
        runtime,
        "acp_launch",
        lambda agent_id, **_kwargs: launches[agent_id],
    )
    calls = []

    async def fake_acp_runner(app_id, instruction, **kwargs):
        calls.append((app_id, instruction, kwargs))
        return kwargs["launch"].agent_id

    monkeypatch.setattr(coding_agent_module, "run_coding_agent_acp", fake_acp_runner)

    results = [
        await coding_agent_module.run_coding_agent(
            f"{agent_id}-app",
            "build",
            coding_agent=agent_id,
            runtime=runtime,
            model_config={"native_model": "gpt-test"} if agent_id == "codex" else {},
        )
        for agent_id in ("opencode", "codex")
    ]

    assert results == ["opencode", "codex"]
    assert [call[2]["launch"].transport for call in calls] == ["native", "bridge"]
    assert all(call[2]["promote"] is True for call in calls)


@pytest.mark.asyncio
async def test_main_composition_root_has_no_opencode_execution_bypass(monkeypatch):
    calls = []

    async def fake_runner(app_id, instruction, **kwargs):
        calls.append((app_id, instruction, kwargs))
        return "staged"

    monkeypatch.setattr(main_module, "run_coding_agent", fake_runner)

    result = await main_module._run_coding_agent_staged(
        "weather-app",
        "build",
        coding_agent="opencode",
        coding_agent_model={"mode": "shared_binding", "inherit": "ambient.primary"},
    )

    assert result == "staged"
    assert len(calls) == 1
    assert calls[0][2]["coding_agent"] == "opencode"


@pytest.mark.asyncio
async def test_bridged_codex_repairs_in_the_same_acp_session(tmp_path, monkeypatch):
    codex = _executable(tmp_path / "codex")
    codex_acp = _executable(tmp_path / "codex-acp")
    monkeypatch.setenv("CODEX_COMMAND", codex)
    monkeypatch.setenv("CODEX_ACP_COMMAND", codex_acp)
    monkeypatch.setenv("APPS_DIR", str(tmp_path / "apps"))
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    connection = AsyncMock()
    connection.initialize = AsyncMock(return_value=InitializeResponse(protocolVersion=1))
    connection.new_session = AsyncMock(return_value=NewSessionResponse(session_id="codex-acp-session"))

    async def generate(*_args, **_kwargs):
        staging_dir = Path(connection.new_session.call_args.kwargs["cwd"])
        (staging_dir / "controller.js").write_text(
            "export default function App() { return null; }",
            encoding="utf-8",
        )
        (staging_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 2,
                    "id": "codex-widget",
                    "title": "Codex Widget",
                    "description": "",
                    "app_version": "0.1.0",
                    "intents": [],
                    "schema_refs": [],
                    "capabilities": [],
                }
            ),
            encoding="utf-8",
        )
        return PromptResponse(stop_reason="end_turn")

    connection.prompt = AsyncMock(side_effect=generate)
    spawn_calls = []

    @contextlib.asynccontextmanager
    async def fake_spawn(client, command, *args, **kwargs):
        spawn_calls.append((command, args, kwargs))
        client.on_connect(connection)
        yield connection, MagicMock(returncode=0)

    validations = 0

    def validate_contract(_result):
        nonlocal validations
        validations += 1
        if validations == 1:
            raise CodingAgentArtifactError(
                "Manifest differs from approved contract",
                code="runtime_contract_mismatch",
                stage="runtime_contract",
            )

    monkeypatch.setattr("backend.coding_agent_acp.spawn_agent_process", fake_spawn)

    result = await coding_agent_module.run_coding_agent(
        "codex-widget",
        "build",
        coding_agent="codex",
        runtime=runtime,
        model_config={"native_model": "gpt-test"},
        promote=False,
        artifact_validator=validate_contract,
    )

    assert isinstance(result, CodingAgentStagedResult)
    assert result.repair_attempts == 1
    assert connection.new_session.await_count == 1
    assert connection.prompt.await_count == 2
    assert {call.kwargs["session_id"] for call in connection.prompt.await_args_list} == {"codex-acp-session"}
    assert spawn_calls[0][0] == codex_acp
    assert spawn_calls[0][1] == ()
    assert spawn_calls[0][2]["env"]["CODEX_PATH"] == codex
    assert spawn_calls[0][2]["inherit_default_environment"] is False


def test_backend_image_pins_the_codex_acp_bridge():
    root = Path(__file__).parents[2]
    dockerfile = (root / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "AS coding-agent-acp" in dockerfile
    assert "@agentclientprotocol/codex-acp@1.1.7" in dockerfile
    assert "COPY --from=coding-agent-acp /opt/coding-agent-acp /opt/coding-agent-acp" in dockerfile
