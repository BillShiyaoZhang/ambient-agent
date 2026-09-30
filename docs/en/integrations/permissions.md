# Permissions, Execution Boundaries, and Audit

Ambient Agent uses composable policy layers rather than one global “approved” boolean. Widget grants, local model tools, Capability actions, MCP/remote-Agent runtimes, and Coding Agents answer different questions. Approval in an outer layer never weakens an inner constraint.

## 1. Widget Capability Grants

Widget authority is approved together with data schemas in the schema-alignment interaction and persisted as exact Manifest V2 grants. `CapabilityAuthorizer` defaults to deny and checks App, category, operation, and resource for every Graph, network, file, and installed-capability adapter operation.

When Manifest revision or grants digest changes, an old SDK snapshot cannot continue calling. Hiding a frontend method is not authorization; the backend trusts only the current persistent Manifest. See [Widget Capability Security](/en/architecture/capability-security.md) for the full model.

## 2. Local model tools: Tool Gateway

`ToolGateway` executes model-requested Python tools. Every `ToolSpec` defines typed input/output schemas, effect, required scopes, approval policy, timeout, output limit, idempotency requirement, and sensitive fields.

The Gateway rejects unregistered tools, unknown arguments, insufficient scope, missing approval, and missing idempotency keys, and redacts tool events. Converse receives only read tools projected from the [Agent System Capability Catalog](/en/agent/system-capabilities.md). Effectful workflows use a durable effect ledger rather than an in-process result cache.

## 3. Installed Capabilities, MCP, and remote Agents

A Widget uses only exact `catalog_id + action_id` pairs in its `capability.invoke` grant. The Capability Manifest then fixes the invocation adapter, input/result schemas, and recovery policy. An MCP runtime identity still includes command, arguments, explicit-environment digest, and Manifest revision; changes require a new durable Run interaction.

A Widget grant is neither MCP spawn approval nor arbitrary-tool approval. A call passes, in order: Widget grant → Capability action schema → adapter runtime identity permission → protocol capability/tool policy → Run effect/recovery policy. The new version does not accept direct Widget `mcp_call_tool` submissions.

## 4. Coding Agents

A Coding Agent works only in a per-Run staging App:

1. Paths are safe direct children of the Apps root; escapes and symlinks are rejected.
2. Only `controller.js`, `manifest.json`, and `README.md` are allowed.
3. Terminals use fixed argv with `create_subprocess_exec()`, never a shell.
4. Cwd is fixed to staging and the environment uses a small allowlist.
5. stdout/stderr, wall time, and process groups are bounded.
6. The prompt receives only the approved Runtime Contract.
7. Verification requires equal Manifest grants and subset code use before atomic promotion.

Path/argv/environment/staging policy reduces risk but is not full OS network/filesystem isolation. It never replaces the Widget runtime authorizer.

Windows subprocesses retain the non-secret OS variables required by Node, the system shell, and native CLIs: `SYSTEMROOT`, `SYSTEMDRIVE`, `COMSPEC`, `PATHEXT`, `USERPROFILE`, and `WINDIR`. They do not expand tool or file grants or pass Ambient Provider credentials to native Codex.

ACP file reads and writes preserve the original UTF-8 LF/CRLF text without platform newline conversion. File-change approval compares the UTF-8 SHA-256 of `oldText` with current raw file bytes. A newline change is also a modification; stale hashes, incorrect paths, and reused change evidence remain denied.

Each ACP prompt includes bounded snapshots of the three allowed artifacts; new Apps explicitly mark those files as absent. Coding Agents must use native `apply_patch` or an actually exposed ACP file tool to write allowed files, never shell reads, directory scans, or filesystem permission bypasses. The host verifies artifacts. Oversized files are not presented as complete truncated content; the agent must report missing safe editing context.

ACP startup failures retain bounded stderr diagnostics; startup and protocol exception messages use the same redaction boundary before returning. Remove control sequences and identify credential fields, Bearer tokens, and URL credentials before replacing known environment secrets, so a short token cannot corrupt field names and expose other credentials. Ordinary OS environment values are not secrets and must not be hidden wholesale. Retained stderr and returned diagnostics have separate limits; raw stderr, secret-bearing exception text, and complete environments must never enter logs, Runs, or the UI.

MCP stop and failure cleanup must wait for the child to exit and close every stdin, stdout, and stderr pipe transport while its owning event loop is still running. Malformed or oversized responses can stop stdout consumption, so waiting for the PID or cancelling readers alone is insufficient. These failures require the same complete cleanup without increasing output limits or changing failure of all pending requests.

Coding Agent version and login-status probes retain their five-second deadline. A timed-out probe returns the existing failure result and caller cancellation still propagates; before returning or propagating, terminate a surviving probe child and await its exit while draining and closing stdout/stderr pipes in the current event loop. Successful probes retain their exit code and bounded cleaned output without expanding CLI environment or execution authority.

## 5. Audit and sensitive data

- Integrated Run effect, Tool Gateway, adapter, and LLM paths record events according to their contracts. `CapabilityAuthorizer`, App file operations, and Graph queries do not yet record every capability allow/deny through a shared hook; complete access auditing remains unimplemented. New hooks should record only App, Manifest revision, category, operation, resource summary, and stable code, never file content, secrets, or a full upstream body.
- Run events use a versioned envelope with Run/session/step/attempt/trace correlation.
- Tool and adapter events redact sensitive arguments and bound size. LLM audit stores bounded previews, hashes, usage, and latency.
- Terminal Run events and LLM audit follow retention policy but remain sensitive workspace data.
- User approval never replaces least scope, schema validation, idempotency, fencing, compensation, or `needs_attention` reconciliation.
