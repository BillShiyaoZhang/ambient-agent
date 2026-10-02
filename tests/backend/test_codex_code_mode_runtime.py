from __future__ import annotations

import hashlib
import io
import sys
import tarfile
from unittest.mock import MagicMock

import httpx
import pytest

import backend.coding_agent_runtime as runtime_module
from backend.coding_agent_runtime import CodingAgentRuntime, CodingAgentRuntimeError

TARGET = "x86_64-unknown-linux-musl"
HOST_NAME = "codex-code-mode-host"
HOST_HELP = "Usage: codex-code-mode-host [OPTIONS]\n--listen <URL>"
HOST_BINARY = ("#!/bin/sh\nprintf '%s\\n' '" + HOST_HELP + "'\n").encode()
REAL_HTTPX_CLIENT = httpx.AsyncClient


def cli_binary(version):
    return (
        f"#!/bin/sh\nif [ \"$1\" = login ]; then\n  echo 'Logged in'\nelse\n  echo 'codex-cli {version}'\nfi\n"
    ).encode()


def release_archive(binary_name, payload, *, invalid=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        name = f"{binary_name}-{TARGET}"
        member = tarfile.TarInfo("../" + name if invalid == "path" else name)
        member.mode = 0o755
        member.size = len(payload)
        if invalid in {"symlink", "hardlink"}:
            member.type = tarfile.SYMTYPE if invalid == "symlink" else tarfile.LNKTYPE
            member.linkname = "../outside"
            member.size = 0
        archive.addfile(member, io.BytesIO(payload) if member.isfile() else None)
        if invalid in {"duplicate", "extra_member"}:
            extra = tarfile.TarInfo(name if invalid == "duplicate" else "unexpected-file")
            extra.size = 1
            archive.addfile(extra, io.BytesIO(b"x"))
    return output.getvalue()


@pytest.fixture
def managed_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_COMMAND", raising=False)
    monkeypatch.setenv("CODING_AGENT_RUNTIME_DIR", str(tmp_path / "managed"))
    monkeypatch.setattr(runtime_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime_module.platform, "machine", lambda: "x86_64")
    runtime = CodingAgentRuntime(tmp_path / "workspace")
    monkeypatch.setattr(runtime, "_bridge_command", lambda _spec: ["test-only-bridge"])
    runtime.state_dir("codex").mkdir(parents=True)
    (runtime.state_dir("codex") / "auth.json").write_bytes(b"opaque-original-login")
    return runtime


def write_executable(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o700)


def install_release_transport(monkeypatch, *, host_invalid=None, invalid_download=None):
    requests = []
    binaries = {"primary": cli_binary("0.145.0"), "coding": cli_binary("0.159.3"), "host": HOST_BINARY}
    if host_invalid == "help":
        binaries["host"] = b"#!/bin/sh\necho 'Unrelated executable help'\n"
    host_payload = binaries["host"]
    if host_invalid == "size-minus-one":
        host_payload = host_payload[:-1]
    elif host_invalid == "size-plus-one":
        host_payload += b"x"
    archives = {
        "primary": release_archive("codex", binaries["primary"]),
        "coding": release_archive("codex", binaries["coding"]),
        "host": release_archive(HOST_NAME, host_payload, invalid=host_invalid),
    }
    for component, release_setting, size_setting in (
        ("primary", "_CODEX_RELEASES", "_CODEX_BINARY_SIZES"),
        ("coding", "_CODEX_CODING_RELEASES", "_CODEX_CODING_BINARY_SIZES"),
        ("host", "_CODEX_CODE_MODE_HOST_RELEASES", "_CODEX_CODE_MODE_HOST_BINARY_SIZES"),
    ):
        digest = (
            "0" * 64
            if component == "host" and invalid_download == "checksum"
            else hashlib.sha256(archives[component]).hexdigest()
        )
        monkeypatch.setattr(runtime_module, release_setting, {("linux", "x86_64"): (TARGET, digest)})
        monkeypatch.setattr(runtime_module, size_setting, {TARGET: len(binaries[component])})
    if invalid_download == "stream_size":
        monkeypatch.setattr(runtime_module, "_CODEX_CODE_MODE_HOST_ARCHIVE_LIMIT", 64)

    class ArchiveStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield archives["host"][:64]
            yield archives["host"][64:]

    def handler(request):
        requests.append(str(request.url))
        component = (
            "host"
            if f"/{HOST_NAME}-" in str(request.url)
            else "primary"
            if "rust-v0.145.0/" in str(request.url)
            else "coding"
        )
        if component == "host" and invalid_download == "header_size":
            return httpx.Response(
                200, content=archives[component], headers={"content-length": str(32 * 1024 * 1024 + 1)}
            )
        if component == "host" and invalid_download == "stream_size":
            return httpx.Response(200, stream=ArchiveStream())
        return httpx.Response(200, content=archives[component])

    monkeypatch.setattr(
        runtime_module.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests, binaries


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Pinned Codex managed releases support macOS and Linux")
@pytest.mark.parametrize("existing", ["none", "primary", "partial", "bad_host"])
async def test_install_repairs_complete_managed_bundle_without_changing_primary_login(
    managed_runtime, monkeypatch, existing
):
    runtime = managed_runtime
    requests, binaries = install_release_transport(monkeypatch)
    if existing != "none":
        write_executable(runtime.managed_command("codex"), binaries["primary"])
    if existing in {"partial", "bad_host"}:
        write_executable(runtime._managed_coding_command(), binaries["coding"])
    if existing == "bad_host":
        write_executable(runtime._managed_code_mode_host(), b"broken-old-host")

    operation = await runtime.start_install("codex")
    assert operation["status"] == "installing"
    await runtime._install_tasks["codex"]

    assert runtime.operation("codex", operation["id"])["status"] == "installed"
    assert len(requests) == {"none": 3, "primary": 2, "partial": 1, "bad_host": 1}[existing]
    assert runtime.managed_command("codex").read_bytes() == binaries["primary"]
    assert runtime._managed_coding_command().read_bytes() == binaries["coding"]
    assert runtime._managed_code_mode_host().read_bytes() == binaries["host"]
    assert (runtime.state_dir("codex") / "auth.json").read_bytes() == b"opaque-original-login"
    assert (runtime._coding_root() / "state" / "auth.json").resolve() == (
        runtime.state_dir("codex") / "auth.json"
    ).resolve()
    assert not list(runtime._coding_root().glob(".install-*"))
    assert runtime._managed_code_mode_host().stat().st_mode & 0o777 == 0o700
    status = await runtime.status("codex")
    assert status["installed"] and status["available"] and status["authenticated"]
    assert status["update_available"] is False
    assert status["version"] == "codex-cli 0.159.3"
    assert (await runtime.start_install("codex"))["status"] == "installed"
    launch = runtime.acp_launch("codex")
    assert launch.environment["CODEX_PATH"] == str(runtime._managed_coding_command())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        "checksum",
        "size-minus-one",
        "size-plus-one",
        "path",
        "symlink",
        "hardlink",
        "duplicate",
        "extra_member",
        "header_size",
        "stream_size",
        "help",
    ],
)
async def test_bad_companion_download_preserves_existing_binary_and_cleans_staging(
    managed_runtime, monkeypatch, invalid
):
    runtime = managed_runtime
    host_invalid = invalid if invalid not in {"checksum", "header_size", "stream_size"} else None
    download_invalid = invalid if invalid in {"checksum", "header_size", "stream_size"} else None
    requests, binaries = install_release_transport(
        monkeypatch, host_invalid=host_invalid, invalid_download=download_invalid
    )
    write_executable(runtime.managed_command("codex"), binaries["primary"])
    write_executable(runtime._managed_coding_command(), binaries["coding"])
    write_executable(runtime._managed_code_mode_host(), b"old-host-must-survive")

    with pytest.raises(CodingAgentRuntimeError) as failure:
        await runtime._install_coding_codex("rejected-host")

    assert failure.value.code == "install_failed"
    assert len(requests) == 1
    assert f"/{HOST_NAME}-{TARGET}.tar.gz" in requests[0]
    assert runtime._managed_code_mode_host().read_bytes() == b"old-host-must-survive"
    assert runtime._managed_coding_command().read_bytes() == binaries["coding"]
    assert runtime.managed_command("codex").read_bytes() == binaries["primary"]
    assert (runtime.state_dir("codex") / "auth.json").read_bytes() == b"opaque-original-login"
    assert not list(runtime._coding_root().glob(".install-*"))


@pytest.mark.asyncio
async def test_failed_first_companion_install_retries_only_missing_companion(managed_runtime, monkeypatch):
    runtime = managed_runtime
    requests, binaries = install_release_transport(monkeypatch, invalid_download="checksum")
    write_executable(runtime.managed_command("codex"), binaries["primary"])
    first = await runtime.start_install("codex")
    await runtime._install_tasks["codex"]

    assert runtime.operation("codex", first["id"])["status"] == "failed"
    assert len(requests) == 2
    assert runtime._managed_coding_command().read_bytes() == binaries["coding"]
    assert not runtime._managed_code_mode_host().exists()
    status = await runtime.status("codex")
    assert status["installed"] and status["authenticated"]
    assert status["available"] is False
    assert status["update_available"] is True
    assert "code mode" in status["status_detail"]
    with pytest.raises(CodingAgentRuntimeError) as failure:
        runtime.acp_launch("codex")
    assert failure.value.code == "coding_agent_code_mode_unavailable"

    repaired_requests, _ = install_release_transport(monkeypatch)
    retry = await runtime.start_install("codex")
    await runtime._install_tasks["codex"]

    assert retry["id"] != first["id"]
    assert runtime.operation("codex", retry["id"])["status"] == "installed"
    assert len(repaired_requests) == 1
    assert runtime._managed_coding_command().read_bytes() == binaries["coding"]
    assert runtime._managed_code_mode_host().read_bytes() == HOST_BINARY


@pytest.mark.asyncio
async def test_same_size_unrunnable_companion_is_unavailable_and_can_be_repaired(managed_runtime, monkeypatch):
    runtime = managed_runtime
    requests, binaries = install_release_transport(monkeypatch)
    write_executable(runtime.managed_command("codex"), binaries["primary"])
    write_executable(runtime._managed_coding_command(), binaries["coding"])
    wrong_help = b"#!/bin/sh\necho 'wrong helper'\n"
    write_executable(runtime._managed_code_mode_host(), wrong_help + b" " * (len(HOST_BINARY) - len(wrong_help)))
    assert runtime._code_mode_host_file_ready()

    status = await runtime.status("codex")

    assert status["installed"] and status["authenticated"]
    assert status["available"] is False
    assert status["update_available"] is True
    assert "code mode" in status["status_detail"]
    operation = await runtime.start_install("codex")
    await runtime._install_tasks["codex"]
    assert runtime.operation("codex", operation["id"])["status"] == "installed"
    assert len(requests) == 1
    assert runtime._managed_code_mode_host().read_bytes() == HOST_BINARY


def test_partial_bundle_launch_fails_before_resolving_bridge_or_creating_coding_home(managed_runtime, monkeypatch):
    runtime = managed_runtime
    write_executable(runtime._managed_coding_command(), cli_binary("0.159.3"))
    bridge = MagicMock(side_effect=AssertionError("Missing companion must be reported first"))
    monkeypatch.setattr(runtime, "_bridge_command", bridge)

    with pytest.raises(CodingAgentRuntimeError) as failure:
        runtime.acp_launch("codex")

    assert failure.value.code == "coding_agent_code_mode_unavailable"
    assert "repair" in str(failure.value)
    bridge.assert_not_called()
    assert not (runtime._coding_root() / "state").exists()


def test_explicit_codex_and_legacy_cli_do_not_require_managed_companion(managed_runtime, monkeypatch):
    runtime = managed_runtime
    write_executable(runtime.managed_command("codex"), cli_binary("0.145.0"))
    assert runtime.acp_launch("codex").environment["CODEX_PATH"] == str(runtime.managed_command("codex"))
    monkeypatch.setenv("CODEX_COMMAND", sys.executable)
    assert runtime.acp_launch("codex").environment["CODEX_PATH"] == sys.executable
