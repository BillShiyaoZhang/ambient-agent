import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse

import backend.coding_agent as coding_agent_module
import backend.main as main_module
from backend.coding_agent_runtime import CodingAgentRuntime, CodingAgentRuntimeError
from backend.coding_agent_acp import (
    CodingAgentACPInputError,
    CodingAgentArtifactError,
    CodingAgentStagedResult,
    _structured_verifier_error,
    run_coding_agent_acp,
)


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


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf"])
def test_runtime_rejects_non_finite_acp_timeout(tmp_path, monkeypatch, timeout):
    codex = _executable(tmp_path / "codex")
    bridge = _executable(tmp_path / "codex-acp")
    monkeypatch.setenv("CODEX_COMMAND", codex)
    monkeypatch.setenv("CODEX_ACP_COMMAND", bridge)
    monkeypatch.setenv("CODEX_TIMEOUT", timeout)
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    with pytest.raises(CodingAgentRuntimeError, match="finite and positive"):
        runtime.acp_launch("codex")


@pytest.mark.asyncio
async def test_acp_runner_rejects_non_finite_timeout_before_staging(tmp_path, monkeypatch):
    monkeypatch.setenv("APPS_DIR", str(tmp_path / "apps"))
    launch = SimpleNamespace(
        agent_id="codex", agent_name="Codex", argv=("/unused",), environment={}, timeout_seconds=float("nan")
    )

    with pytest.raises(CodingAgentACPInputError, match="timeout must be finite and positive"):
        await run_coding_agent_acp("finite-timeout", "build", launch=launch)

    assert not (tmp_path / "apps" / "finite-timeout").exists()


def test_verifier_report_parser_handles_brace_heavy_diagnostics():
    report = '{"ok":false,"code":"required_feature_missing","message":"missing","hint":"repair"}'

    parsed = _structured_verifier_error(["{" * 100_000 + report + "}" * 100_000])

    assert parsed == {"ok": False, "code": "required_feature_missing", "message": "missing", "hint": "repair"}


@pytest.mark.asyncio
async def test_runtime_does_not_report_agent_available_when_its_acp_bridge_is_missing(tmp_path, monkeypatch):
    codex = _executable(tmp_path / "codex")
    monkeypatch.setenv("CODEX_COMMAND", codex)
    monkeypatch.setenv("CODEX_ACP_COMMAND", str(tmp_path / "missing-codex-acp"))
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    async def probe(argv, *, agent_id):
        assert agent_id == "codex"
        assert argv in ([codex, "--version"], [codex, "login", "status"])
        return 0, "codex-cli test" if argv[-1] == "--version" else "Logged in"

    monkeypatch.setattr(runtime, "_run_probe", probe)

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
async def test_direct_opencode_runner_rejects_native_model_before_acp_launch(tmp_path, monkeypatch):
    from backend.llm_config import LLMConfigError, LLMConfigStore, ModelSelection
    from backend.llm_runtime import use_model_selections

    store = LLMConfigStore(str(tmp_path))
    store.create_provider(
        {"id": "native", "name": "Native", "preset": "codex_native", "models": [{"id": "gpt-5.6-luna"}]}, {}
    )
    monkeypatch.setattr("backend.llm_service.get_default_llm_store", lambda: store)
    runtime = CodingAgentRuntime(tmp_path)
    launch = MagicMock(return_value=SimpleNamespace(agent_id="opencode"))
    invoke = AsyncMock(return_value=None)
    monkeypatch.setattr(runtime, "acp_launch", launch)
    monkeypatch.setattr(coding_agent_module, "run_coding_agent_acp", invoke)
    selection = ModelSelection(provider_id="native", model_id="gpt-5.6-luna")
    with use_model_selections(selection), pytest.raises(LLMConfigError) as failure:
        await coding_agent_module.run_coding_agent("native-test", "build", coding_agent="opencode", runtime=runtime)
    assert failure.value.code == "coding_agent_model_binding_unsupported"
    launch.assert_not_called()
    invoke.assert_not_called()


@pytest.mark.asyncio
async def test_main_composition_root_has_no_opencode_execution_bypass(monkeypatch):
    calls = []
    approved_template = {
        "manifest_version": 2,
        "id": "weather-app",
        "title": "Weather",
        "description": "",
        "app_version": "0.1.0",
        "intents": [],
        "schema_refs": [],
        "capabilities": [],
    }

    async def fake_runner(app_id, instruction, **kwargs):
        calls.append((app_id, instruction, kwargs))
        return "staged"

    monkeypatch.setattr(main_module, "run_coding_agent", fake_runner)

    result = await main_module._run_coding_agent_staged(
        "weather-app",
        "build",
        coding_agent="opencode",
        coding_agent_model={"mode": "shared_binding", "inherit": "ambient.primary"},
        manifest_template=approved_template,
    )

    assert result == "staged"
    assert len(calls) == 1
    assert calls[0][2]["coding_agent"] == "opencode"
    assert calls[0][2]["manifest_template"] is approved_template


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
    assert "coding-agent-acp/package-lock.json" in dockerfile
    assert "npm ci --omit=dev --ignore-scripts" in dockerfile
    assert "COPY --from=coding-agent-acp /opt/coding-agent-acp /opt/coding-agent-acp" in dockerfile
