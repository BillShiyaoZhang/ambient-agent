# Local remote-workspace verification

## Environment and isolation

2026-10-01, Windows, Python 3.13, Node 24, and Codex’s Chromium browser. Tests use an isolated Ambient workspace, independent proposal checkout, Gateway SQLite, and portal database under `.cache`; two synthetic accounts and no real model, email, or production platform calls. Original user data and cloud checkout remain unchanged. The test frontend uses 5174 because 5173 is occupied; backend 8000, Frame 8001, Gateway 8788, and Web 3310. Only fixture startup overrides the frontend port; product defaults remain 5173.

## Interaction evidence

The browser creates a one-hour local pairing, preserves its code through login, claims the node in the portal, then confirms the displayed account/scopes/expiry locally. The portal subsequently shows online. Unconfigured startup does not connect, and a cloud claim does not auto-approve. Screenshot of the approved synthetic account and default scope:

![Locally approved connection](../../verification/remote-workspace/local-approved.jpg)

The remote UI runs on an independent `<node_id>.localhost:8788` origin and uses same-origin API/WS. Real Frame shell/modules load, the Widget handshake succeeds, and clicking changes its count from 0 to 1. Chat displays connected; creating a conversation through the remote UI yields two locally stored sessions.

![Remote Widget interaction](../../verification/remote-workspace/remote-widget.jpg)

Local revocation invalidates the active Widget and chat connections. Portal refresh shows revoked with launch disabled. A native HTTP request for the node Host returns 401. Backend/Gateway restarts restore the approved identity without a fresh claim and do not replay writes.

![Active connection revoked](../../verification/remote-workspace/revoked-live.jpg)

## Automated verification

All 36 frontend files and 275 tests pass; production build and lint pass. Backend: 814 passed, 21 skipped; Connector/Compose: 35 passed; Ruff check and 144-file format check pass. Gateway: 22 passed; Widget facade/frame/server: 21 passed. Web full suite passes 397/397 in an isolated local Linux container; 41 related unit tests, production build, Next lint, and equivalent ESLint pass. Real HTTPS NextAuth/account/Origin isolation, wrong-password preservation of an approved grant, deletion-triggered Gateway revocation/old-cookie invalidation, and late approval rejection pass with logs in the isolated build directory.

Both incremental Git bundles pass verification and offline restoration in isolated checkouts containing only their original bases. The deploy proposal commit is `392deca500c7db906e7ba971ee04f258f1cc52e1` and Web is `fc271b6b4093fa23b334869d22c0edee11b27da4`; the parent Web gitlink matches the restored Web HEAD, while Platform/SDK versions remain unchanged. Cloud source and replay checkouts are clean. The original cloud checkout is unchanged, and Ambient contains no submodule. Exports, metadata, checksums, and restoration evidence live in repository-root `proposals/agent-collaboration-deploy`.

## Runtime limits

Locked LiteLLM 1.92.0 has no Windows wheel. Local regression uses the exact official source hash from uv.lock to build a test-only pure-Python wheel with its optional native bridge fallback. Dependency versions and lockfile remain unchanged; evidence lives in `.cache/dependency-build`. Production image installation remains unchanged.

The deploy repository’s historical structure check reports 92 old references to absent build evidence; new proposal documents introduce no broken links. Old evidence was not fabricated and assertions were not weakened. Windows Web suite has 396 passes and one existing POSIX 0600 permission failure. Its test and security implementation blobs match the base; the policy was not weakened and the full Linux suite passes. Ambient Compose and Gateway overlay receive configuration validation only; original containers were not started or changed. Browser tests use isolated native services. Local results do not verify public TLS, server ingress, or multi-replica operation.
