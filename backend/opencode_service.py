import os
import subprocess
import shlex
import logging

logger = logging.getLogger("opencode_service")

def run_opencode_agent(app_id: str, instruction: str) -> str:
    """
    Invokes the OpenCode developer agent as a subprocess to create or modify a widget.
    Reads the OPENCODE_COMMAND from environment variables.
    """
    opencode_cmd = os.getenv("OPENCODE_COMMAND", "opencode")
    apps_dir = os.getenv("APPS_DIR", os.path.join("backend", "apps"))
    target_dir = os.path.join(apps_dir, app_id)
    
    # Create the directory beforehand so OpenCode is clear on where to put it
    os.makedirs(target_dir, exist_ok=True)

    # Formulate a structured prompt for the agent
    # We strip out newlines and double quotes to prevent cmd.exe from truncating/mangling the prompt on Windows
    clean_instruction = instruction.replace('"', "'").replace('\n', ' ').replace('\r', '')
    prompt = (
        f"You are modifying or creating the ambient widget app '{app_id}' located in the directory '{target_dir}'. "
        f"User request instruction: '{clean_instruction}'. "
        f"Please inspect the directory, check any existing source files there, apply the modifications directly to the files, "
        f"and save them back to '{target_dir}'. Ensure the code is functional, visually premium, and directly modifies those files. "
        f"Do not put any XML <ambient-widget> tags inside index.html, style.css, or controller.js themselves. Write only raw HTML, CSS, and JS."
    )

    # Construct the full execution command
    full_command = f'{opencode_cmd} run "{prompt}" --auto'
    
    logger.info(f"Executing OpenCode CLI agent: {full_command}")
    
    try:
        # Resolve workspace root directory to run in
        workspace_root = os.path.abspath(os.path.join(apps_dir, "..", ".."))
        
        # Run command synchronously and capture stdout/stderr
        process = subprocess.run(
            full_command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            cwd=workspace_root,
            timeout=300.0  # 5 minutes timeout for agent task
        )
        
        stdout_output = process.stdout or ""
        stderr_output = process.stderr or ""
        
        # Combine output
        combined_output = stdout_output
        if stderr_output.strip():
            combined_output += f"\n\n--- Standard Error ---\n{stderr_output}"
            
        if process.returncode != 0:
            combined_output = (
                f"OpenCode Agent exited with error code {process.returncode}.\n"
                f"Command output:\n{combined_output}"
            )
            
        return combined_output
        
    except subprocess.TimeoutExpired as e:
        logger.error(f"OpenCode Agent timed out after 300 seconds.")
        return f"Timeout Error: OpenCode Agent execution timed out after 5 minutes.\nPartial output:\n{e.stdout or ''}"
    except Exception as err:
        logger.error(f"Failed to run OpenCode Agent: {err}")
        return f"Failed to execute OpenCode Agent: {str(err)}"
