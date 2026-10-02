from unittest.mock import AsyncMock

import pytest

import backend.coding_agent as coding_agent_module
import backend.coding_agent_runtime as runtime_module
from backend.coding_agent_runtime import CodingAgentRuntime, CodingAgentRuntimeError


def managed_runtime(tmp_path, monkeypatch, helper_source):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    monkeypatch.setenv("CODING_AGENT_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("APPS_DIR", str(tmp_path / "apps"))
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    command = runtime._managed_coding_command()
    command.parent.mkdir(parents=True)
    command.write_text("coding-cli", encoding="utf-8")
    host = runtime._managed_code_mode_host()
    host.write_text(helper_source, encoding="utf-8")
    host.chmod(0o700)
    monkeypatch.setattr(
        runtime_module,
        "_CODEX_CODE_MODE_HOST_BINARY_SIZES",
        {key: host.stat().st_size for key in runtime_module._CODEX_CODE_MODE_HOST_BINARY_SIZES},
    )
    monkeypatch.setattr(runtime, "_bridge_command", lambda _spec: ["unused-bridge"])
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["#!/bin/sh\nexit 1\n", "#!/bin/sh\nprintf 'incompatible helper'\n"])
async def test_unusable_managed_helper_stops_before_model_or_staging(tmp_path, monkeypatch, source):
    runtime = managed_runtime(tmp_path, monkeypatch, source)
    assert runtime._code_mode_host_file_ready(), "The existence/size/permission check alone misses this fault"
    invoke = AsyncMock(return_value="should not call the model")
    monkeypatch.setattr(coding_agent_module, "run_coding_agent_acp", invoke)

    with pytest.raises(CodingAgentRuntimeError) as captured:
        await coding_agent_module.run_coding_agent(
            "weather-app", "Create the App", coding_agent="codex", runtime=runtime
        )

    assert captured.value.code == "coding_agent_code_mode_unavailable"
    invoke.assert_not_awaited()
    assert not (tmp_path / "apps").exists()


@pytest.mark.asyncio
async def test_healthy_managed_helper_reaches_single_acp_runner(tmp_path, monkeypatch):
    runtime = managed_runtime(
        tmp_path,
        monkeypatch,
        "#!/bin/sh\nprintf 'Usage: codex-code-mode-host [OPTIONS]\\n--listen <URL>\\n'\n",
    )
    invoke = AsyncMock(return_value="validated staged result")
    monkeypatch.setattr(coding_agent_module, "run_coding_agent_acp", invoke)

    result = await coding_agent_module.run_coding_agent(
        "weather-app", "Create the App", coding_agent="codex", runtime=runtime
    )

    assert result == "validated staged result"
    invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", ["opencode", "codex"])
async def test_explicit_or_native_agent_does_not_probe_managed_codex_helper(tmp_path, monkeypatch, agent_id):
    runtime = CodingAgentRuntime(tmp_path)
    monkeypatch.setattr(runtime, "coding_command", lambda _agent: [str(tmp_path / "explicit-command")])
    probe = AsyncMock(return_value=False)
    monkeypatch.setattr(runtime, "_code_mode_host_ready", probe)

    await runtime.ensure_coding_ready(agent_id)

    probe.assert_not_awaited()
