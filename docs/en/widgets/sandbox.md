# Widget Isolation Runtime

By default, a Widget renders as a microfrontend inside an isolated iframe in the user's browser. The system no longer continuously streams JPEG/PNG frames from server-side Chromium. A Controller still uses Manifest V2 plus `controller.js`, but it is evaluated only inside a `sandbox="allow-scripts"` opaque-origin frame; neither the trusted React host nor the Backend evaluates Controller source.

The previous server-side Chromium pixel stream remains available for one release as an explicit rollback path; see section 7.

## 1. Deployment and trust boundary

```mermaid
flowchart LR
    Host["Trusted Frontend: SandboxWidget"] -->|"fixed frame.html"| Frame["opaque-origin iframe"]
    Host <-->|"nonce + MessageChannel"| Frame
    Host <-->|"one-time ticket WebSocket"| API["FastAPI"]
    API --> Auth["CapabilityAuthorizer"]
    Auth --> Graph["Graph adapter"]
    Auth --> Files["App file adapter"]
    Auth --> Net["HTTP data-source adapter"]
    Auth --> Caps["Capability / Run adapter"]
    Host --> Storage["host IndexedDB: scoped by App"]
```

Docker Compose adds a `widget-frame` service. Its browser-reachable port 8001 serves only the fixed Shell, renderer, and compiler assets. It receives no App ID, ticket, Controller, or user data and sets no Cookie. The Backend continues to issue tickets and process capability RPC on port 8000.

Local Compose always publishes port 8001. An HTTPS reverse proxy or
path-prefixed deployment sets the browser build variable `VITE_API_BASE_URL`
to either a same-origin path prefix or an absolute `http(s)` URL, and sets the
Backend's `WIDGET_FRAME_URL` to the complete public frame URL. Both resolved
addresses must be browser-reachable; a Docker service name must never be
returned to the browser.

The iframe combines:

- the HTML attribute `sandbox="allow-scripts"` without `allow-same-origin`, forms, popups, downloads, top navigation, or other permissions;
- `allow=""` and `referrerPolicy="no-referrer"`;
- a response CSP with `sandbox allow-scripts`, `default-src 'none'`, and `connect-src 'none'`, plus blocks for workers, child frames, objects, media, fonts, manifests, forms, and base URLs;
- a Permissions Policy denying camera, microphone, geolocation, clipboard, USB, serial, payment, credentials, and other browser capabilities;
- `Cache-Control: no-store` for frame HTML, health, and error responses;
  strong SHA-256 ETags plus validated caching for fixed same-service JS/CSS
  assets; and `X-Content-Type-Options: nosniff` on every response.

The CSP permits fixed same-service scripts and Babel's required `'unsafe-eval'`. Because CSP sandbox gives the document an opaque origin, fixed ES modules carry credentialless `Access-Control-Allow-Origin: *`. Those responses contain no App data, while Controller `fetch` and WebSocket calls remain blocked by `connect-src 'none'`.

The Frame Server serves only its fixed path allowlist. At startup it reads each
JS/CSS asset, computes a strong ETag, and rewrites the Frame HTML references to
content-digest `?v=` URLs. A matching version URL receives
`Cache-Control: public, max-age=31536000, immutable`; an unversioned or
mismatched compatibility URL receives `public, max-age=0, must-revalidate`.
Within one browser network-cache partition, either form returns a bodyless
`304` for a matching `If-None-Match`.

Each newly constructed `sandbox="allow-scripts"` iframe receives a new opaque
origin. A browser may assign that origin a new network-cache partition, so the
implementation cannot promise that suspend/resume reuses the previous iframe's
HTTP cache. To bound unavoidable retransmission, the Frame Server precomputes
Brotli and gzip representations of fixed JS/CSS at startup, selects `br`,
`gzip`, or identity from `Accept-Encoding`, and sends
`Vary: Accept-Encoding`; each encoded representation has its own strong ETag.
The currently pinned Babel asset falls from 2,458,024 identity bytes to 444,563
bytes (about 434 KiB) with Brotli or 562,162 bytes (about 549 KiB) with gzip.
Frame HTML itself and any App or dynamic content never enter a long-lived
shared cache. `HEAD`, `304`, and `200` responses retain the same CSP,
Permissions Policy, Referrer Policy, CORS, and nosniff security headers.

## 2. Tickets, sessions, and identity

When opening a Widget, the trusted host:

1. calls `POST /api/apps/{app_id}/client-runtime-ticket`;
2. has its request `Origin` checked against `AMBIENT_FRONTEND_ORIGINS`;
3. receives a 256-bit, single-use ticket valid for 30 seconds plus a fixed `frame_url`;
4. connects to `/ws/widgets/{app_id}/client-runtime` with WebSocket subprotocols `ambient-widget-client-v1` and `ticket.<token>`;
5. has the Backend bind the consumed ticket to its issuance Origin, App ID, Manifest revision, grants digest, and Controller artifact digest.

The ticket never enters the iframe, URL, DOM, or local storage. Before the WebSocket opens, the Backend reloads the App snapshot. An expired/reused ticket, Origin or App mismatch, or changed artifact fails closed. Defaults allow at most 256 pending tickets and 16 active client-runtime sessions.

A Controller RPC contains only `{ request_id, method, params }`. The Frontend and Backend remove forged `app_id`, session, revision, grants, and artifact fields from the payload. Authorization and idempotency identity come only from the Backend-owned session binding. Every Graph, Network, Files, or installed-capability operation still enters the existing `CapabilityAuthorizer`.

## 3. Frame startup and communication

`frame.html` reads no query, Cookie, `localStorage`, or `IndexedDB`. The trusted host creates a `MessageChannel` and random nonce, then transfers one port to the iframe. The iframe accepts only the first direct-parent message with a matching protocol version and exactly one port, echoes the nonce, and immediately removes its global `window.message` listener.

All later communication uses the transferred port:

```text
Backend -> trusted host WS: bootstrap(controller_source, capability_ids)
Host -> frame port:          init / before_suspend / presentation / responses
Frame -> host port:          rpc_request / storage_request / host_event / suspend_ready
```

The host never uses `eval`, `Function`, Babel, or React to render Controller code. Babel transformation, the module wrapper, and Preact rendering all occur in the isolated frame. Closing the port, WebSocket, or component rejects pending requests, closes the session, and unregisters Graph subscriptions. A server-side send lock serializes concurrent RPC and subscription output.

## 4. Local persistent data

A Controller uses `ambient.storage` for non-secret state belonging to the current browser and App:

```javascript
const draft = await ambient.storage.get("draft");
await ambient.storage.set("draft", { text: "local only" });
const keys = await ambient.storage.list();
await ambient.storage.delete("draft");
await ambient.storage.clear();
```

The trusted host writes the data to IndexedDB; iframe Cookies and storage are not used. The namespace always comes from the host-known `widget.id`, ignoring any identity fields in a Controller payload, so one Widget cannot select or read another Widget's namespace. Closing, reopening, or upgrading the same App ID reuses its data.

Constraints:

- keys contain 1–256 characters;
- values are bounded-depth, acyclic JSON values, at most 64 KiB each;
- each App is limited to 128 keys and 1 MiB total, preventing one Widget from exhausting the browser quota for the entire origin;
- a missing key returns `null`; mutations return `{ status: "ok" }`;
- data exists only in the current browser profile and may disappear when site data is cleared, private browsing ends, or the browser reclaims quota;
- never store credentials, API keys, access tokens, cross-device state, or the sole copy of user data.

### 4.1 Pre-suspend flush

A Controller that may be suspended, unmounted, and reconstructed when the
Workspace next activates it can register one asynchronous flush handler:

```javascript
const unsubscribe = ambient.lifecycle.onBeforeSuspend(async () => {
  await ambient.storage.set("draft", latestDraft);
});
```

At most one handler is retained; a later registration replaces the earlier
one. Each `unsubscribe` removes only the registration it created if that
registration is still current, so stale cleanup cannot remove a newer handler.
Only after the authenticated port reaches `ready` may the host send
`before_suspend` with a non-empty, bounded `request_id`. The Frame runs at most
one handler at a time and replies with `suspend_ready` after its Promise
settles. A repeated completed request reuses the one-entry bounded result
cache; a new concurrent request fails immediately without starting a second
flush or masquerading as a capability RPC.

If the handler throws, the Frame returns a bounded error without a stack and
keeps the Frame and host session usable. The host still releases the iframe
after its own bounded wait, so this hook is a best-effort opportunity to flush
before unmount, not an indefinite suspension lock. Drafts, forms, and editor
state should be written through to `ambient.storage`; a debounced design must
await any outstanding write in the handler. The pixel fallback does not expose
this API. No late `suspend_ready` is sent after the Frame or port is disposed.

## 5. Presentation context and host events

Theme, language, and reduced-motion preferences travel over the same port without rebuilding the iframe or WebSocket. The Runtime updates `documentElement` and CSS variables and notifies `ambient.presentation` and `ambient.theme` subscribers.

A Controller may request `fullscreen`, `minimize`, and `sendMessage`. The host processes only those three fixed events, with a 16 KiB message-text limit. The port cannot request arbitrary DOM operations, browser APIs, or host functions.

## 6. Security properties and limits

This design separates an untrusted Controller from the host page's DOM, Cookies, storage, JavaScript realm, and privileged browser APIs. It also avoids video encoding, frame latency, IME translation, blurry output, and loss of the accessibility tree. The Backend remains the final authorization boundary; client isolation never replaces per-operation capability checks.

It is not a VM-grade strong sandbox. Residual risks include:

- browser-engine vulnerabilities and side channels within the browser process;
- a malicious Controller consuming CPU or memory in the current tab;
- a sandboxed frame can navigate itself, so browser sandboxing and CSP greatly reduce direct networking but do not promise mathematically zero egress or covert channels;
- the verifier prevents mistakes and reduces attack surface; it is not a complete proof system for JavaScript.

Controllers and local storage therefore receive no secrets. Every Controller RPC, data value, and UI event is treated as untrusted input. High-risk capabilities remain protected by Backend policy, user confirmation, and audit.

## 7. Pixel-stream rollback

Build the Frontend with `VITE_WIDGET_UI_TRANSPORT=pixels` to restore the original `PixelSandboxWidget`, `/ws/widgets/{app_id}/runtime`, and zero-network Chromium `widget-runtime` container. Compose temporarily keeps both runtime services.

This is an emergency compatibility rollback, not the default. It retains video bandwidth, input forwarding, and higher resource usage, and it does not expose the new host IndexedDB `ambient.storage`. A new Widget that depends on local storage must not run in pixel mode. The old path can be removed after one release of deployment metrics and compatibility validation.

To keep the default iframe path from paying for an idle Chromium process, `widget-runtime` listens on its Unix socket first but does not create a browser at startup. The first pixel session or generation smoke-test `start` message launches one Chromium on demand; concurrent first opens share that single launch. After the last session closes, the Runtime waits for `WIDGET_RUNTIME_IDLE_TIMEOUT_MS` (60 seconds by default) before closing Chromium. A new session during that window cancels reclamation. An intentional idle close releases only the browser and never exits the socket supervisor, so a later session can launch Chromium again.

The browser lifecycle enforces a strict single-instance resource bound: at most one running or closing Chromium exists at any time. If the idle timer has already entered asynchronous `close()`, a new `start` waits for that close to settle instead of launching a second browser in parallel. After a successful close, or a failed close when the browser is confirmed disconnected, waiters may share the next on-demand launch. If `close()` fails while the browser remains connected, the Runtime immediately enters a fatal state, rejects every later `start`, and leaves recovery to the container restart instead of launching a second Chromium beside the surviving process.

An unplanned Chromium disconnect also marks the browser lifecycle fatal synchronously, so every new `start` is rejected from the disconnect event onward; Chromium cannot relaunch during the supervisor's delayed-exit window. The production fatal callback still sends a runtime error to every existing session, closes those sessions, and exits non-zero so Docker's automatic restart policy can recover the complete Runtime. `restart: unless-stopped` does not bound retry count, and an `unhealthy` status alone does not restart a process that is still running. Explicit supervisor shutdown is an intentional close and does not enter this restart path.

Pixel mode keeps at most four BrowserContexts by default (lower it with `WIDGET_RUNTIME_MAX_CONTEXTS`; raise it only after increasing and load-testing the container's PID and memory budgets), leaving PID headroom for Chromium's main process, renderers, and the Runtime under the container's 192-PID limit. A start beyond the limit fails without disturbing existing sessions; closing or disconnecting a session releases its BrowserContext. Babel standalone is also absent from the zero-session startup path and is dynamically loaded and reused only when the first Controller is transformed.

See [ambient SDK](/en/widgets/sdk.md) for APIs, [Widget Capability Security](/en/architecture/capability-security.md) for authorization, and [Widget Generation Information Contract](/en/architecture/widget-generation.md) for generation.
