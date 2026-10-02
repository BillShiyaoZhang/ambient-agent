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


def test_coding_cli_upgrade_preserves_primary_command_and_managed_login(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    runtime = CodingAgentRuntime(tmp_path)
    original = runtime.managed_command("codex")
    original.parent.mkdir(parents=True)
    original.write_bytes(b"pinned-primary")
    upgraded = runtime.agent_root("codex") / "coding" / "0.159.3" / "bin" / original.name
    upgraded.parent.mkdir(parents=True)
    upgraded.write_bytes(b"coding-cli")

    assert runtime.command("codex") == [str(original)]
    assert runtime.coding_command("codex") == [str(upgraded)]
    assert runtime.process_environment("codex")["CODEX_HOME"] == str(runtime.state_dir("codex"))

    monkeypatch.setenv("CODEX_COMMAND", sys.executable)
    assert runtime.coding_command("codex") == runtime.command("codex") == [sys.executable]


def _installed_codex_runtime(tmp_path, monkeypatch, *, upgraded=True):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    runtime = CodingAgentRuntime(tmp_path)
    primary = runtime.managed_command("codex")
    primary.parent.mkdir(parents=True)
    primary.write_bytes(b"pinned-primary")
    if upgraded:
        coding = runtime._managed_coding_command()
        coding.parent.mkdir(parents=True)
        coding.write_bytes(b"pinned-coding")
    return runtime


@pytest.mark.skipif(sys.platform == "win32", reason="Managed Codex installations require Linux or macOS")
def test_native_inference_uses_upgraded_cli_in_an_independent_home_with_shared_login(tmp_path, monkeypatch):
    runtime = _installed_codex_runtime(tmp_path, monkeypatch)
    login_home = runtime.state_dir("codex")
    login_home.mkdir()
    auth = login_home / "auth.json"
    auth.write_bytes(b"opaque-test-login")
    login_cache = login_home / "models_cache.json"
    login_cache.write_bytes(b"old-public-catalog")
    coding_home = Path(runtime.coding_environment("codex")["CODEX_HOME"])
    (coding_home / "config.toml").write_text("coding-only = true")
    coding_cache = coding_home / "models_cache.json"
    coding_cache.write_bytes(b"coding-public-catalog")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")

    environment = runtime.inference_environment("codex")
    inference_home = runtime.inference_state_dir("codex")

    assert runtime.inference_command("codex") == [str(runtime._managed_coding_command())]
    assert runtime.command("codex") == [str(runtime.managed_command("codex"))]
    assert inference_home == runtime.agent_root("codex") / "inference" / "0.159.3" / "state"
    assert environment["CODEX_HOME"] == environment["HOME"] == str(inference_home)
    assert "OPENAI_API_KEY" not in environment
    assert inference_home.stat().st_mode & 0o777 == 0o700
    assert (inference_home / "auth.json").is_symlink()
    assert (inference_home / "auth.json").readlink() == auth.absolute()
    assert not (inference_home / "config.toml").exists()
    assert not (inference_home / "models_cache.json").exists()
    (inference_home / "models_cache.json").write_bytes(b"inference-public-catalog")
    assert login_cache.read_bytes() == b"old-public-catalog"
    assert coding_cache.read_bytes() == b"coding-public-catalog"
    assert auth.read_bytes() == b"opaque-test-login"

    auth.unlink()
    assert not (inference_home / "auth.json").exists()
    assert runtime.inference_environment("codex")["CODEX_HOME"] == str(inference_home)
    assert not auth.exists()


def test_native_inference_preserves_legacy_runtime_until_managed_upgrade(tmp_path, monkeypatch):
    runtime = _installed_codex_runtime(tmp_path, monkeypatch, upgraded=False)
    assert runtime.inference_command("codex") == runtime.command("codex")
    assert runtime.inference_state_dir("codex") == runtime.state_dir("codex")
    assert runtime.inference_environment("codex") == runtime.process_environment("codex")
    assert not (runtime.agent_root("codex") / "inference").exists()


def test_native_inference_resolves_managed_launch_paths_before_changing_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODING_AGENT_RUNTIME_DIR", "relative-runtime")
    runtime = _installed_codex_runtime(tmp_path, monkeypatch)

    assert runtime.inference_command("codex") == [str(runtime._managed_coding_command().absolute())]
    home = runtime.inference_state_dir("codex")
    assert home == (runtime.agent_root("codex") / "inference" / "0.159.3" / "state").absolute()
    environment = runtime.inference_environment("codex")
    assert environment["CODEX_HOME"] == environment["HOME"] == str(home)
    assert (home / "auth.json").readlink() == (runtime.state_dir("codex") / "auth.json").absolute()


def test_native_inference_respects_explicit_command_and_login_home(tmp_path, monkeypatch):
    runtime = _installed_codex_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_COMMAND", sys.executable)
    assert runtime.inference_command("codex") == [sys.executable]
    assert runtime.inference_state_dir("codex") == runtime.state_dir("codex")
    assert runtime.inference_environment("codex") == runtime.process_environment("codex")
    assert not (runtime.agent_root("codex") / "inference").exists()


@pytest.mark.parametrize("invalid_auth", ["file", "wrong_link"])
def test_native_inference_rejects_independent_or_untrusted_login(tmp_path, monkeypatch, invalid_auth):
    runtime = _installed_codex_runtime(tmp_path, monkeypatch)
    inference_home = runtime.agent_root("codex") / "inference" / "0.159.3" / "state"
    inference_home.mkdir(parents=True)
    alias = inference_home / "auth.json"
    if invalid_auth == "file":
        alias.write_bytes(b"untrusted-other-account")
    else:
        alias.symlink_to(tmp_path / "untrusted-login")
    with pytest.raises(CodingAgentRuntimeError) as error:
        runtime.inference_environment("codex")
    assert error.value.code == "coding_agent_auth_invalid"


@pytest.mark.parametrize("directory", ["inference", "version", "state"])
@pytest.mark.parametrize("invalid_path", ["symlink", "file"])
def test_native_inference_rejects_untrusted_home_ancestry(tmp_path, monkeypatch, directory, invalid_path):
    runtime = _installed_codex_runtime(tmp_path, monkeypatch)
    inference_root = runtime.agent_root("codex") / "inference"
    path = {
        "inference": inference_root,
        "version": inference_root / "0.159.3",
        "state": inference_root / "0.159.3" / "state",
    }[directory]
    path.parent.mkdir(parents=True, exist_ok=True)
    external_home = tmp_path / "external-home"
    external_home.mkdir()
    if invalid_path == "symlink":
        path.symlink_to(external_home, target_is_directory=True)
    else:
        path.write_bytes(b"must-not-replace")

    with pytest.raises(CodingAgentRuntimeError) as error:
        runtime.inference_environment("codex")
    assert error.value.code == "coding_agent_auth_invalid"
    assert list(external_home.iterdir()) == []
    if invalid_path == "file":
        assert path.read_bytes() == b"must-not-replace"


def test_upgraded_coding_home_rejects_an_independent_authentication_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    runtime = CodingAgentRuntime(tmp_path)
    original = runtime.managed_command("codex")
    original.parent.mkdir(parents=True)
    original.write_bytes(b"pinned-primary")
    upgraded = runtime.agent_root("codex") / "coding" / "0.159.3" / "bin" / original.name
    upgraded.parent.mkdir(parents=True)
    upgraded.write_bytes(b"coding-cli")
    coding_home = runtime.agent_root("codex") / "coding" / "0.159.3" / "state"
    coding_home.mkdir()
    (coding_home / "auth.json").write_bytes(b"untrusted-other-account")
    with pytest.raises(CodingAgentRuntimeError) as error:
        runtime.coding_environment("codex")
    assert error.value.code == "coding_agent_auth_invalid"


@pytest.mark.skipif(sys.platform == "win32", reason="Managed Codex installations require Linux or macOS")
def test_upgraded_coding_home_isolates_cache_but_shares_cli_owned_login(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    runtime = CodingAgentRuntime(tmp_path)
    original = runtime.managed_command("codex")
    original.parent.mkdir(parents=True)
    original.write_bytes(b"pinned-primary")
    upgraded = runtime.agent_root("codex") / "coding" / "0.159.3" / "bin" / original.name
    upgraded.parent.mkdir(parents=True)
    upgraded.write_bytes(b"coding-cli")
    native_home = runtime.state_dir("codex")
    native_home.mkdir()
    source_auth = native_home / "auth.json"
    source_auth.write_bytes(b"opaque-test-login")
    (native_home / "models_cache.json").write_bytes(b"original-public-catalog")
    environment = runtime.coding_environment("codex")
    coding_home = Path(environment["CODEX_HOME"])
    assert coding_home != native_home
    assert environment["HOME"] == str(coding_home)
    assert (coding_home / "auth.json").is_symlink()
    assert (coding_home / "auth.json").resolve() == source_auth.resolve()
    (coding_home / "models_cache.json").write_bytes(b"new-public-catalog")
    assert (native_home / "models_cache.json").read_bytes() == b"original-public-catalog"
    assert source_auth.read_bytes() == b"opaque-test-login"
    source_auth.unlink()
    assert not (coding_home / "auth.json").exists()
    assert runtime.coding_environment("codex")["CODEX_HOME"] == str(coding_home)
    (coding_home / "auth.json").unlink()
    (coding_home / "auth.json").symlink_to(tmp_path / "another-login")
    with pytest.raises(CodingAgentRuntimeError) as error:
        runtime.coding_environment("codex")
    assert error.value.code == "coding_agent_auth_invalid"


@pytest.mark.asyncio
async def test_logout_keeps_original_command_and_login_home_after_coding_upgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    runtime = CodingAgentRuntime(tmp_path)
    original = runtime.managed_command("codex")
    original.parent.mkdir(parents=True)
    original.write_bytes(b"pinned-primary")
    upgraded = runtime._managed_coding_command()
    upgraded.parent.mkdir(parents=True)
    upgraded.write_bytes(b"coding-cli")
    calls = []

    async def probe(argv, *, agent_id):
        calls.append(argv)
        assert runtime.process_environment(agent_id)["CODEX_HOME"] == str(runtime.state_dir("codex"))
        return 0, "Logged out"

    monkeypatch.setattr(runtime, "_run_probe", probe)
    session = await runtime.logout("codex")
    assert calls == [[str(original), "logout"]]
    assert session["status"] == "signed_out"


@pytest.mark.asyncio
async def test_coding_upgrade_status_keeps_existing_install_ready_and_reports_target(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    runtime = CodingAgentRuntime(tmp_path)
    original = runtime.managed_command("codex")
    original.parent.mkdir(parents=True)
    original.write_bytes(b"pinned-primary")
    monkeypatch.setattr(runtime, "_bridge_command", lambda _spec: ["bridge"])

    async def probe(argv, *, agent_id):
        if "--version" in argv:
            version = "0.159.3" if Path(argv[0]) == runtime._managed_coding_command() else "0.145.0"
            return 0, f"codex-cli {version}"
        assert Path(argv[0]) == original, "Login lifecycle remains owned by the original managed CLI"
        return 0, "Logged in"

    monkeypatch.setattr(runtime, "_run_probe", probe)
    before = await runtime.status("codex")
    assert before["installed"] and before["authenticated"] and before["available"]
    assert before["update_available"] is True
    assert before["target_version"] == "0.159.3"
    upgraded = runtime._managed_coding_command()
    upgraded.parent.mkdir(parents=True)
    upgraded.write_bytes(b"coding-cli")
    after = await runtime.status("codex")
    assert after["version"] == "codex-cli 0.159.3"
    assert after["update_available"] is False
    assert after["authenticated"] is True
    assert (await runtime.start_install("codex"))["status"] == "installed"
    assert runtime.command("codex") == [str(original)]


@pytest.mark.asyncio
async def test_existing_codex_can_upgrade_once_and_retry_without_replacing_primary(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    runtime = CodingAgentRuntime(tmp_path)
    original = runtime.managed_command("codex")
    original.parent.mkdir(parents=True)
    original.write_bytes(b"pinned-primary")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def install_primary(_operation_id):
        pytest.fail("An upgrade must not reinstall primary inference")

    async def upgrade(operation_id):
        calls.append(operation_id)
        entered.set()
        await release.wait()
        raise RuntimeError("verified download failed")

    monkeypatch.setattr(runtime, "_install_codex", install_primary)
    monkeypatch.setattr(runtime, "_install_coding_codex", upgrade, raising=False)
    operation = await runtime.start_install("codex")
    assert operation["status"] == "installing"
    await entered.wait()
    repeated = await runtime.start_install("codex")
    assert repeated["id"] == operation["id"]
    release.set()
    await runtime._install_tasks["codex"]
    assert runtime.operation("codex", operation["id"])["status"] == "failed"
    assert runtime.command("codex") == [str(original)]
    assert original.read_bytes() == b"pinned-primary"
    retry = await runtime.start_install("codex")
    assert retry["status"] == "installing"
    assert retry["id"] != operation["id"]
    await runtime._install_tasks["codex"]
    assert len(calls) == 2


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
@pytest.mark.parametrize(
    "purpose,release_version,official_size", [("primary", "0.145.0", 310730800), ("coding", "0.159.3", 287086056)]
)
@pytest.mark.parametrize("binary_size", [None, "official"], ids=["small-fixture", "official-expanded-size"])
async def test_managed_codex_install_uses_a_pinned_verified_release(
    tmp_path, monkeypatch, binary_size, purpose, release_version, official_size
):
    target = "x86_64-unknown-linux-musl"
    binary_payload = f"#!/bin/sh\necho 'codex-cli {release_version}'\n".encode()
    binary_size = official_size if binary_size == "official" else len(binary_payload)

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
                f"https://github.com/openai/codex/releases/download/rust-v{release_version}/codex-x86_64-unknown-linux-musl.tar.gz"
            )
            return FakeStream()

    monkeypatch.setattr(coding_agent_runtime_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(coding_agent_runtime_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        coding_agent_runtime_module,
        "_CODEX_RELEASES" if purpose == "primary" else "_CODEX_CODING_RELEASES",
        {("linux", "x86_64"): (target, hashlib.sha256(archive_payload).hexdigest())},
    )
    monkeypatch.setattr(
        coding_agent_runtime_module,
        "_CODEX_BINARY_SIZES" if purpose == "primary" else "_CODEX_CODING_BINARY_SIZES",
        {target: binary_size},
    )
    monkeypatch.setattr(coding_agent_runtime_module.httpx, "AsyncClient", FakeClient)
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    monkeypatch.setattr(runtime, "managed_command", lambda _agent_id: runtime.agent_root("codex") / "bin" / "codex")
    monkeypatch.setattr(runtime, "_prepare_coding_home", lambda: runtime._coding_root() / "state")
    if purpose == "coding":
        primary = runtime.managed_command("codex")
        primary.parent.mkdir(parents=True)
        primary.write_bytes(b"original-primary")
    verified_paths = []

    async def probe_verified_binary(argv, *, agent_id):
        assert agent_id == "codex"
        assert argv[1:] == ["--version"]
        assert Path(argv[0]).stat().st_size == binary_size
        with Path(argv[0]).open("rb") as source:
            assert source.read(len(binary_payload)) == binary_payload
        verified_paths.append(Path(argv[0]))
        return 0, f"codex-cli {release_version}"

    chmod_calls = {}
    original_chmod = Path.chmod

    def record_chmod(path, mode, **kwargs):
        chmod_calls[path] = mode
        return original_chmod(path, mode, **kwargs)

    monkeypatch.setattr(runtime, "_run_probe", probe_verified_binary)
    monkeypatch.setattr(Path, "chmod", record_chmod)

    if purpose == "primary":
        await runtime._install_codex("verified-release")
    else:
        await runtime._install_coding_codex("verified-release")
        assert primary.read_bytes() == b"original-primary"
        assert runtime.command("codex") == [str(primary)]

    binary = Path(runtime.coding_command("codex")[0])
    assert binary.stat().st_size == binary_size
    with binary.open("rb") as source:
        assert source.read(len(binary_payload)) == binary_payload
    assert len(verified_paths) == 1
    assert chmod_calls[verified_paths[0]] == 0o700


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose,release_version", [("primary", "0.145.0"), ("coding", "0.159.3")])
@pytest.mark.parametrize(
    "invalid", ["size-minus-one", "size-plus-one", "oversize", "path", "symlink", "hardlink", "version"]
)
async def test_managed_codex_install_rejects_invalid_binary_and_cleans_staging(
    tmp_path, monkeypatch, invalid, purpose, release_version
):
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
        assert str(request.url).endswith(f"/rust-v{release_version}/codex-{target}.tar.gz")
        return coding_agent_runtime_module.httpx.Response(200, content=payload)

    def client_factory(**kwargs):
        return actual_client(transport=coding_agent_runtime_module.httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(coding_agent_runtime_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(coding_agent_runtime_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        coding_agent_runtime_module,
        "_CODEX_RELEASES" if purpose == "primary" else "_CODEX_CODING_RELEASES",
        {("linux", "x86_64"): (target, hashlib.sha256(payload).hexdigest())},
    )
    monkeypatch.setattr(
        coding_agent_runtime_module,
        "_CODEX_BINARY_SIZES" if purpose == "primary" else "_CODEX_CODING_BINARY_SIZES",
        {target: expected_size},
    )
    monkeypatch.setattr(coding_agent_runtime_module.httpx, "AsyncClient", client_factory)
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    monkeypatch.setattr(runtime, "_prepare_coding_home", lambda: runtime._coding_root() / "state")
    if purpose == "coding":
        primary = runtime.managed_command("codex")
        primary.parent.mkdir(parents=True)
        primary.write_bytes(b"original-primary")
    probes = []

    async def probe(argv, *, agent_id):
        probes.append(argv)
        return 0, "codex-cli 0.144.0" if invalid == "version" else f"codex-cli {release_version}"

    monkeypatch.setattr(runtime, "_run_probe", probe)
    with pytest.raises(CodingAgentRuntimeError):
        if purpose == "primary":
            await runtime._install_codex("invalid-binary")
        else:
            await runtime._install_coding_codex("invalid-binary")
    assert len(probes) == (1 if invalid == "version" else 0)
    release_root = runtime.agent_root("codex") if purpose == "primary" else runtime._coding_root()
    assert not (release_root / "bin").exists()
    suffix = "" if purpose == "primary" else "-coding"
    assert not (release_root / f".install-invalid-binary{suffix}").exists()
    if purpose == "coding":
        assert primary.read_bytes() == b"original-primary"


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


@pytest.mark.asyncio
async def test_coding_agent_catalog_reads_model_bindings_after_status_probes(tmp_path, monkeypatch):
    store = CodingAgentConfigStore(tmp_path / "workspace")
    store.update_agent_model("codex", {"mode": "native", "native_model": "gpt-5.6-luna"})
    entered, release = asyncio.Event(), asyncio.Event()

    async def status(agent_id):
        entered.set()
        await release.wait()
        return {"installed": True, "authenticated": agent_id == "codex", "available": True}

    monkeypatch.setattr(store.runtime, "status", status)
    pending = asyncio.create_task(store.runtime_catalog())
    await entered.wait()
    store.update_agent_model("codex", {"mode": "native", "native_model": "gpt-6-luna"})
    release.set()
    agents = await pending
    codex = next(agent for agent in agents if agent["id"] == "codex")
    assert codex["model_config"] == store.get_settings()["agent_models"]["codex"]
    assert codex["model_config"]["native_model"] == "gpt-6-luna"


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
    coding_installations = []

    async def fake_coding_install(operation_id):
        assert runtime.command("codex") is not None
        coding_installations.append(operation_id)

    monkeypatch.setattr(runtime, "_install_coding_codex", fake_coding_install)
    operation = await runtime.start_install("codex")
    while runtime.operation("codex", operation["id"])["status"] == "installing":
        await asyncio.sleep(0.01)
    assert runtime.operation("codex", operation["id"])["status"] == "installed"
    assert coding_installations == [operation["id"]]

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
