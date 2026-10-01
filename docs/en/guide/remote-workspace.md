# Connect a cloud platform and open your workspace remotely

## Usage

First sign into the portal and explicitly create an enrollment code on its local workspaces page. The account-bound code expires after five minutes and can be consumed once. Run Ambient locally with its frontend, backend, and Widget Frame service. Open “Connect a cloud platform” in App Center system settings. Enter the operator-provided portal and connection URLs, enrollment code, computer name, expiry, and optional administration scope. Public endpoints require HTTPS; native localhost development may use HTTP.

Create a connection link and claim it using the same account that issued the enrollment code. Return to the local Ambient window, verify the account, permissions, and expiry, then approve that account. Enrollment and cloud claim never automatically allow access; a pending account ID does not imply approval. Open the online node from the portal’s local workspace page. Keep the computer awake and online. The cloud provides entry and relay; agents, credentials, workspace data, and Runs stay local. The UI and default Widget Controller execute in the visiting browser.

Workspace operations and task execution are enabled by default. Administration is separately selected for models, coding agents, skills, and capabilities. Remote pages cannot create new pairings or change connection grants. A cloud claim requires local approval; pairing codes and launch links are single-use.

Enrollment codes exist only for the current submission and are cleared after every attempt or when the dialog closes. They are absent from saved state, browser storage, URLs, and logs. Generate a fresh code in the portal if it expires or is used. Respect the displayed wait before manually retrying capacity errors. A lost pairing response never automatically creates another node. Existing paired identities and grants survive upgrade; old unapproved connections require fresh enrollment.

## Revocation and recovery

The duration selector also offers explicit “Until revoked”. It continues access for the selected account under the original scopes until local or platform revocation; local account review displays this duration again. A supporting Cloud Gateway is required. The client checks capability first, so unsupported old Gateways receive no pairing POST and consume no enrollment; select a bounded duration instead. Existing connections retain their deadlines and never become long-lived through an upgrade. Browser sessions still last at most one hour; reopen an authorized node from Portal after session expiry.

Local revocation stops local forwarding immediately even when the cloud is unavailable. Portal revocation invalidates existing workspace sessions and connections. Account deletion first permanently revokes its Gateway nodes and preserves the account if that fails. Expiry requires a fresh connection. Temporary network loss shows offline; the Connector reconnects and never blindly replays writes.

Workspace browser sessions last at most one hour and never exceed the local grant. Portal sign-out and already-opened workspace sessions are independent. Use revocation to stop remote access immediately. The relay can read traffic bodies; this implementation does not persist them. Existing chat RPC policy signatures do not cover the independent HTTP/WebSocket channel.

## Local development and proposal package

Ambient needs no Agent Collaboration submodule. Native startup selects fixed loopback ports 5173, 8000, and 8001; Compose selects fixed docker service targets. Connections are activated only by explicit pairing and remain offline when unconfigured.

The current client follows the cloud [2026-10-01 handoff contract](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/blob/d6dd823f1b379440616a2dc2e866ce9d9bac7729/docs/developers/AMBIENT_WORKSPACE_HANDOFF_2026-10-01.md). Fetch it with `git clone --branch codex/ambient-workspace-review-20261001 --recurse-submodules https://github.com/BillShiyaoZhang/agent-collaboration-deploy.git cloud-workspace-review`. This review pins Deploy `d6dd823f1b379440616a2dc2e866ce9d9bac7729` and Web `fa55096cc66f88f17f1b6191a5bc41d046cd08e8`. Original cloud checkouts remain unchanged, and Ambient needs no submodule. Repository-root `proposals/agent-collaboration-deploy` retains the first proposal's historical patches/bundles; it does not replace the current enrollment-aware cloud version.

Install isolated dependencies following the README in the independent cloud review checkout's `workspace-gateway` directory. This complete native development example uses `http://localhost:3000` for the portal and `http://localhost:8090` for Gateway while Ambient keeps its fixed native ports. The Gateway launcher listens on `0.0.0.0:8090`; use localhost for local access and the public control origin for devices.

| Variable | Gateway | Web |
| --- | --- | --- |
| `WORKSPACE_GATEWAY_SECRET` | Dedicated random service secret, at least 32 characters | The same secret, server-side BFF only |
| `WORKSPACE_GATEWAY_DATABASE` | A new isolated absolute SQLite path | Not used; keep a separate portal database configuration |
| `WORKSPACE_GATEWAY_DOMAIN` | `localhost:8090` | `localhost:8090` |
| `WORKSPACE_GATEWAY_SCHEME` | `http` | Not used |
| `WORKSPACE_GATEWAY_PUBLIC_URL` | `http://localhost:8090` | `http://localhost:8090`, the public control address shown to users |
| `WORKSPACE_GATEWAY_CONTROL_HOST` | `localhost:8090` | Not used |
| `WORKSPACE_GATEWAY_SERVICE_HOST` | `127.0.0.1:8090`, permitting that internal BFF Host for account/metrics routes | Not used |
| `WORKSPACE_GATEWAY_PORTAL_ORIGIN` | `http://localhost:3000` | Not used |
| `WORKSPACE_GATEWAY_URL` | Not used | `http://127.0.0.1:8090`, internal BFF requests only |
| `NEXTAUTH_URL` / `NEXTAUTH_SECRET` | Not used; set the portal origin above | `http://localhost:3000` / a dedicated private secret |

From the Gateway directory run its `python -m workspace_gateway.launcher` in the isolated environment so transport queues and concurrency match application budgets. With this example, enter `http://localhost:3000` as Ambient's portal URL and `http://localhost:8090` as its connection URL. An internal `127.0.0.1` BFF URL is not a public Connector address. Account routes require the configured control/service Host plus service Bearer; device and health routes require the public control Host. Compose BFF uses `workspace-gateway:8090` and the overlay's corresponding SERVICE_HOST. The package excludes databases, device credentials, service secrets, and fixture accounts.

Production requires an exact HTTPS CONTROL_HOST / PUBLIC_URL, a separate node wildcard domain, TLS, and internal account-API isolation. Portal and workspaces must use different registrable domains; sibling subdomains of one site are insufficient. For example, use `portal.example.com`, `connect.example-workspace.com`, and `*.nodes.example-workspace.com`; replace placeholders and pass the cloud branch's PSL, certificate, and ingress checks. Also validate redacted logs, service policy, global admission quotas, state maintenance, and real workload RSS. One Gateway instance is supported. Public TLS/DNS, rejection of forged Host/proxy headers, inaccessible account APIs, and real multi-WS load require independent environment verification.

See the [architecture](../architecture/remote-workspace.md), this handoff's [2026-10-01 verification](../verification/remote-workspace-handoff-2026-10-01.md), and the preserved [historical first-release verification](../verification/remote-workspace.md). This work has not been deployed to a server.
