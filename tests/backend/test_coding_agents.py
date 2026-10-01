import asyncio
import hashlib
import io
import sys
import tarfile
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import backend.main as main_module
import backend.coding_agent_runtime as coding_agent_runtime_module
from backend.coding_agent import CodingAgentConfigStore
from backend.coding_agent_runtime import CodingAgentRuntime, CodingAgentRuntimeError
from backend.codex_service import _codex_environment, _codex_prompt, run_codex_agent


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


def test_coding_runtime_preserves_required_windows_os_environment_without_provider_secrets(tmp_path, monkeypatch):
    required = {
        "SYSTEMROOT": "C:/Windows",
        "SYSTEMDRIVE": "C:",
        "COMSPEC": "C:/Windows/System32/cmd.exe",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "USERPROFILE": "C:/Users/Audit",
        "WINDIR": "C:/Windows",
    }
    for key, value in required.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("AMBIENT_TEST_SECRET", "must-not-inherit-either")
    environment = CodingAgentRuntime(tmp_path / "workspace").process_environment("codex")
    assert {key: environment.get(key) for key in required} == required
    assert "OPENAI_API_KEY" not in environment
    assert "AMBIENT_TEST_SECRET" not in environment


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


@pytest_asyncio.fixture
async def real_probe_children(monkeypatch):
    children = []
    started = asyncio.Event()
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        child = await spawn(*args, **kwargs)
        children.append(child)
        started.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    yield children, started
    for child in children:
        if child.returncode is None:
            child.kill()
        child._transport.close()
        await asyncio.wait_for(child.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_runtime_probe_timeout_reaps_child_and_closes_stdio(tmp_path, real_probe_children):
    children, _ = real_probe_children
    result = await CodingAgentRuntime(tmp_path)._run_probe(
        [sys.executable, "-c", "import time; time.sleep(30)"], agent_id="codex"
    )
    assert result == (1, "")
    assert children[0].returncode is not None
    assert children[0]._transport.is_closing()


@pytest.mark.asyncio
async def test_runtime_probe_cancel_reaps_child_and_preserves_cancellation(tmp_path, real_probe_children):
    children, started = real_probe_children
    task = asyncio.create_task(
        CodingAgentRuntime(tmp_path)._run_probe([sys.executable, "-c", "import time; time.sleep(30)"], agent_id="codex")
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None
    assert children[0]._transport.is_closing()


@pytest.mark.asyncio
async def test_runtime_probe_success_preserves_output_and_closes_stdio(tmp_path, real_probe_children):
    children, _ = real_probe_children
    result = await CodingAgentRuntime(tmp_path)._run_probe(
        [sys.executable, "-c", "print('codex-cli test')"], agent_id="codex"
    )
    assert result == (0, "codex-cli test")
    assert children[0].returncode == 0
    assert children[0]._transport.is_closing()


@pytest.mark.asyncio
@pytest.mark.parametrize("binary_size", [None, 310730800], ids=["small-fixture", "official-expanded-size"])
async def test_managed_codex_install_uses_a_pinned_verified_release(tmp_path, monkeypatch, binary_size):
    target = "x86_64-unknown-linux-musl"
    binary_payload = b"#!/bin/sh\necho 'codex-cli 0.145.0'\n"
    binary_size = binary_size or len(binary_payload)

    class BinarySource:
        prefix = binary_payload
        remaining = binary_size

        def read(self, count):
            size = min(count, self.remaining)
            prefix = self.prefix[:size]
            self.prefix = self.prefix[size:]
            self.remaining -= size
            return prefix + b"\0" * (size - len(prefix))

    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(f"codex-{target}")
        member.size = binary_size
        member.mode = 0o755
        archive.addfile(member, BinarySource())
    archive_payload = archive_buffer.getvalue()

    class FakeResponse:
        headers = {"content-length": str(len(archive_payload))}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield archive_payload

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url):
            assert method == "GET"
            assert url == (
                "https://github.com/openai/codex/releases/download/rust-v0.145.0/codex-x86_64-unknown-linux-musl.tar.gz"
            )
            return FakeStream()

    monkeypatch.setattr(coding_agent_runtime_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(coding_agent_runtime_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        coding_agent_runtime_module,
        "_CODEX_RELEASES",
        {("linux", "x86_64"): (target, hashlib.sha256(archive_payload).hexdigest())},
    )
    monkeypatch.setattr(coding_agent_runtime_module, "_CODEX_BINARY_SIZES", {target: binary_size}, raising=False)
    monkeypatch.setattr(coding_agent_runtime_module.httpx, "AsyncClient", FakeClient)
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    monkeypatch.setattr(runtime, "managed_command", lambda _agent_id: runtime.agent_root("codex") / "bin" / "codex")
    verified_paths = []

    async def probe_verified_binary(argv, *, agent_id):
        assert agent_id == "codex"
        assert argv[1:] == ["--version"]
        assert Path(argv[0]).stat().st_size == binary_size
        with Path(argv[0]).open("rb") as source:
            assert source.read(len(binary_payload)) == binary_payload
        verified_paths.append(Path(argv[0]))
        return 0, "codex-cli 0.145.0"

    chmod_calls = {}
    original_chmod = Path.chmod

    def record_chmod(path, mode, **kwargs):
        chmod_calls[path] = mode
        return original_chmod(path, mode, **kwargs)

    monkeypatch.setattr(runtime, "_run_probe", probe_verified_binary)
    monkeypatch.setattr(Path, "chmod", record_chmod)

    await runtime._install_codex("verified-release")

    binary = runtime.managed_command("codex")
    assert binary.stat().st_size == binary_size
    with binary.open("rb") as source:
        assert source.read(len(binary_payload)) == binary_payload
    assert len(verified_paths) == 1
    assert chmod_calls[verified_paths[0]] == 0o700


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["size-minus-one", "size-plus-one", "oversize", "path", "symlink", "hardlink", "version"]
)
async def test_managed_codex_install_rejects_invalid_binary_and_cleans_staging(tmp_path, monkeypatch, invalid):
    target = "x86_64-unknown-linux-musl"
    prefix = b"test-only-pinned-binary"
    expected_size = len(prefix)
    sizes = {"size-minus-one": expected_size - 1, "size-plus-one": expected_size + 1, "oversize": 160 * 1024 * 1024 + 1}
    member_size = sizes.get(invalid, expected_size)
    archive_buffer = io.BytesIO()

    class BinarySource:
        remaining = member_size
        pending = prefix

        def read(self, count):
            size = min(count, self.remaining)
            data = self.pending[:size]
            self.pending = self.pending[size:]
            self.remaining -= size
            return data + b"\0" * (size - len(data))

    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo(("../" if invalid == "path" else "") + f"codex-{target}")
        member.size = member_size
        if invalid in {"symlink", "hardlink"}:
            member.type = tarfile.SYMTYPE if invalid == "symlink" else tarfile.LNKTYPE
            member.linkname = "../outside"
            member.size = 0
        archive.addfile(member, BinarySource() if member.isfile() else None)
    payload = archive_buffer.getvalue()
    actual_client = coding_agent_runtime_module.httpx.AsyncClient

    def respond(request):
        assert request.method == "GET"
        assert str(request.url).endswith(f"/rust-v0.145.0/codex-{target}.tar.gz")
        return coding_agent_runtime_module.httpx.Response(200, content=payload)

    def client_factory(**kwargs):
        return actual_client(transport=coding_agent_runtime_module.httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(coding_agent_runtime_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(coding_agent_runtime_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        coding_agent_runtime_module,
        "_CODEX_RELEASES",
        {("linux", "x86_64"): (target, hashlib.sha256(payload).hexdigest())},
    )
    monkeypatch.setattr(coding_agent_runtime_module, "_CODEX_BINARY_SIZES", {target: expected_size}, raising=False)
    monkeypatch.setattr(coding_agent_runtime_module.httpx, "AsyncClient", client_factory)
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    probes = []

    async def probe(argv, *, agent_id):
        probes.append(argv)
        return 0, "codex-cli 0.144.0" if invalid == "version" else "codex-cli 0.145.0"

    monkeypatch.setattr(runtime, "_run_probe", probe)
    with pytest.raises(CodingAgentRuntimeError):
        await runtime._install_codex("invalid-binary")
    assert len(probes) == (1 if invalid == "version" else 0)
    assert not (runtime.agent_root("codex") / "bin").exists()
    assert not (runtime.agent_root("codex") / ".install-invalid-binary").exists()


@pytest.mark.asyncio
async def test_managed_codex_install_rejects_a_release_checksum_mismatch(tmp_path, monkeypatch):
    archive_payload = b"not-the-pinned-release"

    class FakeResponse:
        headers = {"content-length": str(len(archive_payload))}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield archive_payload

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args):
            return None

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, _url):
            return FakeStream()

    monkeypatch.setattr(coding_agent_runtime_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(coding_agent_runtime_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        coding_agent_runtime_module,
        "_CODEX_RELEASES",
        {("linux", "x86_64"): ("x86_64-unknown-linux-musl", "0" * 64)},
    )
    monkeypatch.setattr(coding_agent_runtime_module.httpx, "AsyncClient", FakeClient)
    runtime = CodingAgentRuntime(tmp_path / "workspace")

    with pytest.raises(Exception, match="checksum verification failed"):
        await runtime._install_codex("tampered-release")

    assert not runtime.managed_command("codex").exists()


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


def test_codex_prompt_explains_the_supported_app_scoped_data_path():
    prompt = _codex_prompt("weather-app", "show live weather", "en")

    assert "Never use `fetch`" in prompt
    assert "ambient.net.request" in prompt
    assert "network.request" in prompt
    assert "Manifest V2" in prompt
    assert "manifest.json" in prompt
    assert "Do not replace requested live behavior with fake/sample data" in prompt
    assert "Close dynamic HTM components with `<//>`" in prompt
    assert "ambient.mcp" in prompt
    assert "ambient.presentation.getSnapshot()" in prompt
    assert "ambient.presentation.subscribe" in prompt
    assert "approval envelope, not the `manifest.json` document" in prompt
    for forbidden_manifest_field in (
        "contract_version",
        "catalog_version",
        "app_id",
        "schemas",
        "grants_digest",
        "allowed_files",
    ):
        assert forbidden_manifest_field in prompt


@pytest.mark.asyncio
async def test_legacy_codex_entrypoint_delegates_to_the_unified_acp_runner(tmp_path, monkeypatch):
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    calls = []

    async def fake_unified_runner(app_id, instruction, **kwargs):
        calls.append((app_id, instruction, kwargs))
        return "acp-result"

    monkeypatch.setattr("backend.coding_agent.run_coding_agent", fake_unified_runner)

    result = await run_codex_agent(
        "codex-widget",
        "build it",
        language="en",
        promote=False,
        runtime=runtime,
        native_model="gpt-test",
    )

    assert result == "acp-result"
    assert calls == [
        (
            "codex-widget",
            "build it",
            {
                "language": "en",
                "on_update": None,
                "promote": False,
                "coding_agent": "codex",
                "runtime": runtime,
                "model_config": {"mode": "native", "native_model": "gpt-test"},
                "staged_result": None,
                "artifact_validator": None,
                "repair_decider": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_runtime_install_and_device_auth_lifecycle(tmp_path, monkeypatch):
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    monkeypatch.setattr(
        runtime,
        "command",
        lambda _agent_id: (
            [sys.executable, str(runtime.managed_command("codex"))]
            if runtime.managed_command("codex").is_file()
            else None
        ),
    )

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
        " initialized = False\n"
        " for line in sys.stdin:\n"
        "  request = json.loads(line)\n"
        "  if request['method'] == 'initialize':\n"
        "   print(json.dumps({'id': request['id'], 'result': {'userAgent': 'test'}}), flush=True)\n"
        "  elif request['method'] == 'initialized':\n"
        "   initialized = True\n"
        "  elif not initialized:\n"
        "   print(json.dumps({'id': request['id'], 'error': {'code': -32000, 'message': 'Not initialized'}}), flush=True)\n"
        "  elif request['method'] == 'model/list':\n"
        "   print(json.dumps({'id': request['id'], 'result': {'data': [\n"
        "    {'id': 'gpt-default', 'model': 'gpt-default', 'displayName': 'GPT Default', 'description': 'Default model', 'hidden': False, 'isDefault': True, 'defaultReasoningEffort': 'medium', 'supportedReasoningEfforts': [{'reasoningEffort': 'low', 'description': 'Fast'}]},\n"
        "    {'id': 'gpt-fast', 'model': 'gpt-fast', 'displayName': 'GPT Fast', 'description': 'Fast model', 'hidden': False, 'isDefault': False, 'defaultReasoningEffort': 'low', 'supportedReasoningEfforts': []}\n"
        "   ], 'nextCursor': None}}), flush=True)\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    monkeypatch.setattr(runtime, "command", lambda _agent_id: [sys.executable, str(fake_codex)])

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
