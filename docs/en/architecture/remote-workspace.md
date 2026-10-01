# Cloud entry and local workspaces

## Initial boundary

Ambient remains a user's exclusive local workspace and owns agents, apps, graph data, provider credentials and durable Runs. Ambient does not depend on an Agent Collaboration submodule. Cloud changes are developed on independent proposal branches and an isolated checkout for the original project to review. They reuse its Web account login and add a workspace entry and an independent Workspace Gateway. The cloud neither copies the entire workspace nor schedules Runs. The UI and default Widget Controller execute in the visiting browser.

The Connector only connects outbound after an explicit local action. Claiming a one-time code in the cloud requires a subsequent local confirmation of the account, scopes and expiry. Chat pairing does not grant workspace access. Each node has one account; an account may own multiple nodes. `workspace.control` grants the owner's workspace reads, writes and execution; separate `workspace.manage` grants provider, coding-agent, skill and capability administration. Local connection management is never remotely exposed.

## Identity and interfaces

The Gateway uses separate SQLite state for nodes, hashed one-time codes and device credentials, account bindings, grant expiry, short-lived browser sessions and metadata audit. It does not persist request or response bodies. Device credentials stay in the local private workspace and are never returned to browsers. The Web backend uses a dedicated `WORKSPACE_GATEWAY_SECRET` and derives account IDs from its existing authenticated session.

Gateway connector routes are `POST /v1/connector/pairings` (`name`, `scopes`, `expires_in`, returning `node_id`, `connector_token`, `pairing_code`, `pairing_expires_at`, `workspace_origin`), authenticated `GET /v1/connector/state`, `POST /v1/connector/approve`, `POST /v1/connector/revoke` and `WS /v1/connector/tunnel`. State includes status, claimed account, scopes, grant expiry and `grant_id`. Account-service routes are `POST /v1/accounts/{account_id}/pairings/claim` (`code`, `label`), `GET /v1/accounts/{account_id}/nodes`, `POST /v1/accounts/{account_id}/nodes/{node_id}/launch` (returning one-time `url`) and `DELETE /v1/accounts/{account_id}/nodes/{node_id}`. `DELETE /v1/accounts/{account_id}` permanently tombstones a deleted account and revokes every node before Web account deletion. Concurrent claims, approvals, and launches for that account are rejected. Claims are atomic; claimed owners cannot be replaced. Launch requires an approved online node.

Local routes under `/api/remote-workspace` are `GET /status`, `POST /pair` (`gateway_url`, `portal_url`, `name`, `scopes`, `expires_in`), `POST /approve` (`account_id`, `grant_id`) and `POST /revoke`. Approval must match the current claimed identity and grant. The expiry field is `expires_at`. They require a trusted local peer and mutations enforce the existing browser Origin allowlist. Status never discloses credentials. Internet URLs require HTTPS; explicit localhost/loopback HTTP is development-only. Local revocation removes authority and disconnects before attempting cloud notification. Transient network errors retain local state. Corrupt persisted credentials report invalid without connecting and permit a new explicit pairing from the local dialog.

## Channel contract

The Connector verifies `{type:"hello",node_id,grant_id,account_id,scopes,workspace_origin}` against its locally approved grant. Every Gateway request carries the same identity context, revalidated locally against expiry, scopes, target and path. Untrusted proxy headers cannot supply identity. Device secrets, cookies, authorization, Host and hop-by-hop headers are not forwarded. The upstream uses a fixed trusted local Origin without broadening Ambient's peer or Origin allowlists.

Bounded JSON carries HTTP bodies and binary WS frames as base64. `http.request` carries `id`, identity, `service` (frontend/backend/frame), `method`, `path` including query, header pairs and `body`. `http.response` carries `id`, `status`, header pairs and `body`. `ws.open` adds `subprotocols`; `ws.accept` returns the selected `subprotocol`. Bidirectional `ws.data` carries `id`, `kind` (text/bytes) and `data`; `ws.close` carries `id`, `code`, `reason`. Connector `ping` receives Gateway `pong`; revocation sends `revoked` and closes in-flight channels.

Fixed public Frame assets containing no workspace bodies travel as raw gzip. Only those responses may preserve `Content-Encoding: gzip`; the 2 MiB quota counts compressed wire bytes. Ordinary API/frontend quotas count decoded bodies.

The Connector defaults to fixed `127.0.0.1:5173/8000/8001` services. Process-start configuration `AMBIENT_REMOTE_UPSTREAM_MODE=docker` selects fixed Compose targets `frontend:5173`, `127.0.0.1:8000`, and `widget-frame:8001`. Other modes are rejected; browser/cloud messages cannot supply hostnames or URLs. Compose explicitly selects docker mode and native startup defaults to loopback.

HTTP bodies are limited to 2 MiB and WS frames to 256 KiB. Per node limits are 16 HTTP requests and 16 browser WS connections, with 30-second request timeouts and at most one-hour browser sessions capped by the local grant. Disconnect cancels in-flight requests and never blindly replays writes. Ambient's durable events and snapshots remain the recovery mechanism for Runs.

## Browser and Widgets

Each node has an independent origin: `<node_id>.localhost:<gateway-port>` locally and an explicitly configured wildcard TLS domain in production. A single-use launch ticket becomes a host-only HttpOnly SameSite=Lax cookie (Secure for HTTPS), followed by a redirect removing the ticket. Host must match the node; account service APIs are unavailable on workspace hosts. Portal cookies are never shared.

`/api/*` and `/ws/*` route to Backend; permitted UI resources route to Frontend. The Gateway injects non-secret `window.__AMBIENT_REMOTE__={apiBaseUrl:"/",nodeId}` into HTML. The Connector rewrites ticket `frame_url` to the node's `/_ambient/frame.html`. That document and fixed root `/frame_shell.css`, `/frame_shell.mjs`, `/controller_facade.mjs`, `/presentation_context.mjs`, `/vendor/*` resources route independently to Frame and support credential-free loading. Preserve opaque-origin sandboxing, CSP and Backend tickets; do not broaden Widget capabilities.

## Local acceptance

Use failing tests before implementation. Verify authenticated account isolation; one-time code/ticket replay rejection; cloud claim plus local confirmation; the full Ambient UI with real HTTP/WS transport; Widget shell/module/ticket handshake; management scope denial; target/path/header/size limits; expiry and revocation closing existing channels; offline state without Run resubmission; and persisted identity/grants after restart. Browser tests use synthetic accounts and isolated workspace data without real model calls. Record actual environments and do not claim production readiness from local evidence.

The initial Gateway is a bounded, testable outbound channel that can later be replaced behind the contract. Shared workspaces, cross-node merging, full cloud copies, P2P and production multi-replica routing are outside this first release.
