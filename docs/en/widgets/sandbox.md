# Widget Runtime Boundary and Dynamic Compilation

`SandboxWidget.tsx` compiles and loads dynamic Widgets in the browser. “Sandbox” is the product component name and an API boundary; it is not strong isolation for hostile JavaScript.

## 1. Current loading mechanism

After `@babel/standalone` transpilation, the Widget controller executes through this module wrapper:

```javascript
const runScript = new Function("exports", "React", "ambient", transpileScript);
runScript(exportsObj, React, ambientProps);
```

- `exports` is the module export container.
- `React` exposes the React API.
- `ambient` exposes host Graph, Run, Capability, and MCP APIs.
- Component rendering is wrapped in a React `ErrorBoundary`, so syntax/render errors appear in the Widget area.

The injected parameters reduce direct coupling to host implementation details, but `new Function` still runs in the page's JavaScript realm. It does not prevent access to `window`, DOM, storage, or same-origin network capabilities. An ErrorBoundary handles React failures; it is not an authorization boundary.

## 2. Where host enforcement belongs

Host-state operations must be revalidated by the backend. Never trust a Widget-supplied `app_id`, tool name, schema, or permission claim:

- capability and MCP tool calls execute as durable Runs;
- Graph actions are preflighted and committed in a backend SQLite transaction;
- local model tools pass through Tool Gateway;
- MCP and OpenCode enforce their own permission, path, argv, environment, and lifecycle policies.

Hiding or omitting an `ambient` method improves the API surface but cannot replace backend authorization.

## 3. Dynamic compilation and fault containment

`@babel/standalone` transpiles the controller at mount time. Widget-local ErrorBoundary rendering limits how a compilation or component-render failure propagates through the Canvas React tree.

This is availability-oriented fault containment, not confidentiality or integrity isolation. CPU-heavy loops, direct DOM access, and same-origin resource access can still affect the host page.

## 4. Running untrusted code

Strong isolation requires a separate security boundary, such as a sandboxed iframe on a separate origin, a constrained Worker, or a backend/OS container, combined with CSP, message schemas, network allowlists, and resource budgets. Until that migration exists, run only workspace Widgets the user trusts; do not describe the current component as safe for arbitrary third-party code.
