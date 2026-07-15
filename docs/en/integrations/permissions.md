# Permissions & Auditing

Ambient Agent isolates card execution security and provides visual auditing of prompt contents.

---

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
    "agents": [
      "http://localhost:5000/agent/v1"
    ]
  }
}
```

---

## 2. In-flight Interception (`AppPermissionModal`)

When a widget requests an unlisted command:
1.  Backend creates an async future and suspends the execution thread.
2.  Frontend displays `AppPermissionModal` asking for manual approval.
3.  If approved, the backend appends the permission configuration to the JSON file and resumes execution.

---

## 3. LLM Audit Logs

Every model request payload and response is recorded in the SQLite database to prevent leakage. You can view raw prompt texts in the **Audit Log** sidebar panel.
