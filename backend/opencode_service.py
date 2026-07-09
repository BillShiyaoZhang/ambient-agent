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

UI_ELEMENTS_GUIDE = """
Here are the pre-configured custom UI web components and utility CSS classes available in our glassmorphism design system.
You are strongly encouraged to reuse these pre-existing components and styles instead of writing custom complex HTML/CSS structure where possible.

Custom Web Components:
1. <a-card title="Optional Title"> - A container card with glassmorphism style, subtle hover border, and glow effects.
2. <a-button variant="primary|secondary|danger" [loading] [disabled]> - Styled button.
3. <a-input placeholder="..." type="text|number|..." [value] [disabled]> - Styled input box. Exposes a `.value` property. Dispatches 'input' and 'change' events.
4. <a-select [value] [disabled]> - Styled dropdown select. Wrap standard <option> tags inside it. Exposes a `.value` property and dispatches 'change' events.
5. <a-badge variant="info|success|warning|danger"> - Status tag badge.
6. <a-table> - Table wrapper. Put standard table structure <thead>, <th>, <tbody>, <tr>, <td> inside it.
7. <a-chart type="line|bar" labels="comma,separated,labels" data="comma,separated,numeric,data"> - Light canvas-based chart. For example: <a-chart type="line" labels="Mon,Tue,Wed" data="12,19,3"></a-chart>

Utility CSS Classes available (along with full Tailwind CSS):
- .glass: Backdrop blur and thin semi-transparent border styling.
- .glass-card: Translucent background, box shadow, and purple glow on hover.
- .glass-input: Translucent styling for input fields.
- .glow-accent: Text shadow glow effect.

Please integrate these components and styles elegantly into the widget layout to maintain visual cohesion with the user workspace.
"""

class FastAPIACPClient(Client):
    def __init__(self, workspace_root: Path, on_update_callback: Callable[[str], None]):
        self.workspace_root = workspace_root
        self.on_update_callback = on_update_callback
        self.terminals: Dict[str, asyncio.subprocess.Process] = {}
        self.terminal_buffers: Dict[str, bytes] = {}
        self.terminal_tasks: Dict[str, asyncio.Task] = {}
        self.output_buffer: List[str] = []

    async def read_text_file(
        self, session_id: str, path: str, line: int | None = None, limit: int | None = None, **kwargs: Any
    ) -> ReadTextFileResponse:
        full_path = self.workspace_root / path
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
        # Auto approve permissions
        for opt in options:
            if opt.kind in ("allow_once", "allow_always"):
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(option_id=opt.option_id, outcome="selected")
                )
        return RequestPermissionResponse(
            outcome=DeniedOutcome(outcome="cancelled")
        )

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
    apps_dir = os.getenv("APPS_DIR", os.path.join("backend", "apps"))
    target_dir = os.path.join(apps_dir, app_id)
    
    os.makedirs(target_dir, exist_ok=True)

    clean_instruction = instruction.replace('"', "'").replace('\n', ' ').replace('\r', '')
    prompt = (
        f"You are modifying or creating the ambient widget app '{app_id}' located in the directory '{target_dir}'. "
        f"User request instruction: '{clean_instruction}'. "
        f"Please inspect the directory, check any existing source files there, apply the modifications directly to the files, "
        f"and save them back to '{target_dir}'. Ensure the code is functional, visually premium, and directly modifies those files. "
        f"Do not put any XML <ambient-widget> tags inside index.html, style.css, or controller.js themselves. Write only raw HTML, CSS, and JS.\n\n"
        f"{UI_ELEMENTS_GUIDE}"
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

    apps_dir = os.getenv("APPS_DIR", os.path.join("backend", "apps"))
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
            
            prompt_text = (
                f"You are modifying or creating the ambient widget app '{app_id}' located in the directory '{target_dir.absolute()}'. "
                f"User request instruction: '{instruction}'. "
                f"Please inspect the directory, check any existing source files there, apply the modifications directly to the files, "
                f"and save them back. Ensure the code is functional, visually premium, and directly modifies those files. "
                f"Do not put any XML <ambient-widget> tags inside index.html, style.css, or controller.js themselves. Write only raw HTML, CSS, and JS.\n\n"
                f"{UI_ELEMENTS_GUIDE}"
            )
            
            await conn.prompt(
                session_id=session_id,
                prompt=[text_block(prompt_text)]
            )
            
            return "".join(client.output_buffer)
            
    except Exception as e:
        logger.error(f"Error running OpenCode ACP agent: {e}")
        return f"Failed to execute OpenCode ACP: {str(e)}"
