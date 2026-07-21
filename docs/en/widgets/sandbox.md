# Widget Runtime Boundary and Dynamic Compilation

`SandboxWidget` hosts dynamic React Controllers. The new version defines the capability boundary as static publication verification plus a minimal SDK membrane plus per-operation backend authorization. React ErrorBoundary and “Sandbox” in the component name mean fault containment, not safe execution of arbitrary third-party JavaScript.

## 1. Preconditions for loading

The host loads only Manifest V2 Apps. The App snapshot returned to the frontend contains normalized grants and revision. If the Manifest is missing, its version or grants are invalid, or the Controller hash differs from the publication record, the API does not return executable Controller code.

The publication verifier rejects:

- imports, dynamic imports, `window`, `document`, `globalThis`, storage, `fetch`, XHR, WebSocket, Worker, `eval`, and `Function`;
- capability/source/catalog/action IDs that cannot be extracted statically;
- an `ambient` namespace without an approved grant or a resource outside its scope;
- an artifact whose staging Manifest grants differ from the approved Runtime Contract.

## 2. SDK Membrane

The host constructs a new frozen object from grants and never passes the complete internal SDK to a Controller:

```javascript
const ambient = Object.freeze({
  ...hostFeatures,
  ...(canGraphQuery ? { graph: Object.freeze({ subscribe }) } : {}),
  ...(canUseNetwork ? { net: Object.freeze({ request }) } : {}),
  ...(canUseFiles ? { files: Object.freeze(fileMethods) } : {}),
  ...(canInvoke ? { capabilities: Object.freeze({ invoke }) } : {})
});
```

The SDK binds current App ID; the Controller cannot supply another App identity. Every call carries Manifest revision/grants digest, while the backend reloads the persistent Manifest as its authority.

## 3. The backend is final

Every external operation enters `CapabilityAuthorizer.authorize(app_id, capability, operation, resource)`. Graph, HTTP, file, and Capability adapters call it before I/O or an effect. Routes and WebSocket handlers do not duplicate decisions. Denials return structured errors and enter the audit trail.

Frontend omission, frozen objects, and static verification are defense in depth, not independent authorization. A user-approved grant also cannot weaken Graph schema, network SSRF, file path, Capability input/output, MCP spawn, Run interaction, idempotency, or recovery policy.

## 4. Dynamic compilation and fault containment

`@babel/standalone` still transpiles the Controller, which executes through a constrained module wrapper in the host-page realm. React `ErrorBoundary` contains compilation/render failures and cleanup removes Widget Graph/Run listeners.

Same-realm dynamic execution is not hostile-code isolation, so the publication path accepts only local user-approved generated code that passed the system verifier. Availability risks such as CPU-heavy loops still require a future separate-origin iframe/Worker and resource budgets. Capability policy controls host I/O authority; it is not a general JavaScript sandbox.

## 5. Verification requirements

- Frontend tests verify that different grants produce different frozen SDK surfaces.
- Verifier tests cover forbidden globals, dynamic IDs, and unapproved APIs.
- Backend contract tests construct unauthorized HTTP/WebSocket calls directly, proving enforcement does not depend on the frontend.
- After a Manifest grant is changed or revoked, the next call from an already-open page must also fail.

See [Widget Capability Security](/en/architecture/capability-security.md) for categories and scopes.
