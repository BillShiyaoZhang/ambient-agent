# Public workspace runtime acceptance

## Current conclusion

The actual public acceptance run on 2026-10-01 establishes the core workflow: real-account pairing and local approval, public Widgets and Chat, local native `gpt-5.6-luna` primary inference and Codex code generation, App verification and publication, an active local Run completing while its Cloud connection is stopped, recovery under the same authorization, and both Portal and local revocation. The generated App actually performs `READY → DONE → READY` in its public Frame. The final authorization is actively revoked, persisted credentials are cleared, and the isolated services and login copy are removed.

This conclusion covers the directly observed core behavior below. The ten acceptance scenarios also contain Cookie attributes, old-Cookie 401, real-browser security negatives, in-memory runner/poll counts, five automatic retry counts, and Native internal-request counts that were not all directly captured. The entire matrix is therefore not marked passed. Current capacity also does not pass a second complete active page or two parallel App Frames.

Final source regression for this Ambient change passes **995 backend tests with 22 skips** and **292 frontend tests**, plus build, lint, Ruff, and paired UML checks. GitHub `main` publication remains pending when this document is consolidated; local tests do not establish a push. Ambient stays on `main` without a submodule. This run does not rewrite the Cloud project or deploy production Cloud services.

## Tested topology and isolation

The actual Portal is `https://agent-communication.online`, control origin is `https://gateway.workspace.agent-communication.online`, and node origin is `https://<24hex>.workspace.agent-communication.online`. Read-only runtime observations confirm explicitly selected `WORKSPACE_GATEWAY_ORIGIN_MODE=same-site-subdomains`, application reservations `WORKSPACE_GATEWAY_BUFFER_BYTES=268435456` (256 MiB), and Gateway container limits of 384 MiB/0.5 CPU. Default `separate-site` and full PSL validation remain; TLS, hostname checks, and Portal protections are not weakened for acceptance.

The Cloud [temporary-subdomain release record](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/blob/main/docs/releases/WORKSPACE_TEMPORARY_SUBDOMAIN_2026-10-01.md) matches actual observations: Web revision [4abb3f34331f2d26be411cf0af60bd3827b26314](https://github.com/BillShiyaoZhang/agent-collaboration-web/tree/4abb3f34331f2d26be411cf0af60bd3827b26314), image `sha256:c974855860cf9ef173bca1cc3df765dc4fbeaa9507f192652c426e1705b9b2dd`; Gateway revision [152dea974f09e5dbca4d0da16867df1be9d3395e](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/152dea974f09e5dbca4d0da16867df1be9d3395e), image `sha256:bb265785e5d1c446f599712d3a06f9e028bae9d22b4d9e7659a653b688b1de8f`. Ambient starts from published baseline [793da83ac263aebe26dc989d5e025d8b7c7c01de](https://github.com/BillShiyaoZhang/ambient-agent/tree/793da83ac263aebe26dc989d5e025d8b7c7c01de); native primary integration and compatibility fixes are subsequently implemented and verified locally.

Local execution uses an independent synthetic workspace `.cache/ambient-public-e2e-20261001/workspace` and independent Neo4j. The actual adapter is `Neo4jGraphDatabase`, `RETURN 1` succeeds, and Neo4j listens only on loopback 9747/9768. Complete Runtime execution uses supported Linux Docker topology, fixed Docker upstreams, a Unix socket, and smoke=1. The Runtime socket is 0660 and an actual unauthorized uid 65534 receives EACCES. The initial controller App's real first-frame smoke passes without inference or App promotion.

Only the identity-checked test node and owned services are operated. Human authorization is control-only, excludes model/Coding Agent/skill administration, and lasts at most one hour per grant. Web model configuration uses a separate local administration context. Existing Hermes, the original workspace, and business data are not test targets. Isolated Linux Codex uses a login copy in its owned state directory; that copy is removed, while the original host login file remains and its content is neither inspected nor emitted. Screenshots and safe metadata remain in ignored observations. Accounts, email, enrollment codes, credentials, Cookies, launch tickets, model keys, raw requests, and business-reply bodies are not committed.

The previous [2026-10-01 handoff acceptance](remote-workspace-handoff-2026-10-01.md) remains historical Windows/loopback/synthetic-account/SQLite-test-adapter/no-inference evidence, not a substitute for actual HTTPS, Neo4j, model, or durable Run results. Times below are **UTC on 2026-10-01**; Asia/Shanghai is eight hours later.

## Acceptance matrix and evidence scope

| No. | Scenario and contract | Direct result in this run | Details not directly established |
| --- | --- | --- | --- |
| 1 | Real account creates a code, local pairing and same-account claim occur, scope/deadline are checked before approval; opening is denied before approval | Normal authorization chains are observed in rounds two through four, with individual submissions, Local Connected, and Portal Online. The first pending grant's expiry remains expected history. The fourth grant supports actual models/App/recovery and is finally revoked | No additional wrong-account or enrollment replay is constructed in production; separate local regression covers these boundaries |
| 2 | HTTPS launch consumes the ticket and cleans the URL; node sessions use prescribed Cookie attributes | Portal actually opens the node with no ticket in the URL; clean-URL reload succeeds under a valid grant without another launch | Pinned Cloud source and local regression support `__Host-ambient_workspace`, Secure, HttpOnly, Lax, Path=/, and no Domain. The in-app browser provides no independent Set-Cookie metadata, so individual attributes are not directly captured here |
| 3 | Temporary same-site mode denies cross-origin node→Portal fetch/form/iframe and prevents account replacement | Of 11 actual HTTPS GET probes without Cookie/Auth, eight synthetic node→Portal Fetch Metadata requests return 403. Portal login, claim, and address-bar return work | Synthetic metadata establishes server responses, not real-browser security negatives with real Cookies, ordinary-link navigation, or complete account-replacement prevention |
| 4 | Isolated Frame, one-time Runtime handshake, and real Widget interaction | The original controller performs Count 0→1 and interaction after over 75 seconds idle; the generated public Frame displays READY, then DONE and READY after two Toggles | Modules/vendor/CSP/CORS/handshake headers are not independently captured item by item. The new App's smoke is implied by its workflow gate; its direct smoke result is not separately captured. Public rendering and interaction are separate direct evidence |
| 5 | chat/runs/run-live/client-runtime WSS are active together and remain usable after idle | Actual Cloud snapshots show WS=4/Tunnel=1; the controller responds after more than nginx's 75-second read timeout. Actual model and generated-App single pages later also have four WS | Snapshots and tested interactions do not cover every load or arbitrary idle duration |
| 6 | Actual local models, durable Run/final message, and App generation/verification/publication | Native Model and Test tools succeed; one public short Chat submission returns its expected Receipt and one succeeded Run. A new controlled App Run succeeds with exact Luna snapshots and completed generation, verification, and promotion | The first App's AlignData failure remains, with cause unproven. Native internal model-request counts are unavailable; one legitimate workflow autoRepair increases the model budget, so zero extra model requests is not claimed |
| 7 | Stop only the owned Connector; active local Run continues; recovery preserves identity/grant without request replay | Connector is stopped for about 49 seconds; the same Run succeeds before restoration. Recovery preserves event/message/step prefixes, model snapshots, and counts, with no additional Run/route/user/final message; explicit connection/Widget Retry restores generated-App interaction | Five actual automatic retries are not independently counted; container-scoped process snapshots do not independently bind a process to a Run |
| 8 | Portal and local revocation disable authorization, close forwarding, and clear credentials, including unavailable Cloud notification | Both revocation paths actually finish. Cloud WS/Tunnel/HTTP become zero; API and persisted state are revoked with cleared credentials and approved=false. The fourth grant is actively revoked before its deadline | Reload after revocation retains old DOM; old-Cookie HTTP/main-navigation 401 is not independently captured. In-memory runner and Cloud poll-request counts are unavailable. Source cancellation plus resource release supports forwarding termination, not those direct counts |
| 9 | control-only permits Chat/Widgets and denies administration; public control Host exposes no account APIs/metrics | Public Provider mutation returns 403 and original Provider count stays zero; remote-connection administration entry is absent. Anonymous control-Host account API/metrics return 404 and node access returns 401 | Every management API, Host-misclassification case, and real-browser attack negative is not marked directly passed; source/local regression scope remains separate |
| 10 | Budget denial is bounded, closing test connections releases resources, and the original node remains usable | One four-WS page, actual second-page 429, and original controller interaction after closing that page are observed. Final revocation releases resources; all observations have OOMKilled=false/restart_count=0 | A second complete page and two parallel App Frames do not pass; multi-user/multi-node/full-queue/sustained-model capacity is untested |

Local coverage remains for authorization expiry, Cookie-session bounds, ticket/enrollment replay, wrong-account non-consumption, in-flight revocation, and no single-write replay. `scripts/verify_remote_workspace_handoff.py` uses a real loopback Tunnel and echo fixtures; it does not replace this run's TLS, nginx, Widgets, models, or durable Run observations.

## Actual execution, recovery, and revocation

### Web model configuration and actual execution

Under human authorization, the local web UI adds `Codex Native · local` and selects exact `gpt-5.6-luna` for default, fast, and the Codex Coding Agent's native model, without entering an API key or endpoint. Actual primary Provider Model and Test tools checks succeed. One public short Chat is submitted once, returns expected `AMBIENT_REMOTE_MODEL_OK`, and persists one succeeded Run. Its snapshots independently confirm exact Luna primary/fast/coding-native routing.

Ambient's native primary Provider uses pinned Linux Codex 0.145. The Windows host's separate 0.159.2 probe, login-status, catalog, and Coding Agent web configuration are not primary Provider execution proof. The new controlled App passes plan/data stages, actually enters Codex generation, verification, and publication, and creates independent `Remote Model Receipt 2` without modifying the original controller App.

### Active Run disconnect and recovery

| UTC time | Direct observation |
| --- | --- |
| 14:52:20 | New Run waits for a user, with 32 events/latest sequence 73, one Run user message, three cumulative workspace Runs/three audits, model budget3, route attempt1, and saved exact Luna snapshots |
| 14:52:47 | The same Run's stage_code is RUNNING. Its owned container contains one Codex app-server, one ACP bridge, and zero Native Primary processes; scanning is container-scoped without an independent process→Run binding |
| 14:52:52 | Owned IPC stops only the Connector, preserving node identity, grant, credentials, approval, and expiry; the local Run and Coding Agent remain running |
| 14:52:56 | Same Run remains RUNNING with 42 events/latest sequence83. Saved event/message/step prefixes, models, Run counts, and route attempts remain preserved |
| 14:53:36 | Before Connector restoration, the same Run is already SUCCEEDED and Codex/ACP process counts are zero, directly establishing completion of an active local Run during Cloud disconnection |
| 14:53:41 | Original Connector starts after about 49 seconds offline with preserved identity/grant/credentials/authorization. Its start ACK itself has online=false and is not treated as reconnection proof; subsequent public recovery is observed separately |
| 14:53:45 | Recovered snapshot has 64 events/latest sequence105, the same one user plus one final agent message, three cumulative Runs/three audits, route attempt1, and generation/promote/verify=true, preserving durable prefixes and models |
| 14:58:02 | Terminal recheck remains the same succeeded Run with 64 events, three Runs/three audits, one user plus one final agent message, and unchanged prefixes/counts. The generated public App has actually performed READY→DONE→READY |

Model budget rises from three to four for one legitimate workflow autoRepair correcting the generated manifest's `app_spec`; this is not a Run replay caused by reconnection. Public UI shows Completed/Publish and one repair. Native underlying-request counts remain null, so budget/audit counts cannot establish their total. Event sequence strictly increases but is workspace-scoped; contiguous increments of one are not required for an individual Run's recovery.

Explicit browser Retry connection and Retry Widget recover. Two parallel App Frames previously time out. Closing only the original controller window, retaining the new App, and retrying Widget Runtime produces READY; two actual Toggles produce DONE and READY. Closing the window does not delete an App. This establishes one generated App's recoverability, not parallel two-Frame capacity.

### Revocation and cleanup

The second grant is revoked through one Portal Revoke and one Confirm; Cloud WS/Tunnel/HTTP become zero, and API/persisted state is revoked with online=false, credentials cleared, and approved=false. The third grant tests unavailable Cloud notification by injecting a control-origin DNS fault only in the owned backend: it starts at 13:55:01 with other hosts preserved. One local Revoke produces Revoked/cloud notification unavailable and an offline public page. Original hosts are restored at 13:55:37. Cloud WS/Tunnel/HTTP are zero at 13:56:09. License rechecks at 13:58:07 and 13:58:43 retain cleared credentials while the 14:06:26 deadline is still future; Portal subsequently revokes the residual Cloud grant.

The fourth and final grant is also revoked with one Portal Revoke and one Confirm. Portal displays revoked, the public App displays Authorization inactive, and local UI displays Revoked. At **15:00:57**, actual Cloud WS/Tunnel/HTTP are zero, control=1 belongs to the collector, reservations are 32,768 bytes, and queued=0. At **15:04:02**, independent read-only verification confirms API and persisted state are revoked, online=false, credentials cleared, and approved=false. Identity and the 15:16:48 deadline remain, with that deadline still future, establishing active revocation rather than expiry. This verification does not capture in-memory runner or poll-request counts.

At **15:08:27**, final cleanup verification confirms the owned supervisor is stopped, five owned containers are removed, the owned network and socket volume are absent, test loopback ports are closed, and the login copy is absent. Original Hermes container identity/running state remain unchanged; the original host login file exists, and workspace Graph data/observations remain. Cleanup makes no model calls, reads no original login content, and deletes no revocation records, business data, or other users' services.

## Fixes and preserved historical failures

The web UI initially has only native Coding Agent model settings and cannot use the local Codex login as Ambient's default/fast primary Provider. Behavior is documented first in the [Provider contract](../integrations/llm-providers.md) and [UML](../architecture/uml.md), followed by Red–Green implementation of `codex_native`, discovery, primary transport, web hints, and explicit rejection of unsupported OpenCode native shared bindings. Primary inference uses pinned Linux 0.145 app-server, validates managed configuration and raw-layer endpoint denial, empty MCP, read-only/no-external-environment thread policy, strict output and registered-tool JSON Schema, and retains deadline/message/byte bounds and owned process-group cleanup. It adds no API fallback or request replay.

The first actual native catalog safety preflight rejects a pinned 0.145 typed-configuration projection mismatch before account/thread/turn/inference. A correction against official pinned schema subsequently passes actual discovery, returning four models including exact Luna. Real web primary calls pass separately afterward; catalog success is not treated as inference.

**The first App remains a failed result.** One Approve plan is followed by AlignData failure, with UI generically mapping `llm_capability_unsupported` to model/tool incompatibility. Safe read-only diagnosis records two messages, a 17,575-byte prompt, a 17,592-byte empty-tools envelope, and 26,370 ms latency. Input is below 512 KiB and managed-model metadata guards pass. Source contains no dedicated API-only/tool-capability-false gate, and AlignData passes no explicit tools. These checks exclude the examined explanations, without identifying the historical native item/notification/protocol rejection.

Fixed reason/category diagnostics and a general web message are documented first, followed by pinned 0.145 ordinary `error` notification compatibility. Only strict shapes bound to this thread/turn are accepted; known `willRetry=true` waits on the same turn within the original deadline/budget without creating another RPC. Terminal errors are sanitized; foreign, unknown, or malformed structures still fail closed. Focused regression and the new controlled App establish usable new behavior, **not that this static compatibility gap caused the first AlignData failure**. The failed request is not automatically replayed.

The native Windows viewer's Count interaction does not support full Unix Runtime smoke, so complete execution moves to Linux Docker without disabling smoke. The first Docker Backend fails to become HTTP-ready in 90 seconds, with cause unproven; its owned containers/network/socket are cleaned up. One subsequent attempt with safe diagnostics becomes ready. The historical timeout is retained rather than calling a successful retry a localized or fixed product defect.

## Final regression and evidence inventory

| Final check | Result |
| --- | --- |
| Complete backend | 995 passed, 22 skipped, one existing Starlette warning, 174.79 seconds. Skips comprise 21 existing cases and one Linux process-group regression unavailable on the Windows host; that owned Linux parent/child no-orphan regression separately passes on actual Linux without inference |
| Complete frontend | 38 files, 292 tests passed |
| Frontend build/lint | Passed |
| Ruff and paired UML | Passed |
| Paired documentation and links | Consolidated `scripts/verify_docs.py` passes all 33 page pairs |
| Diff formatting | Consolidated `git diff --check` passes |
| GitHub publication | Actual push of this Ambient change to `main` remains pending when consolidated; it is not prematurely marked complete |

Regression includes Native safety preflight, configuration/endpoint restrictions, registered-tool schema, turn notification preceding RPC response, ordinary error notifications, sanitized diagnostics, OpenCode binding denial, web model hints, and localization. Tests use isolated workspaces and injected boundaries without depending on real network/models; actual runtime evidence is separate. Historical 941/953 backend and 290 frontend results do not substitute for final source-freeze results.

Safe evidence remains in ignored directories; filenames locate observations without reusable authorization secrets:

| Evidence group | Files and purpose |
| --- | --- |
| Local observations | `.cache/ambient-public-e2e-20261001/observations/`: `native-provider-luna-verified.jpg`, `public-native-model-receipt.jpg`, `public-app-align-failed.jpg`, `public-run-connector-offline.jpg`, `public-generated-app-done.jpg`, `public-generated-app-ready-restored.jpg`, and `public-final-grant-revoked.jpg`, retaining model/success/failure/offline/actual-Frame/revocation UI |
| Durable Run | `owned-run-before-20261001T145220548351Z.json`, `owned-run-offline-20261001T145256613868Z.json`, `owned-run-recovered-20261001T145345041166Z.json`, and `owned-run-terminal-20261001T145802214311Z.json`: same-Run models/counts/metadata prefixes without message bodies |
| Final revocation and cleanup | `final-grant-revocation-verified.json`, `final-owned-cleanup-verified.json`, and the two unavailable-notification rechecks in `local-revoke-unreachable-verified.json` |
| Cloud observations | `.cache/ambient-public-e2e-20261001-observation/`: `cloud-active-widget-chat-v3.json`, `cloud-second-page-capacity-v3.json`, `cloud-after-native-app-recovery-v3.json`, `cloud-single-generated-app-active-v3.json`, and `cloud-final-grant-revoked-v3.json`, recording matched versions, actual configuration, Uvicorn RSS, container memory, and resource counts |
| Final backend | `observations/backend-full-native-protocol-final.log`, recording 995/22/1 and complete test termination |

Uncaptured Cookie/old-session/browser-negative/runner/poll/retry/internal-request boundaries are explicit outcomes. Source, UI, synthetic metadata, and null counters do not masquerade as their direct observation.

## Capacity and operating limits

| Stage (UTC) | WS/Tunnel | Application reservation bytes | Gateway Uvicorn RSS bytes | Container stats bytes | Cumulative capacity rejections |
| --- | --- | --- | --- | --- | --- |
| 12:53:54 Original controller plus Chat page | 4/1 | 184,351,704 | 43,180,032 | 42,173,726 | 0 |
| 12:56:51 Owned second page, actual 429 | 6/1 | 259,339,208 | 42,561,536 | 47,783,608 | 12 |
| 14:55:13 Generated-App recovery, two Frames previously time out | 3/1 | 146,857,952 | 51,576,832 | 55,218,012 | 18 |
| 14:58:14 Active page retaining only the generated App | 4/1 | 184,351,704 | 47,984,640 | 52,974,059 | 18 |
| 15:00:57 Final revocation | 0/0 | 32,768 | 48,087,040 | 55,469,670 | 18 |

Every sample has HTTP=0/queued=0; control=1 is the collector. OOMKilled=false/restart_count=0 throughout. Uvicorn is actual PID7; PID1 init's 4,096 bytes are not Gateway RSS. Reservations, RSS, container stats, and lifetime HWM are different quantities, not stage peaks or established multi-user capacity. Cumulative rejections increase from 12 to 18, so recovery is not recorded as having zero rejections.

Current deployment directly passes functionality for **one complete page with one active App Frame** in this run. A second complete page encounters budget denial. Closing the original window and retrying a single Frame correlate with recovery after parallel Frame timeouts, without establishing capacity as the sole cause. The original controller still performs Count 2→3 after the second page closes, and the generated single Frame toggles; this local recovery does not establish two-App/multi-node/multi-user/sustained-model capacity. Platform operation needs separate resource budgeting and concurrency/backpressure load acceptance. Production Cloud limits are not changed here.

The release record's wildcard TLS uses manual DNS-01, **expires on 2026-12-30, and has no automatic renewal**. Trusted TLS/hostname access does not establish renewal, backup restore, database migration, or full rollback. Temporary same-site mode still has a parent-domain ordinary-Cookie exhaustion availability boundary; independent registrable domains remain the long-term deployment plan. Security-negative or restore exercises must not overwrite live databases or modify other users' authorization.
