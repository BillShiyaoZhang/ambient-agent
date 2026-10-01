"""Verify a supplied cloud Gateway against Ambient with temporary loopback fixtures.

Run with uv run --no-sync python scripts/verify_remote_workspace_handoff.py
--gateway-root /path/to/agent-collaboration-deploy/workspace-gateway.
This checks real Gateway HTTP/Tunnel/browser WS, with synthetic local HTTP and
an echo WS upstream. It does not test the real Ambient UI, models, TLS, or RSS.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import secrets
import socket
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import HTTPException
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.remote_workspace import RemoteWorkspaceConnector, RemoteWorkspaceDenied, RemoteWorkspaceNodeStore


class VerificationFailure(Exception):
    """A static, credential-free assertion message."""


def require(condition, description):
    if not condition:
        raise VerificationFailure(description)


async def eventually(predicate, description, timeout=8):
    try:
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.025)
    except TimeoutError:
        raise VerificationFailure(description) from None
    require(predicate(), description)


class Clock:
    def __init__(self):
        self.value = time.time()

    def __call__(self):
        return self.value

    def datetime(self):
        return datetime.fromtimestamp(self.value, UTC)


class Gateway:
    def __init__(self, api, directory, *, clock=None, port=0, database=None, **limits):
        self.api, self.directory = api, directory
        self.clock = clock or Clock()
        self.port, self.limits = port, limits
        self.database = database or directory / "gateway.sqlite3"
        self.secret = secrets.token_urlsafe(48)
        self.app = self.server = self.task = self.socket = None

    async def __aenter__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", self.port))
        self.port = self.socket.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        config = self.api.GatewayConfig(
            database=str(self.database),
            secret=self.secret,
            workspace_domain=f"localhost:{self.port}",
            control_host=f"127.0.0.1:{self.port}",
            request_timeout=0.75,
            **self.limits,
        )
        self.app = self.api.create_app(config)
        self.app.state.clock = self.clock
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=self.port,
                log_level="critical",
                access_log=False,
                proxy_headers=False,
                ws="websockets",
                ws_max_size=config.http_limit * 4 // 3 + 65536,
                ws_max_queue=config.ws_max_queue,
                lifespan="on",
                timeout_graceful_shutdown=3,
            )
        )
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        try:
            await eventually(lambda: self.server.started or self.task.done(), "Gateway startup failed")
            require(self.server.started, "Gateway startup failed")
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *unused):
        if self.server:
            self.server.should_exit = True
        if self.task:
            try:
                await asyncio.wait_for(self.task, 8)
            except TimeoutError:
                self.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.task
        if self.socket:
            self.socket.close()

    async def service(self, client, method, path, **kwargs):
        return await client.request(
            method, self.url + path, headers={"Authorization": "Bearer " + self.secret}, **kwargs
        )

    async def enroll(self, client, account):
        result = await self.service(client, "POST", f"/v1/accounts/{account}/enrollments", json={"label": account})
        require(result.status_code == 200, "Service enrollment failed")
        return result.json()["enrollment_token"]


class LocalUpstream:
    def __init__(self):
        self.calls = Counter()
        self.held = asyncio.Event()
        self.release = asyncio.Event()

    async def http(self, request):
        key = (request.method, request.url.path, request.url.query)
        self.calls[key] += 1
        require(not request.headers.get("authorization"), "Private Authorization reached the local upstream")
        require(not request.headers.get("cookie"), "Private Cookie reached the local upstream")
        require(not request.headers.get("x-account-id"), "Browser identity reached the local upstream")
        if request.url.params.get("case") in {"timeout", "revoke"}:
            self.held.set()
            await self.release.wait()
        return httpx.Response(200, json={"fixture": "local", "sequence": self.calls[key]})

    @staticmethod
    async def websocket(connection):
        try:
            async for message in connection:
                await connection.send(message)
        except ConnectionClosed:
            pass


def pair_data(gateway, token):
    return {
        "gateway_url": gateway.url,
        "portal_url": "http://localhost:3000",
        "name": "Synthetic Ambient",
        "scopes": ["workspace.control"],
        "expires_in": 3600,
        "enrollment_token": token,
    }


async def make_connector(gateway, client, local, directory, ws_port):
    store = RemoteWorkspaceNodeStore(directory, now=gateway.clock.datetime)
    connector = RemoteWorkspaceConnector(store, gateway_http=client, local_http=local)
    connector.upstreams["backend"] = f"http://127.0.0.1:{ws_port}"
    return connector


async def authorize(gateway, client, connector, account="alice", *, wait_online=True):
    token = await gateway.enroll(client, account)
    paired = await connector.pair(pair_data(gateway, token))
    require(paired["status"] == "pending" and not paired["online"], "Pairing bypassed local confirmation")
    await connector.refresh()
    require(connector.status()["status"] == "pending", "Account-bound pending was mistaken for a claim")
    require(token not in connector.store.path.read_text(encoding="utf-8"), "Enrollment leaked into node state")
    replay = await client.post(gateway.url + "/v1/connector/pairings", json=pair_data(gateway, token))
    # The public Gateway does not accept Ambient-only URL fields.
    require(replay.status_code == 422, "Gateway accepted unexpected pairing fields")
    raw = {key: pair_data(gateway, token)[key] for key in ("enrollment_token", "name", "scopes", "expires_in")}
    replay = await client.post(gateway.url + "/v1/connector/pairings", json=raw)
    require(replay.status_code == 409, "Enrollment replay was accepted")
    code = paired["pairing_code"]
    wrong = await gateway.service(
        client, "POST", "/v1/accounts/wrong-account/pairings/claim", json={"code": code, "label": "wrong"}
    )
    require(wrong.status_code == 409, "Wrong account claimed a node")
    claimed = await gateway.service(
        client, "POST", f"/v1/accounts/{account}/pairings/claim", json={"code": code, "label": account}
    )
    require(claimed.status_code == 200, "Wrong account consumed the pairing code")
    await connector.refresh()
    require(connector.status()["status"] == "claimed" and not connector.online, "Cloud claim started the Tunnel")
    try:
        await connector.approve("wrong-account", claimed.json()["grant_id"])
    except RemoteWorkspaceDenied:
        pass
    else:
        raise VerificationFailure("Local approval accepted a different account")
    await connector.approve(account, claimed.json()["grant_id"])
    if wait_online:
        await eventually(lambda: connector.online, "Approved Connector did not establish the real Tunnel")
    return connector.store.identity()


async def browser_session(gateway, client, identity):
    result = await gateway.service(
        client, "POST", f"/v1/accounts/{identity['account_id']}/nodes/{identity['node_id']}/launch"
    )
    require(result.status_code == 200, "Workspace launch failed")
    launch = urlsplit(result.json()["url"])
    response = await client.get(
        gateway.url + launch.path + "?" + launch.query, headers={"Host": launch.netloc}, follow_redirects=False
    )
    require(response.status_code == 303, "Launch ticket did not produce a browser session")
    require(
        "Domain=" not in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"],
        "Browser session cookie lost host-only or HttpOnly protection",
    )
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    cookie = "; ".join(f"{key}={value.value}" for key, value in cookies.items())
    again = await client.get(gateway.url + launch.path + "?" + launch.query, headers={"Host": launch.netloc})
    require(again.status_code == 401, "Launch ticket replay was accepted")
    return {"Host": launch.netloc, "Cookie": cookie, "Origin": identity["workspace_origin"]}


def browser_connect(gateway, identity, headers, path):
    uri = identity["workspace_origin"].replace("http://", "ws://", 1) + path
    return connect(
        uri,
        host="127.0.0.1",
        port=gateway.port,
        origin=identity["workspace_origin"],
        additional_headers={"Cookie": headers["Cookie"]},
        subprotocols=["handoff.echo"],
        proxy=None,
        open_timeout=4,
        close_timeout=1,
    )


async def test_transport(api, directory, report):
    fixture = LocalUpstream()
    async with Gateway(api, directory, buffer_bytes=256 * 1024 * 1024) as gateway:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.http)) as local:
                async with serve(fixture.websocket, "127.0.0.1", 0, subprotocols=["handoff.echo"]) as upstream:
                    port = upstream.sockets[0].getsockname()[1]
                    connector = await make_connector(gateway, client, local, directory / "ambient", port)
                    try:
                        missing = await client.post(gateway.url + "/v1/connector/pairings", json={"name": "missing"})
                        require(missing.status_code == 422, "Anonymous enrollment was accepted")
                        identity = await authorize(gateway, client, connector)
                        headers = await browser_session(gateway, client, identity)
                        response = await client.post(
                            gateway.url + "/api/runs",
                            headers=headers
                            | {"Authorization": "Bearer synthetic-browser-header", "X-Account-Id": "forged-account"},
                            json={"fixture": "single-write"},
                        )
                        require(response.status_code == 200, "Real Tunnel HTTP write failed")
                        require(fixture.calls[("POST", "/api/runs", b"")] == 1, "HTTP write was replayed")
                        response = await client.post(gateway.url + "/api/runs?case=timeout", headers=headers, json={})
                        require(response.status_code == 504, "Slow local write did not return the Gateway timeout")
                        fixture.release.set()
                        await asyncio.sleep(0.15)
                        require(
                            fixture.calls[("POST", "/api/runs", b"case=timeout")] == 1, "Timed-out write was replayed"
                        )
                        async with contextlib.AsyncExitStack() as browsers:
                            sockets = []
                            for path in ("/ws/chat", "/ws/runs", "/ws/run-live", "/ws/widgets/fixture/runtime"):
                                ws = await browsers.enter_async_context(
                                    browser_connect(gateway, identity, headers, path)
                                )
                                require(ws.subprotocol == "handoff.echo", "WebSocket subprotocol was lost")
                                await ws.send("synthetic-text")
                                require(
                                    await asyncio.wait_for(ws.recv(), 3) == "synthetic-text",
                                    "Text frame did not traverse both directions",
                                )
                                await ws.send(b"\x00synthetic-binary")
                                require(
                                    await asyncio.wait_for(ws.recv(), 3) == b"\x00synthetic-binary",
                                    "Binary frame did not traverse both directions",
                                )
                                sockets.append(ws)
                            fixture.release.clear()
                            fixture.held.clear()
                            pending = asyncio.create_task(
                                client.get(gateway.url + "/api/sessions?case=revoke", headers=headers)
                            )
                            await asyncio.wait_for(fixture.held.wait(), 3)
                            await connector.revoke()
                            inactive = await asyncio.wait_for(pending, 3)
                            require(inactive.status_code in {401, 503}, "Revocation left in-flight HTTP active")
                            for ws in sockets:
                                await asyncio.wait_for(ws.wait_closed(), 3)
                            require(
                                not connector.online and not connector.store._state.get("connector_token"),
                                "Revocation retained local forwarding credentials",
                            )
                            require(
                                (await client.get(gateway.url + "/api/sessions", headers=headers)).status_code == 401,
                                "Revocation left the browser cookie usable",
                            )
                        report.append(
                            "enrollment/owner/local-confirmation/real-Tunnel/HTTP-once/timeout-no-replay/4-WS/subprotocol/text+binary/revoke"
                        )
                    finally:
                        fixture.release.set()
                        await connector.stop()


async def test_admission(api, directory, report):
    async with Gateway(api, directory, max_pending=1) as gateway:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            token = await gateway.enroll(client, "alice")
            first = await client.post(
                gateway.url + "/v1/connector/pairings", json={"name": "pending", "enrollment_token": token}
            )
            require(first.status_code == 200, "Quota fixture pairing failed")
            second = await gateway.enroll(client, "bob")
            connector = RemoteWorkspaceConnector(
                RemoteWorkspaceNodeStore(directory / "ambient", now=gateway.clock.datetime), gateway_http=client
            )
            try:
                try:
                    await connector.pair(pair_data(gateway, second))
                except ValueError as error:
                    require(
                        getattr(error, "status_code", None) == 429 and getattr(error, "retry_after", None) == 60,
                        "Connector did not safely expose quota and Retry-After",
                    )
                else:
                    raise VerificationFailure("Pending quota was bypassed")
                require(
                    gateway.app.state.store.one(
                        "SELECT token_hash FROM enrollments WHERE token_hash=?", (api.digest(second),)
                    ),
                    "Capacity rejection consumed enrollment",
                )
                source_counts = dict(gateway.app.state.sources.entries)
                try:
                    await connector.pair(pair_data(gateway, second))
                except ValueError as error:
                    require(getattr(error, "status_code", None) == 429, "Local cooldown changed the safe error")
                else:
                    raise VerificationFailure("Immediate retry ignored cooldown")
                require(
                    dict(gateway.app.state.sources.entries) == source_counts, "Cooldown sent another pairing request"
                )
                await gateway.service(client, "DELETE", f"/v1/accounts/alice/nodes/{first.json()['node_id']}")
                gateway.clock.value += 61
                require(
                    (await connector.pair(pair_data(gateway, second)))["status"] == "pending",
                    "Unconsumed enrollment could not be reused after cooldown",
                )
                await connector.stop()
                expired = await gateway.enroll(client, "carol")
                gateway.clock.value += 301
                failed = await client.post(
                    gateway.url + "/v1/connector/pairings", json={"name": "expired", "enrollment_token": expired}
                )
                require(failed.status_code == 409, "Expired enrollment was accepted")
                report.append("quota-preserves-enrollment/429-Retry-After/local-cooldown/expired-enrollment")
            finally:
                await connector.stop()


async def test_terminal(api, directory, report):
    async with Gateway(api, directory) as gateway:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            for account, reason in (
                ("expiring", "expiry"),
                ("deleted", "account-delete"),
                ("invalid", "invalid-device"),
            ):
                connector = RemoteWorkspaceConnector(
                    RemoteWorkspaceNodeStore(directory / account, now=gateway.clock.datetime), gateway_http=client
                )
                try:
                    await authorize(gateway, client, connector, account)
                    if reason == "expiry":
                        gateway.clock.value += 3601
                        await connector.refresh()
                    elif reason == "account-delete":
                        result = await gateway.service(client, "DELETE", f"/v1/accounts/{account}")
                        require(result.status_code == 200, "Account deletion failed")
                        await eventually(
                            lambda connector=connector: not connector.store._state.get("connector_token"),
                            "Account deletion did not terminate the Connector",
                        )
                        enrollment = await gateway.service(
                            client, "POST", f"/v1/accounts/{account}/enrollments", json={"label": account}
                        )
                        require(enrollment.status_code == 410, "Deleted account generated enrollment")
                    else:
                        await connector.stop()
                        connector.store.save({**connector.store._state, "connector_token": "invalid-synthetic-device"})
                        try:
                            await connector.refresh()
                        except ValueError as error:
                            require(
                                getattr(error, "status_code", None) == 401, "Invalid device error was not mapped safely"
                            )
                    require(
                        connector.status()["status"] in {"expired", "revoked"} and not connector.online,
                        "Terminal authorization retained forwarding",
                    )
                    require(
                        not connector.store._state.get("connector_token"),
                        "Terminal authorization retained device credentials",
                    )
                    require(
                        connector._runner is None or connector._runner.done(),
                        "Terminal authorization continued polling",
                    )
                finally:
                    await connector.stop()
            report.append("grant-expiry/account-delete+permanent-tombstone/invalid-device-stop")


async def test_default_ws_budget(api, directory, report):
    fixture = LocalUpstream()
    async with Gateway(api, directory, buffer_bytes=128 * 1024 * 1024) as gateway:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.http)) as local:
                async with serve(fixture.websocket, "127.0.0.1", 0, subprotocols=["handoff.echo"]) as upstream:
                    connector = await make_connector(
                        gateway, client, local, directory / "ambient", upstream.sockets[0].getsockname()[1]
                    )
                    try:
                        identity = await authorize(gateway, client, connector)
                        headers = await browser_session(gateway, client, identity)
                        async with contextlib.AsyncExitStack() as browsers:
                            for path in ("/ws/chat", "/ws/runs"):
                                await browsers.enter_async_context(browser_connect(gateway, identity, headers, path))
                            try:
                                async with browser_connect(gateway, identity, headers, "/ws/run-live"):
                                    raise VerificationFailure("128 MiB budget unexpectedly admitted a third browser WS")
                            except InvalidStatus as error:
                                require(
                                    error.response.status_code == 403,
                                    "Capacity rejection returned an unexpected WS handshake status",
                                )
                            require(
                                gateway.app.state.resources.metrics["capacity_rejections"] >= 1,
                                "Third WS rejection was not caused by resource admission",
                            )
                        report.append("128-MiB-real-third-WS-rejected-as-documented")
                    finally:
                        await connector.stop()


async def test_tunnel_capacity_recovery(api, directory, report):
    async with Gateway(api, directory, global_tunnel_concurrency=1) as gateway:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            backoff_started = asyncio.Event()

            async def observed_sleep(delay):
                if delay >= 3:
                    backoff_started.set()
                await asyncio.sleep(delay)

            first = RemoteWorkspaceConnector(
                RemoteWorkspaceNodeStore(directory / "alice", now=gateway.clock.datetime), gateway_http=client
            )
            second = RemoteWorkspaceConnector(
                RemoteWorkspaceNodeStore(directory / "bob", now=gateway.clock.datetime),
                gateway_http=client,
                sleep=observed_sleep,
            )
            try:
                await authorize(gateway, client, first, "alice")
                identity = await authorize(gateway, client, second, "bob", wait_online=False)
                credential = second.store._state["connector_token"]
                await eventually(
                    lambda: gateway.app.state.resources.metrics["capacity_rejections"] >= 1,
                    "Second Tunnel did not encounter the controlled capacity limit",
                )
                await asyncio.wait_for(backoff_started.wait(), 8)
                require(first.online and not second.online, "Capacity rejection replaced the existing Tunnel")
                require(
                    second.store._state.get("connector_token") == credential and second.status()["status"] == "paired",
                    "Capacity handshake 403 incorrectly terminated a valid device",
                )
                require(second.store.identity() == identity, "Capacity rejection changed the approved identity")
                await first.stop()
                await eventually(lambda: second.online, "Second Tunnel did not recover after capacity was released")
                require(
                    second.store.identity() == identity and second.store._state.get("connector_token") == credential,
                    "Capacity recovery replaced identity or device credentials",
                )
                report.append(
                    "Tunnel-capacity-403/state-recheck/identity-retained/first-not-replaced/automatic-recovery"
                )
            finally:
                await second.stop()
                await first.stop()


async def test_legacy_restart(api, directory, report):
    clock = Clock()
    database = directory / "legacy.sqlite3"
    token, pending_token, node, grant = secrets.token_urlsafe(48), secrets.token_urlsafe(48), "b" * 24, "c" * 32
    with contextlib.closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE nodes(node_id TEXT PRIMARY KEY,name TEXT,token_hash TEXT UNIQUE,pairing_hash TEXT UNIQUE,"
            "pairing_expires REAL,status TEXT,account_id TEXT,account_label TEXT,grant_id TEXT,scopes TEXT,"
            "expires REAL,last_seen REAL,created REAL)"
        )
        db.execute(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                node,
                "legacy",
                api.digest(token),
                None,
                clock() + 300,
                "paired",
                "legacy-owner",
                "legacy-owner",
                grant,
                '["workspace.control"]',
                clock() + 3600,
                clock(),
                clock(),
            ),
        )
        db.execute(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "d" * 24,
                "pending",
                api.digest(pending_token),
                api.digest("legacy-code"),
                clock() + 300,
                "pending",
                None,
                None,
                "e" * 32,
                '["workspace.control"]',
                clock() + 3600,
                None,
                clock(),
            ),
        )
    port = 0
    identity = None
    for restart in (False, True):
        async with Gateway(api, directory, clock=clock, port=port, database=database) as gateway:
            port = gateway.port
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                store = RemoteWorkspaceNodeStore(directory / "ambient", now=clock.datetime)
                if not restart:
                    store.save(
                        {
                            "node_id": node,
                            "account_id": "legacy-owner",
                            "account_label": "legacy-owner",
                            "grant_id": grant,
                            "scopes": ["workspace.control"],
                            "workspace_origin": f"http://{node}.localhost:{port}",
                            "status": "paired",
                            "approved": True,
                            "connector_token": token,
                            "gateway_url": gateway.url,
                            "portal_url": "http://localhost:3000",
                            "name": "legacy",
                            "expires_at": api.iso(clock() + 3600),
                        }
                    )
                    identity = store.identity()
                connector = RemoteWorkspaceConnector(store, gateway_http=client)
                try:
                    await connector.refresh()
                    require(
                        store.identity() == identity and store._state["connector_token"] == token,
                        "Legacy restart changed paired identity, grant, origin, or credentials",
                    )
                    await connector.start()
                    await eventually(
                        lambda connector=connector: connector.online, "Legacy paired identity did not reconnect"
                    )
                    pending = await client.get(
                        gateway.url + "/v1/connector/state", headers={"Authorization": "Bearer " + pending_token}
                    )
                    require(pending.json()["status"] == "expired", "Legacy pending identity remained usable")
                    claim = await gateway.service(
                        client,
                        "POST",
                        "/v1/accounts/legacy-owner/pairings/claim",
                        json={"code": "legacy-code", "label": "legacy-owner"},
                    )
                    require(claim.status_code == 409, "Legacy unbound pairing could be claimed")
                finally:
                    await connector.stop()
    report.append("legacy-schema/paired-identity+origin-preserved/Gateway+Ambient-restart/old-pending-invalidated")


def budget_probe(api, safeguards):
    results = {}
    for mib in (128, 256):
        resources = safeguards.Resources(
            api.GatewayConfig(secret="synthetic-budget-secret", buffer_bytes=mib * 1024 * 1024)
        )
        admitted = []
        rejected = None
        for kind in ("tunnel", "ws", "ws", "ws", "ws", "http", "http"):
            try:
                resources.acquire(kind, "synthetic-node")
                admitted.append(kind)
            except HTTPException as error:
                rejected = {"kind": kind, "status": error.status_code}
                break
        results[str(mib)] = {"admitted": admitted, "reserved_bytes": resources.buffer_bytes, "rejected": rejected}
    require(
        results["128"]["rejected"] == {"kind": "ws", "status": 429},
        "128 MiB budget no longer rejects the third browser WS",
    )
    require(results["256"]["rejected"] is None, "256 MiB budget cannot admit the documented fixture")
    return results


async def run(gateway_root):
    require(
        (gateway_root / "workspace_gateway" / "app.py").is_file(), "Gateway root must contain workspace_gateway/app.py"
    )
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(gateway_root))
    api = importlib.import_module("workspace_gateway.app")
    safeguards = importlib.import_module("workspace_gateway.safeguards")
    require(Path(api.__file__).resolve().parents[1] == gateway_root, "Loaded Gateway differs from the supplied root")
    report = []
    with tempfile.TemporaryDirectory(prefix="ambient-cloud-handoff-") as temporary:
        root = Path(temporary)
        for name, test in (
            ("transport", test_transport),
            ("admission", test_admission),
            ("terminal", test_terminal),
            ("default-budget", test_default_ws_budget),
            ("tunnel-capacity", test_tunnel_capacity_recovery),
            ("legacy-restart", test_legacy_restart),
        ):
            directory = root / name
            directory.mkdir()
            try:
                await test(api, directory, report)
            except VerificationFailure as error:
                raise VerificationFailure(f"{name}: {error}") from None
            except Exception as error:
                raise VerificationFailure(f"{name}: fixture failed ({type(error).__name__})") from None
    return {
        "status": "PASS",
        "passed_phases": len(report),
        "checks": report,
        "application_budget_probe_mib": budget_probe(api, safeguards),
        "gateway_app_sha256": hashlib.sha256((gateway_root / "workspace_gateway" / "app.py").read_bytes()).hexdigest(),
        "environment": "temporary synthetic state; loopback Gateway HTTP/WS and echo WS; mocked local HTTP",
        "limits": [
            "No real Ambient UI/model or user data",
            "No public DNS, trusted TLS, nginx, or production load",
            "128 MiB default rejects third WS; 256 MiB fixture passes; reservation is not measured RSS",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-root", type=Path, required=True, help="Cloud workspace-gateway source directory")
    args = parser.parse_args()
    previous_mode = os.environ.get("AMBIENT_REMOTE_UPSTREAM_MODE")
    os.environ["AMBIENT_REMOTE_UPSTREAM_MODE"] = "loopback"
    try:
        result = asyncio.run(run(args.gateway_root.resolve()))
    except VerificationFailure as error:
        print(json.dumps({"status": "FAIL", "check": str(error)}, ensure_ascii=False))
        return 1
    except Exception as error:
        # External exceptions can contain request URLs. Never echo their text.
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(error).__name__,
                    "check": "Fixture or contract verification failed",
                }
            )
        )
        return 1
    finally:
        if previous_mode is None:
            os.environ.pop("AMBIENT_REMOTE_UPSTREAM_MODE", None)
        else:
            os.environ["AMBIENT_REMOTE_UPSTREAM_MODE"] = previous_mode
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
