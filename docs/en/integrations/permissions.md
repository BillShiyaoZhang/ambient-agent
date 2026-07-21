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

## 5. Audit and sensitive data

- Every capability allow/deny records App, Manifest revision, category, operation, resource summary, and stable code, never file content, secrets, or a full upstream body.
- Run events use a versioned envelope with Run/session/step/attempt/trace correlation.
- Tool and adapter events redact sensitive arguments and bound size. LLM audit stores bounded previews, hashes, usage, and latency.
- Terminal Run events and LLM audit follow retention policy but remain sensitive workspace data.
- User approval never replaces least scope, schema validation, idempotency, fencing, compensation, or `needs_attention` reconciliation.
