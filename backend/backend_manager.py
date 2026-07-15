import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import httpx
from backend.app_manifest import AppManifest

logger = logging.getLogger(__name__)


class StdioJsonRpcClient:
    def __init__(self, command: list[str], args: list[str], env: dict[str, str] | None = None):
        self.command = command
        self.args = args
        self.env = env
        self.process: asyncio.subprocess.Process | None = None
        self.read_task: asyncio.Task | None = None
        self.pending_requests: dict[int | str, asyncio.Future] = {}
        self.next_id = 1
        self.lock = asyncio.Lock()

    async def start(self):
        logger.info(f"Starting MCP process: {self.command} {self.args}")
        env = os.environ.copy()
        if self.env:
            env.update(self.env)
        
        self.process = await asyncio.create_subprocess_exec(
            self.command[0],
            *self.command[1:],
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env
        )
        self.read_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            while self.process and self.process.stdout:
                line = await self.process.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode("utf-8"))
                    if isinstance(data, dict):
                        req_id = data.get("id")
                        if req_id is not None and req_id in self.pending_requests:
                            fut = self.pending_requests.pop(req_id)
                            if "error" in data:
                                fut.set_exception(Exception(data["error"].get("message", "Unknown RPC error")))
                            else:
                                fut.set_result(data.get("result"))
                except Exception as e:
                    logger.error(f"Error parsing MCP message: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in MCP read loop: {e}")

    async def call(self, method: str, params: dict) -> Any:
        if not self.process or self.process.returncode is not None:
            raise Exception("MCP server is not running")
        
        async with self.lock:
            req_id = self.next_id
            self.next_id += 1

        fut = asyncio.get_running_loop().create_future()
        self.pending_requests[req_id] = fut

        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        
        payload = json.dumps(req) + "\n"
        assert self.process.stdin is not None
        self.process.stdin.write(payload.encode("utf-8"))
        await self.process.stdin.drain()

        return await fut

    async def stop(self):
        if self.read_task:
            self.read_task.cancel()
            try:
                await self.read_task
            except asyncio.CancelledError:
                pass
        if self.process:
            try:
                self.process.terminate()
                await self.process.wait()
            except Exception:
                pass
            self.process = None


class BackendManager:
    def __init__(self):
        self.workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
        self.permissions_file = Path(self.workspace_dir) / "backend_permissions.json"
        self.mcp_clients: dict[str, StdioJsonRpcClient] = {}
        self.pending_permissions: dict[str, asyncio.Future] = {}
        self.permissions: dict[str, Any] = {}
        self._load_permissions()

    def _load_permissions(self):
        if self.permissions_file.exists():
            try:
                self.permissions = json.loads(self.permissions_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Error loading permissions: {e}")
                self.permissions = {}
        else:
            self.permissions = {}

    def _save_permissions(self):
        try:
            self.permissions_file.parent.mkdir(parents=True, exist_ok=True)
            self.permissions_file.write_text(json.dumps(self.permissions, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error saving permissions: {e}")

    def is_mcp_approved(self, app_id: str, command: list[str], args: list[str]) -> bool:
        app_perms = self.permissions.get(app_id, {})
        mcp_perms = app_perms.get("mcp_servers", [])
        target = {"command": command, "args": args}
        return target in mcp_perms

    def approve_mcp(self, app_id: str, command: list[str], args: list[str]):
        if app_id not in self.permissions:
            self.permissions[app_id] = {}
        if "mcp_servers" not in self.permissions[app_id]:
            self.permissions[app_id]["mcp_servers"] = []
        target = {"command": command, "args": args}
        if target not in self.permissions[app_id]["mcp_servers"]:
            self.permissions[app_id]["mcp_servers"].append(target)
            self._save_permissions()

    def is_agent_approved(self, app_id: str, agent_url: str) -> bool:
        app_perms = self.permissions.get(app_id, {})
        agent_perms = app_perms.get("agents", [])
        return agent_url in agent_perms

    def approve_agent(self, app_id: str, agent_url: str):
        if app_id not in self.permissions:
            self.permissions[app_id] = {}
        if "agents" not in self.permissions[app_id]:
            self.permissions[app_id]["agents"] = []
        if agent_url not in self.permissions[app_id]["agents"]:
            self.permissions[app_id]["agents"].append(agent_url)
            self._save_permissions()

    def resolve_permission(self, request_id: str, approved: bool):
        fut = self.pending_permissions.get(request_id)
        if fut and not fut.done():
            fut.set_result(approved)

    async def request_permission(
        self,
        app_id: str,
        permission_type: str,
        value: dict | str,
        send_ws_message_func: Callable
    ) -> bool:
        import uuid
        request_id = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self.pending_permissions[request_id] = fut

        req_msg = {
            "type": "backend_permission_request",
            "request_id": request_id,
            "app_id": app_id,
            "permission_type": permission_type,
            "value": value
        }
        try:
            await send_ws_message_func(req_msg)
            approved = await fut
            return approved
        finally:
            self.pending_permissions.pop(request_id, None)

    async def get_or_start_mcp_client(
        self,
        app_id: str,
        manifest: AppManifest,
        send_ws_message_func: Callable
    ) -> StdioJsonRpcClient | None:
        if not manifest.mcp_server:
            return None

        command = manifest.mcp_server["command"]
        args = manifest.mcp_server.get("args", [])
        env = manifest.mcp_server.get("env", None)

        if app_id in self.mcp_clients:
            client = self.mcp_clients[app_id]
            if client.process and client.process.returncode is None:
                return client

        if not self.is_mcp_approved(app_id, command, args):
            approved = await self.request_permission(
                app_id,
                "mcp_spawn",
                {"command": command, "args": args},
                send_ws_message_func
            )
            if not approved:
                raise Exception("Permission denied to launch MCP server")
            self.approve_mcp(app_id, command, args)

        client = StdioJsonRpcClient(command, args, env)
        await client.start()
        self.mcp_clients[app_id] = client
        return client

    async def handle_agent_message(
        self,
        app_id: str,
        manifest: AppManifest,
        message: dict,
        send_ws_message_func: Callable
    ):
        agent_url = manifest.agent_url
        if not agent_url:
            raise Exception("No agent_url configured in app manifest")

        if not self.is_agent_approved(app_id, agent_url):
            approved = await self.request_permission(
                app_id,
                "agent_connect",
                {"agent_url": agent_url},
                send_ws_message_func
            )
            if not approved:
                raise Exception("Permission denied to connect to Agent")
            self.approve_agent(app_id, agent_url)

        async with httpx.AsyncClient() as client:
            async with client.stream("POST", agent_url, json=message, timeout=60.0) as response:
                if response.status_code != 200:
                    logger.error(f"Agent URL returned status {response.status_code}")
                    return
                
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            event_data = json.loads(line[5:].strip())
                            await send_ws_message_func({
                                "type": "ag_ui_event",
                                "app_id": app_id,
                                "event": event_data
                            })
                        except Exception as e:
                            logger.error(f"Error parsing agent SSE event: {e}")

    async def shutdown(self):
        for client in self.mcp_clients.values():
            await client.stop()
        self.mcp_clients.clear()
