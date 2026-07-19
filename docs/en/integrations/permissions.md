# Permissions, Execution Boundaries, and Auditing

Ambient Agent uses layered enforcement rather than one global “approved” flag. Local model tools, MCP spawn, remote Agents, OpenCode ACP, and browser Widgets have different security properties.

## 1. Local model tools: Tool Gateway

Model-requested Python tools execute through `ToolGateway`. Each `ToolSpec` declares:

- typed input and output schemas;
- a `read`, `write`, `delete`, `execute`, or `network` effect;
- required scopes and a `never/always` approval policy;
- timeout, maximum output bytes, idempotency-key requirement, and sensitive argument fields.

The gateway fails closed on unregistered tools, unknown/invalid arguments, insufficient scope, missing approval, and a missing idempotency key. Tool events redact fields declared sensitive. Ordinary Converse currently exposes read-only tools.

The current idempotency result cache is process-local. It prevents duplicates within one process but is not a restart-durable exactly-once ledger. Side-effecting durable workflows still need persistent idempotency keys and transactional or compensating behavior underneath.

## 2. MCP and remote-Agent permission

`workspace/backend_permissions.json` stores approved identities per App. A new MCP identity includes exact `command`, `args`, a SHA-256 digest of the explicit environment, and the manifest revision. Changing any field requires approval again. Remote Agents are currently approved by their complete endpoint URL.

An unapproved capability, MCP tool/resource, or Agent Run creates a durable Run interaction before execution. Resolution atomically records the response with `run_version` and requeues the Run. Permission state does not depend on a global Future and survives backend restart.

Spawn approval allows one runtime identity to start. It does not authorize every MCP tool automatically. Capability actions pin a tool name in the manifest; client-submitted MCP tool Runs persist the concrete tool name but do not yet build a dynamic allowlist from `tools/list`.

## 3. OpenCode ACP

OpenCode works only in a per-Run sibling staging App:

1. `app_id` is validated first, and live/staging paths must be direct children of the Apps directory.
2. `..`, outside paths, symlinks/junctions, and unsafe links inside an existing App are rejected.
3. Execution uses `create_subprocess_exec(argv)`, never a shell; shell-control syntax in command/args is rejected.
4. Terminal cwd must remain in staging. The environment inherits a small allowlist and restricts request-injected variables.
5. Command policy compares exact/prefix argv tokens and retains a blocklist. Unknown ACP tool kinds are denied by default.
6. Combined output is byte-bounded. Subprocesses run in a separate process group and stop through TERM→KILL escalation.
7. `promote` atomically replaces the live App only after artifact/schema verification passes or the user explicitly overrides findings. Failure or rework discards staging.

The production `opencode_permissions.json` uses strict exact-argv policy:

```json
{
  "policy_mode": "strict",
  "files": {
    "allowed_extensions": [".js", ".json", ".md"],
    "allowed_filenames": ["controller.js", "manifest.json", "README.md"]
  },
  "commands": {
    "allowed_commands": ["npm test", "npm run build"],
    "allowed_prefixes": [],
    "blocklist": ["rm -rf", "curl", "wget", "sudo"]
  }
}
```

Commands are parsed into a fixed executable and argv tokens and executed with `create_subprocess_exec()`. Production policy permits neither prefixes nor arbitrary terminal/network access. An out-of-policy ACP request fails closed instead of holding a durable worker on process-local approval.

## 4. Audit and sensitive data

- Run events have a versioned envelope with Run/session/step/attempt/trace correlation.
- Tool Gateway events record redacted arguments, effect, status, and output size.
- `LLMAuditLog` stores bounded prompt/response previews redacted from tool schemas, plus hashes, usage, latency, and trace correlation.

Run events recursively redact conventional secret keys, bound payload size, and mark changed content with `redacted`. Terminal Run events and LLM audit default to 30-day retention, configured with `RUN_EVENT_RETENTION_DAYS` and `AGENT_AUDIT_RETENTION_DAYS`. They remain sensitive workspace data and must not be exposed to untrusted users.

## 5. What is not a strong sandbox

- OpenCode path, argv, environment, process, and staging controls reduce risk but provide no OS-level filesystem/network isolation.
- MCP subprocesses likewise have no default network denial or separate user/container boundary.
- A browser Widget's `new Function` is a module-loading mechanism, not a hostile-code security boundary.
- User approval does not replace enforced authorization, least-privilege scopes, idempotency, or effect auditing.
