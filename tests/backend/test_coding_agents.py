import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.main as main_module
from backend.coding_agent import CodingAgentConfigStore
from backend.coding_agent_runtime import CodingAgentRuntime
from backend.codex_service import _codex_environment, _codex_prompt, _event_update, run_codex_agent
from backend.opencode_service import OpenCodeArtifactError, OpenCodeStagedResult


@pytest.fixture(autouse=True)
def isolate_managed_coding_agent_runtime(tmp_path, monkeypatch):
    """Keep tests independent from a Dev Container's persistent CLI volume."""
    monkeypatch.setenv("CODING_AGENT_RUNTIME_DIR", str(tmp_path / "coding-agent-runtime"))


def test_coding_agent_settings_migrate_defaults_and_persist_model_bindings(tmp_path, monkeypatch):
    store = CodingAgentConfigStore(tmp_path / "workspace")
    monkeypatch.setenv("OPENCODE_COMMAND", "missing-opencode-test-binary")

    settings = store.get_settings()
    assert settings["default_agent"] == "opencode"
    assert settings["agent_models"]["opencode"] == {
        "mode": "shared_binding",
        "inherit": "ambient.primary",
        "provider_id": None,
        "model_id": None,
        "native_model": None,
    }
    assert settings["agent_models"]["codex"]["mode"] == "native"

    updated = store.update_settings({"default_agent": "codex"})
    assert updated["default_agent"] == "codex"
    assert CodingAgentConfigStore(tmp_path / "workspace").get_settings()["default_agent"] == "codex"
    assert (
        store.update_agent_model("codex", {"mode": "native", "native_model": "gpt-test"})["native_model"] == "gpt-test"
    )

    catalog = store.catalog()
    assert {item["id"] for item in catalog} == {"opencode", "codex"}
    codex = next(item for item in catalog if item["id"] == "codex")
    assert codex["installable"] is True
    assert codex["execution_target"] == "container"
    assert codex["model_capability"]["modes"] == ["native"]


def test_codex_environment_excludes_ambient_provider_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "global-provider-secret")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "global-provider-config")
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "native-codex-token")

    environment = _codex_environment()

    assert "OPENAI_API_KEY" not in environment
    assert "OPENCODE_CONFIG_CONTENT" not in environment
    assert environment["CODEX_ACCESS_TOKEN"] == "native-codex-token"

    runtime_environment = CodingAgentRuntime("workspace").process_environment("codex")
    assert "OPENAI_API_KEY" not in runtime_environment
    assert "OPENCODE_CONFIG_CONTENT" not in runtime_environment
    assert runtime_environment["CODEX_ACCESS_TOKEN"] == "native-codex-token"


@pytest.mark.asyncio
async def test_runtime_reports_latest_install_failure(tmp_path, monkeypatch):
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    async def fail_install(_operation_id):
        raise RuntimeError("installer unavailable")

    monkeypatch.setattr(runtime, "_install_codex", fail_install)
    operation = await runtime.start_install("codex")
    while runtime.operation("codex", operation["id"])["status"] == "installing":
        await asyncio.sleep(0.01)

    status = await runtime.status("codex")

    assert status["install_state"] == "failed"
    assert status["install_operation"]["error"] == "installer unavailable"


def test_coding_agent_api_lists_and_rejects_unready_selection(tmp_path, monkeypatch):
    store = CodingAgentConfigStore(tmp_path / "workspace")
    monkeypatch.setattr(main_module, "coding_agent_config_store", store)

    with TestClient(main_module.app) as client:
        listed = client.get("/api/coding-agents")
        unready = client.patch("/api/coding-agents/settings", json={"default_agent": "codex"})
        invalid = client.patch("/api/coding-agents/settings", json={"default_agent": "unknown"})

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["agents"]] == ["opencode", "codex"]
    assert unready.status_code == 422
    assert unready.json()["detail"]["code"] == "coding_agent_not_installed"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "coding_agent_not_found"


def test_coding_agent_models_api_returns_native_catalog_and_rejects_shared_catalog(tmp_path, monkeypatch):
    store = CodingAgentConfigStore(tmp_path / "workspace")
    monkeypatch.setattr(main_module, "coding_agent_config_store", store)

    with TestClient(main_module.app) as client:
        unsupported = client.get("/api/coding-agents/opencode/models")

        async def fake_models(agent_id):
            return {
                "agent_id": agent_id,
                "source": "agent",
                "default_model": "gpt-default",
                "models": [{"id": "gpt-default", "display_name": "GPT Default"}],
            }

        monkeypatch.setattr(store.runtime, "models", fake_models)
        discovered = client.get("/api/coding-agents/codex/models")

    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "model_catalog_unsupported"
    assert discovered.status_code == 200
    assert discovered.json()["default_model"] == "gpt-default"


def test_codex_event_projection_extracts_messages_and_progress():
    message, update = _event_update(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Widget complete"}}
    )
    assert message == "Widget complete"
    assert update == "Widget complete"
    assert _event_update(
        {"type": "item.started", "item": {"type": "command_execution", "command": "inspect files"}}
    ) == (None, "\n🛠️ Codex: inspect files")


def test_codex_prompt_explains_the_supported_app_scoped_data_path():
    prompt = _codex_prompt("weather-app", "show live weather", "en")

    assert "NEVER use `fetch`" in prompt
    assert "ambient.net.request" in prompt
    assert '"data_sources"' in prompt
    assert "manifest.json" in prompt
    assert "Do not silently replace requested live data with sample data" in prompt
    assert "HTM closing syntax is strict" in prompt
    assert "malformed `</${Row>`" in prompt


@pytest.mark.asyncio
async def test_codex_runner_uses_managed_container_runtime(tmp_path, monkeypatch):
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "sys.stdin.read()\n"
        "pathlib.Path('invocation.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "pathlib.Path('controller.js').write_text(\"export default function App() { return null; }\", encoding='utf-8')\n"
        "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    apps_dir = tmp_path / "apps"
    monkeypatch.setenv("CODEX_COMMAND", str(fake_codex))
    monkeypatch.setenv("APPS_DIR", str(apps_dir))
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    updates = []

    result = await run_codex_agent(
        "codex-widget",
        "build it",
        language="en",
        on_update=updates.append,
        promote=False,
        runtime=runtime,
        native_model="gpt-test",
    )

    assert isinstance(result, OpenCodeStagedResult)
    assert result.output == "done"
    invocation = json.loads((result.staging_dir / "invocation.json").read_text(encoding="utf-8"))
    assert invocation[0] == "exec"
    assert "--model" in invocation
    assert invocation[invocation.index("--model") + 1] == "gpt-test"
    assert updates[-1] == "done"


@pytest.mark.asyncio
async def test_codex_runner_repairs_sequential_validation_failures_in_place(tmp_path, monkeypatch):
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "prompt = sys.stdin.read()\n"
        "count_path = pathlib.Path('count.txt')\n"
        "count = int(count_path.read_text() or '0') + 1 if count_path.exists() else 1\n"
        "count_path.write_text(str(count), encoding='utf-8')\n"
        "pathlib.Path('prompt-' + str(count) + '.txt').write_text(prompt, encoding='utf-8')\n"
        "pathlib.Path('controller.js').write_text('export default function App() { return null; }', encoding='utf-8')\n"
        "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    apps_dir = tmp_path / "apps"
    monkeypatch.setenv("CODEX_COMMAND", str(fake_codex))
    monkeypatch.setenv("APPS_DIR", str(apps_dir))
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    validations = 0

    def validate_until_third_attempt(result):
        nonlocal validations
        validations += 1
        if validations == 1:
            raise OpenCodeArtifactError("App manifest validation failed: data source id must use kebab-case")
        if validations == 2:
            raise OpenCodeArtifactError('Unexpected token, expected "}" in controller.js')

    monkeypatch.setattr("backend.codex_service.validate_coding_agent_staging", validate_until_third_attempt)

    result = await run_codex_agent(
        "repair-widget",
        "build it",
        language="en",
        promote=False,
        runtime=runtime,
    )

    assert isinstance(result, OpenCodeStagedResult)
    assert (result.staging_dir / "count.txt").read_text(encoding="utf-8") == "3"
    repair_prompt = (result.staging_dir / "prompt-2.txt").read_text(encoding="utf-8")
    assert "failed mandatory validation" in repair_prompt
    assert "data source id must use kebab-case" in repair_prompt
    second_repair_prompt = (result.staging_dir / "prompt-3.txt").read_text(encoding="utf-8")
    assert 'Unexpected token, expected "}"' in second_repair_prompt


@pytest.mark.asyncio
async def test_runtime_install_and_device_auth_lifecycle(tmp_path, monkeypatch):
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    async def fake_install(operation_id):
        binary = runtime.managed_command("codex")
        binary.parent.mkdir(parents=True)
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib, sys, time\n"
            "args = sys.argv[1:]\n"
            "if args == ['--version']: print('codex-cli test')\n"
            "elif args == ['login', 'status']: raise SystemExit(1)\n"
            "elif args[:2] == ['login', '--device-auth']:\n"
            " print('https://auth.openai.com/codex/device', flush=True)\n"
            " print('ABCD-12345', flush=True)\n"
            " time.sleep(0.05)\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)

    monkeypatch.setattr(runtime, "_install_codex", fake_install)
    operation = await runtime.start_install("codex")
    while runtime.operation("codex", operation["id"])["status"] == "installing":
        await asyncio.sleep(0.01)
    assert runtime.operation("codex", operation["id"])["status"] == "installed"

    started = await runtime.start_auth("codex")
    assert started["status"] == "starting"
    for _ in range(50):
        if runtime.auth_session("codex")["status"] != "starting":
            break
        await asyncio.sleep(0.01)
    waiting = runtime.auth_session("codex")
    assert waiting["status"] == "waiting"
    assert waiting["user_code"] == "ABCD-12345"
    for _ in range(50):
        if runtime.auth_session("codex")["status"] == "signed_in":
            break
        await asyncio.sleep(0.01)
    assert runtime.auth_session("codex")["status"] == "signed_in"


@pytest.mark.asyncio
async def test_runtime_discovers_native_models_from_codex_app_server(tmp_path, monkeypatch):
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']: print('codex-cli test')\n"
        "elif args == ['login', 'status']: print('Logged in')\n"
        "elif args[:2] == ['app-server', '--stdio']:\n"
        " for line in sys.stdin:\n"
        "  request = json.loads(line)\n"
        "  if request['method'] == 'initialize':\n"
        "   print(json.dumps({'id': request['id'], 'result': {'userAgent': 'test'}}), flush=True)\n"
        "  elif request['method'] == 'model/list':\n"
        "   print(json.dumps({'id': request['id'], 'result': {'data': [\n"
        "    {'id': 'gpt-default', 'model': 'gpt-default', 'displayName': 'GPT Default', 'description': 'Default model', 'hidden': False, 'isDefault': True, 'defaultReasoningEffort': 'medium', 'supportedReasoningEfforts': [{'reasoningEffort': 'low', 'description': 'Fast'}]},\n"
        "    {'id': 'gpt-fast', 'model': 'gpt-fast', 'displayName': 'GPT Fast', 'description': 'Fast model', 'hidden': False, 'isDefault': False, 'defaultReasoningEffort': 'low', 'supportedReasoningEfforts': []}\n"
        "   ], 'nextCursor': None}}), flush=True)\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("CODEX_COMMAND", str(fake_codex))
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    catalog = await runtime.models("codex")

    assert catalog["agent_id"] == "codex"
    assert catalog["default_model"] == "gpt-default"
    assert [model["id"] for model in catalog["models"]] == ["gpt-default", "gpt-fast"]
    assert catalog["models"][0]["supported_reasoning_efforts"] == ["low"]


def test_docker_allows_codex_user_namespace_without_sys_admin():
    root = Path(__file__).parents[2]
    for compose_path in (root / "docker-compose.yml", root / ".devcontainer" / "docker-compose.yml"):
        contents = compose_path.read_text(encoding="utf-8")
        assert "seccomp=unconfined" in contents
        assert "SYS_ADMIN" not in contents


def test_backend_image_contains_the_complete_widget_verifier_runtime():
    root = Path(__file__).parents[2]
    dockerfile = (root / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "AS widget-verifier" in dockerfile
    assert "COPY frontend/package.json frontend/package-lock.json" in dockerfile
    assert "node_modules/@babel/standalone" in dockerfile
    assert "COPY --from=widget-verifier /usr/local/bin/node /usr/local/bin/node" in dockerfile


def test_run_snapshot_freezes_ambient_and_coding_model_bindings(tmp_path, monkeypatch):
    storage, _ = _configure_model(tmp_path, monkeypatch)
    coding_store = CodingAgentConfigStore(tmp_path / "workspace")
    coding_store.update_settings({"default_agent": "opencode"})
    coding_store.update_agent_model(
        "opencode",
        {"mode": "shared_binding", "provider_id": "local", "model_id": "test-model"},
    )
    monkeypatch.setattr(main_module, "coding_agent_config_store", coding_store)
    chat = storage.get(main_module.ChatSession, "snapshot-session")

    snapshot = main_module._snapshot_model_config(chat)

    assert snapshot["coding_agent"] == "opencode"
    assert snapshot["primary"] == {"provider_id": "local", "model_id": "test-model"}
    assert snapshot["coding_model"] == {"provider_id": "local", "model_id": "test-model"}
    assert snapshot["coding_agent_config"]["mode"] == "shared_binding"


def _configure_model(tmp_path, monkeypatch):
    from backend.llm_config import LLMConfigStore
    from backend.models import ChatSession
    from backend.workspace_storage import WorkspaceStorage

    storage = WorkspaceStorage(str(tmp_path / "workspace"))
    llm_store = LLMConfigStore(storage.workspace_dir)
    llm_store.create_provider(
        {"id": "local", "name": "Local", "preset": "ollama", "models": [{"id": "test-model"}]},
        {},
    )
    llm_store.update_settings({"default_model": {"provider_id": "local", "model_id": "test-model"}})
    storage.add(ChatSession(id="snapshot-session", title="Snapshot"))
    storage.commit()
    monkeypatch.setattr(main_module, "llm_config_store", llm_store)
    return storage, llm_store
