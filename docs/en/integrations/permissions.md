# Permissions & Auditing

Ambient Agent isolates card execution security and provides visual auditing of prompt contents.

## 1. Permission File `backend_permissions.json`

Stored under `workspace/backend_permissions.json`, it defines white-listed system calls per `app_id`:

```json
{
  "weather-app": {
    "mcp_servers": [
      {
        "command": ["python", "-m", "mcp_weather"],
        "args": ["--port", "9000"]
      }
    ],
    "agents": ["http://localhost:5000/agent/v1"]
  }
}
```

## 2. In-flight Interception (`AppPermissionModal`)

When a widget requests an unlisted command:

1.  Backend creates an async future and suspends the execution thread.
2.  Frontend displays `AppPermissionModal` asking for manual approval.
3.  If approved, the backend appends the permission configuration to the JSON file and resumes execution.

## 3. LLM Audit Logs

Every model request payload and response is recorded in the SQLite database to prevent leakage. You can view raw prompt texts in the **Audit Log** sidebar panel.

## 4. OpenCode Agent Security Policies `opencode_permissions.json`

Beyond widget runtime API permissions, the platform integrates the OpenCode developer agent to automatically compile and generate widget code. To guarantee host security, file access and command execution permissions are governed by the policy file `backend/opencode_permissions.json`.

### Configuration Structure

```json
{
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
```

### Policy Properties

- `policy_mode`: Configuration mode (e.g. `"interactive"`). In interactive mode, when the agent attempts an unlisted command or file write, the execution suspends and a permission request is broadcast to the frontend for human approval.
- `files`: Restricts file read/write access to specified `allowed_extensions` and `allowed_filenames`. Strict workspace directory traversal checks (Jail Checks) are enforced at the API level.
- `commands`:
  - `allowed_commands`: List of terminal commands allowed for exact matching execution.
  - `allowed_prefixes`: List of allowed command prefixes.
  - `blocklist`: List of blacklisted command substrings (e.g., destructive actions or unauthorized downloads) that are strictly blocked.
