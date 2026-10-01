# Connect a cloud platform and open your workspace remotely

## Usage

Run Ambient locally with its frontend, backend, and Widget Frame service. Open “Connect a cloud platform” in App Center system settings. Enter the operator-provided portal and connection URLs, computer name, expiry, and optional administration scope. Public endpoints require HTTPS; native localhost development may use HTTP.

Create a connection link, sign into the portal, and claim it. Return to the local Ambient window, verify the account, permissions, and expiry, then approve that account. Open the online node from the portal’s local workspace page. Keep the computer awake and online. The cloud provides entry and relay; agents, credentials, workspace data, and Runs stay local. The UI and default Widget Controller execute in the visiting browser.

Workspace operations and task execution are enabled by default. Administration is separately selected for models, coding agents, skills, and capabilities. Remote pages cannot create new pairings or change connection grants. A cloud claim requires local approval; pairing codes and launch links are single-use.

## Revocation and recovery

Local revocation stops local forwarding immediately even when the cloud is unavailable. Portal revocation invalidates existing workspace sessions and connections. Account deletion first permanently revokes its Gateway nodes and preserves the account if that fails. Expiry requires a fresh connection. Temporary network loss shows offline; the Connector reconnects and never blindly replays writes.

Workspace browser sessions last at most one hour and never exceed the local grant. Portal sign-out and already-opened workspace sessions are independent. Use revocation to stop remote access immediately. The relay can read traffic bodies; this implementation does not persist them. Existing chat RPC policy signatures do not cover the independent HTTP/WebSocket channel.

## Local development and proposal package

Ambient needs no Agent Collaboration submodule. Native startup selects fixed loopback ports 5173, 8000, and 8001; Compose selects fixed docker service targets. Connections are activated only by explicit pairing and remain offline when unconfigured.

Cloud changes live in the independent `.cache/agent-collaboration-proposal` checkout on `codex/ambient-workspace-proposal`. The original deploy checkout remains unchanged. Reviewable patches and Git bundles live under repository-root `proposals/agent-collaboration-deploy`; apply them to a fresh checkout using its README. The Web proposal reuses accounts, the deploy proposal adds a separate Gateway, and Platform/SDK stay unchanged.

For Gateway development set a dedicated `WORKSPACE_GATEWAY_SECRET`, isolated `WORKSPACE_GATEWAY_DATABASE`, `WORKSPACE_GATEWAY_DOMAIN=localhost:8788`, and `WORKSPACE_GATEWAY_SCHEME=http`. Run `uvicorn workspace_gateway.app:create_app --factory --host 127.0.0.1 --port 8788 --no-proxy-headers --no-access-log`. Configure Web with the same service secret, `WORKSPACE_GATEWAY_URL=http://127.0.0.1:8788`, an isolated database, and its own NextAuth URL/secret. The package excludes databases, device credentials, service secrets, and fixture accounts.

Production adoption requires a separate wildcard TLS domain, internal account-API isolation, redacted entry logs, service policy, global registration quotas, and state-store maintenance. One Gateway instance is supported. See the [architecture](../architecture/remote-workspace.md) and [local verification](../verification/remote-workspace.md). This proposal has not been deployed to a server.