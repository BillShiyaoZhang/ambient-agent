import os
import shutil
import uuid
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Callable, Any, Dict, List

from acp import Client, PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.exceptions import RequestError
from acp.schema import (
    ClientCapabilities,
    FileSystemCapabilities,
    ReadTextFileResponse,
    WriteTextFileResponse,
    CreateTerminalResponse,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
    ReleaseTerminalResponse,
    KillTerminalResponse,
    RequestPermissionResponse,
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    EnvVariable,
    TerminalExitStatus,
)

logger = logging.getLogger("opencode_service")

# Registry of active ACP clients to map incoming WebSocket permission responses
active_acp_clients: Dict[str, "FastAPIACPClient"] = {}


class PermissionPolicyManager:
    def __init__(self, config_path: str = None):
        if config_path is None:
            # Look in backend/opencode_permissions.json relative to this file
            config_path = os.path.join(os.path.dirname(__file__), "opencode_permissions.json")
        self.config_path = config_path
        self.policy = self._load_policy()

    def _load_policy(self) -> dict:
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading permission policy: {e}")
        return {
            "policy_mode": "interactive",
            "files": {
                "allowed_extensions": [".html", ".css", ".js", ".json", ".md"],
                "allowed_filenames": ["index.html", "style.css", "controller.js", "data.json", "README.md"]
            },
            "commands": {
                "allowed_commands": ["npm test", "npm run build", "npm install"],
                "allowed_prefixes": ["npm install ", "echo "],
                "blocklist": ["rm -rf", "curl", "wget", "sudo", "mv"]
            }
        }

    def validate_file_path(self, path_str: str, workspace_root: Path) -> bool:
        # Check directory traversal (strict jail)
        try:
            resolved_workspace = workspace_root.resolve()
            p = Path(path_str)
            if p.is_absolute():
                resolved_path = p.resolve()
            else:
                resolved_path = (workspace_root / p).resolve()
            if not str(resolved_path).startswith(str(resolved_workspace)):
                return False
        except Exception:
            return False

        # Validate file suffix/name
        filename = resolved_path.name
        ext = resolved_path.suffix
        
        allowed_filenames = self.policy.get("files", {}).get("allowed_filenames", [])
        allowed_extensions = self.policy.get("files", {}).get("allowed_extensions", [])
        
        if filename in allowed_filenames or ext in allowed_extensions:
            return True
        return False

    def validate_command(self, command_str: str) -> bool:
        command_str = command_str.strip()
        commands_cfg = self.policy.get("commands", {})
        
        # Check blocklist first
        blocklist = commands_cfg.get("blocklist", [])
        for blocked in blocklist:
            if blocked in command_str:
                return False
                
        # Check allowed commands exactly
        allowed_commands = commands_cfg.get("allowed_commands", [])
        if command_str in allowed_commands:
            return True
            
        # Check allowed prefixes
        allowed_prefixes = commands_cfg.get("allowed_prefixes", [])
        for prefix in allowed_prefixes:
            if command_str.startswith(prefix):
                return True
                
        return False


class FastAPIACPClient(Client):
    def __init__(self, workspace_root: Path, on_update_callback: Callable[[str], None]):
        self.workspace_root = workspace_root
        self.on_update_callback = on_update_callback
        self.terminals: Dict[str, asyncio.subprocess.Process] = {}
        self.terminal_buffers: Dict[str, bytes] = {}
        self.terminal_tasks: Dict[str, asyncio.Task] = {}
        self.output_buffer: List[str] = []
        self.pending_permissions: Dict[str, asyncio.Future] = {}

    def resolve_permission(self, request_id: str, approved: bool):
        fut = self.pending_permissions.get(request_id)
        if fut and not fut.done():
            fut.set_result(approved)

    async def read_text_file(
        self, session_id: str, path: str, line: int | None = None, limit: int | None = None, **kwargs: Any
    ) -> ReadTextFileResponse:
        full_path = self.workspace_root / path
        
        # Directory traversal jail check
        try:
            resolved_workspace = self.workspace_root.resolve()
            resolved_path = full_path.resolve()
        except Exception as e:
            raise RequestError.invalid_params(f"Invalid path: {str(e)}")

        if not str(resolved_path).startswith(str(resolved_workspace)):
            raise RequestError.invalid_params("Directory traversal attempt blocked.")

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            return ReadTextFileResponse(content=content)
        except Exception as e:
            raise RequestError.internal_error(str(e))

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        full_path = self.workspace_root / path
        
        # Directory traversal jail check
        try:
            resolved_workspace = self.workspace_root.resolve()
            resolved_path = full_path.resolve()
        except Exception as e:
            raise RequestError.invalid_params(f"Invalid path: {str(e)}")

        if not str(resolved_path).startswith(str(resolved_workspace)):
            raise RequestError.invalid_params("Directory traversal attempt blocked.")

        try:
            os.makedirs(full_path.parent, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return WriteTextFileResponse()
        except Exception as e:
            raise RequestError.internal_error(str(e))

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[PermissionOption], **kwargs: Any
    ) -> RequestPermissionResponse:
        policy_mgr = PermissionPolicyManager()
        tool_kind = getattr(tool_call, "kind", "other")
        logger.info(f"request_permission request received: tool_kind={tool_kind}, title={getattr(tool_call, 'title', None)}, raw_input={getattr(tool_call, 'raw_input', None)}")
        
        is_allowed = False
        details = ""
        
        if tool_kind == "execute":
            # Command execution
            cmd = ""
            args = []
            if isinstance(tool_call.raw_input, dict):
                cmd = tool_call.raw_input.get("command", "")
                args = tool_call.raw_input.get("args", [])
            elif hasattr(tool_call, "title") and tool_call.title:
                cmd = tool_call.title
                
            cmd_full = cmd
            if args:
                cmd_full = f"{cmd} " + " ".join(args)
                
            details = f"Command: {cmd_full}"
            is_allowed = policy_mgr.validate_command(cmd_full)
            
        elif tool_kind in ("edit", "read", "delete", "move"):
            # File system operations
            path_str = ""
            raw_in = getattr(tool_call, "raw_input", None)
            if isinstance(raw_in, str):
                try:
                    import json
                    raw_in = json.loads(raw_in)
                except Exception:
                    pass
            
            if isinstance(raw_in, dict):
                path_str = raw_in.get("path", "")
            
            if not path_str and tool_call.content:
                for item in tool_call.content:
                    if hasattr(item, "path"):
                        path_str = item.path
                        break
                    elif isinstance(item, dict) and "path" in item:
                        path_str = item["path"]
                        break

            if not path_str and getattr(tool_call, "title", None):
                import re
                match = re.search(r"([\w\-_\.\/]+\.[a-zA-Z0-9]+)", tool_call.title)
                if match:
                    path_str = match.group(1)
                        
            details = f"File {tool_kind}: {path_str}"
            
            # Directory traversal check
            is_traversal_safe = False
            try:
                resolved_workspace = self.workspace_root.resolve()
                p = Path(path_str)
                if p.is_absolute():
                    resolved_path = p.resolve()
                else:
                    resolved_path = (self.workspace_root / p).resolve()
                is_traversal_safe = str(resolved_path).startswith(str(resolved_workspace))
            except Exception:
                is_traversal_safe = False
                
            if not is_traversal_safe:
                logger.warning(f"Blocking directory traversal attempt in permission request: {path_str}")
                return RequestPermissionResponse(
                    outcome=DeniedOutcome(outcome="cancelled", message="Directory traversal blocked")
                )
                
            is_allowed = policy_mgr.validate_file_path(path_str, self.workspace_root)
            
        else:
            # Fallback auto-approve for other tool types
            details = f"Action: {tool_kind}"
            is_allowed = True
            
        if is_allowed:
            for opt in options:
                if opt.kind in ("allow_once", "allow_always"):
                    return RequestPermissionResponse(
                        outcome=AllowedOutcome(option_id=opt.option_id, outcome="selected")
                    )
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            
        # Strict policy mode -> fail immediately
        if policy_mgr.policy.get("policy_mode") == "strict":
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            
        # Interactive mode -> prompt user
        request_id = str(uuid.uuid4())
        fut = asyncio.Future()
        self.pending_permissions[request_id] = fut
        
        if self.on_update_callback:
            try:
                # Send the permission request over the websocket
                if asyncio.iscoroutinefunction(self.on_update_callback):
                    await self.on_update_callback({
                        "type": "permission_request",
                        "request_id": request_id,
                        "tool_call": tool_kind,
                        "details": details
                    })
                else:
                    self.on_update_callback({
                        "type": "permission_request",
                        "request_id": request_id,
                        "tool_call": tool_kind,
                        "details": details
                    })
            except Exception as e:
                logger.error(f"Error sending permission request: {e}")
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
                
        # Wait up to 60 seconds for approval response
        try:
            approved = await asyncio.wait_for(fut, timeout=60.0)
        except asyncio.TimeoutError:
            approved = False
        finally:
            self.pending_permissions.pop(request_id, None)
            
        if approved:
            for opt in options:
                if opt.kind in ("allow_once", "allow_always"):
                    return RequestPermissionResponse(
                        outcome=AllowedOutcome(option_id=opt.option_id, outcome="selected")
                    )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any
    ) -> CreateTerminalResponse:
        exec_cwd = self.workspace_root
        if cwd:
            exec_cwd = self.workspace_root / cwd

        cmd_str = command
        if args:
            cmd_str = f"{command} " + " ".join(args)

        process_env = os.environ.copy()
        if env:
            for item in env:
                process_env[item.name] = item.value

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd_str,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(exec_cwd),
                env=process_env
            )
            terminal_id = str(uuid.uuid4())
            self.terminals[terminal_id] = proc
            self.terminal_buffers[terminal_id] = b""
            
            task = asyncio.create_task(self._read_terminal_output(terminal_id, proc))
            self.terminal_tasks[terminal_id] = task

            return CreateTerminalResponse(terminal_id=terminal_id)
        except Exception as e:
            raise RequestError.internal_error(str(e))

    async def _read_terminal_output(self, terminal_id: str, proc: asyncio.subprocess.Process):
        async def read_stream(stream):
            while True:
                line = await stream.read(4096)
                if not line:
                    break
                self.terminal_buffers[terminal_id] += line
                
        await asyncio.gather(
            read_stream(proc.stdout),
            read_stream(proc.stderr)
        )

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> TerminalOutputResponse:
        if terminal_id not in self.terminals:
            raise RequestError.invalid_params(f"Terminal {terminal_id} not found")
        
        proc = self.terminals[terminal_id]
        buffer = self.terminal_buffers.get(terminal_id, b"")
        self.terminal_buffers[terminal_id] = b""
        
        decoded_output = buffer.decode("utf-8", errors="ignore")
        
        exit_status = None
        if proc.returncode is not None:
            exit_status = TerminalExitStatus(exit_code=proc.returncode)
            
        return TerminalOutputResponse(
            output=decoded_output,
            truncated=False,
            exit_status=exit_status
        )

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> WaitForTerminalExitResponse:
        if terminal_id not in self.terminals:
            raise RequestError.invalid_params(f"Terminal {terminal_id} not found")
        
        proc = self.terminals[terminal_id]
        await proc.wait()
        
        if terminal_id in self.terminal_tasks:
            self.terminal_tasks[terminal_id].cancel()
            
        return WaitForTerminalExitResponse(
            exit_code=proc.returncode,
            signal=None
        )

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> KillTerminalResponse | None:
        if terminal_id not in self.terminals:
            return None
        proc = self.terminals[terminal_id]
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return KillTerminalResponse()

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> ReleaseTerminalResponse | None:
        if terminal_id in self.terminals:
            proc = self.terminals[terminal_id]
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            self.terminals.pop(terminal_id)
        if terminal_id in self.terminal_buffers:
            self.terminal_buffers.pop(terminal_id)
        if terminal_id in self.terminal_tasks:
            self.terminal_tasks[terminal_id].cancel()
            self.terminal_tasks.pop(terminal_id)
        return ReleaseTerminalResponse()

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        content_text = ""
        if hasattr(update, "session_update"):
            u_type = update.session_update
            if u_type == "agent_thought_chunk":
                if hasattr(update.content, "text"):
                    logger.debug(f"OpenCode Thought: {update.content.text}")
            elif u_type == "agent_message_chunk":
                if hasattr(update.content, "text"):
                    content_text = update.content.text
            elif u_type == "tool_call":
                content_text = f"\n🛠️ Calling tool: {update.title or update.kind}..."
            elif u_type == "tool_call_update":
                if update.status == "completed":
                    content_text = f"\n✅ Tool call completed: {update.title or update.kind}"
                elif update.status == "failed":
                    content_text = f"\n❌ Tool call failed: {update.title or update.kind}"

        if content_text:
            self.output_buffer.append(content_text)
            accumulated_text = "".join(self.output_buffer)
            
            if self.on_update_callback:
                if asyncio.iscoroutinefunction(self.on_update_callback):
                    await self.on_update_callback(accumulated_text)
                else:
                    self.on_update_callback(accumulated_text)

def run_opencode_agent(app_id: str, instruction: str) -> str:
    """
    Invokes the OpenCode developer agent as a subprocess to create or modify a widget.
    Reads the OPENCODE_COMMAND from environment variables.
    (Kept for backwards compatibility / synchronous fallback).
    """
    opencode_cmd = os.getenv("OPENCODE_COMMAND", "opencode")
    workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
    apps_dir = os.getenv("APPS_DIR", os.path.join(workspace_dir, "apps"))
    target_dir = os.path.join(apps_dir, app_id)
    
    os.makedirs(target_dir, exist_ok=True)

    clean_instruction = instruction.replace('"', "'").replace('\n', ' ').replace('\r', '')
    from backend.agent.prompts.manager import PromptManager
    pm = PromptManager()
    prompt = pm.get_prompt(
        "opencode_system.md",
        app_id=app_id,
        target_dir=target_dir,
        instruction=clean_instruction
    )

    full_command = f'{opencode_cmd} run "{prompt}" --auto'
    
    logger.info(f"Executing OpenCode CLI agent: {full_command}")
    
    try:
        workspace_root = os.path.abspath(os.path.join(apps_dir, "..", ".."))
        process = subprocess.run(
            full_command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            cwd=workspace_root,
            timeout=300.0
        )
        stdout_output = process.stdout or ""
        stderr_output = process.stderr or ""
        
        combined_output = stdout_output
        if stderr_output.strip():
            combined_output += f"\n\n--- Standard Error ---\n{stderr_output}"
            
        if process.returncode != 0:
            combined_output = (
                f"OpenCode Agent exited with error code {process.returncode}.\n"
                f"Command output:\n{combined_output}"
            )
        return combined_output
    except Exception as err:
        return f"Failed to execute OpenCode Agent: {str(err)}"

async def run_opencode_agent_acp(app_id: str, instruction: str, on_update: Callable[[str], None]) -> str:
    """
    Spawns OpenCode agent in ACP mode, runs its loop, and streams the output/logs back via on_update callback.
    """
    opencode_cmd = os.getenv("OPENCODE_COMMAND", "opencode")
    
    # Resolve cmd suffix on Windows
    if os.name == "nt" and opencode_cmd == "opencode":
        resolved = shutil.which("opencode")
        if resolved:
            opencode_cmd = resolved

    workspace_dir = os.getenv("WORKSPACE_DIR", "workspace")
    apps_dir = os.getenv("APPS_DIR", os.path.join(workspace_dir, "apps"))
    target_dir = Path(apps_dir) / app_id
    
    os.makedirs(target_dir, exist_ok=True)
    
    client = FastAPIACPClient(workspace_root=target_dir, on_update_callback=on_update)
    workspace_root = os.path.abspath(os.path.join(apps_dir, "..", ".."))

    logger.info(f"Spawning OpenCode ACP agent: {opencode_cmd} acp inside {workspace_root}")

    try:
        async with spawn_agent_process(client, opencode_cmd, "acp", cwd=workspace_root) as (conn, proc):
            await conn.initialize(
                protocol_version=1,
                client_capabilities=ClientCapabilities(
                    fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
                    terminal=True
                )
            )
            
            session_resp = await conn.new_session(cwd=str(target_dir.absolute()))
            session_id = session_resp.session_id
            
            active_acp_clients[session_id] = client
            
            from backend.agent.prompts.manager import PromptManager
            pm = PromptManager()
            prompt_text = pm.get_prompt(
                "opencode_system.md",
                app_id=app_id,
                target_dir=str(target_dir.absolute()),
                instruction=instruction
            )
            
            try:
                await asyncio.wait_for(
                    conn.prompt(
                        session_id=session_id,
                        prompt=[text_block(prompt_text)]
                    ),
                    timeout=180.0  # 3 minutes timeout
                )
            except asyncio.TimeoutError:
                logger.warning(f"OpenCode ACP agent timed out after 180 seconds for app {app_id}.")
                client.output_buffer.append("\n⚠️ OpenCode Agent execution timed out after 3 minutes. The generated application files will be loaded from disk.")
            finally:
                active_acp_clients.pop(session_id, None)
            
            return "".join(client.output_buffer)
            
    except Exception as e:
        logger.error(f"Error running OpenCode ACP agent: {e}")
        return f"Failed to execute OpenCode ACP: {str(e)}"
