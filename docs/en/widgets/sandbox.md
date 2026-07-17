# Sandbox Isolation & Dynamic Compilation

Because Ambient Agent's Widgets are generated dynamically by the LLM, the frontend implements a strict **Execution Sandbox & Dynamic Compilation** mechanism in `frontend/src/components/SandboxWidget.tsx` to safeguard system stability, style autonomy, and logical execution.

## 1. Execution Sandbox (JS Sandbox)

Widgets are not loaded via standard `<script>` tags or `<iframe>` containers. Instead, they run in a **scoped closure** generated via `new Function` to isolate global window scopes:

```javascript
const runScript = new Function("exports", "React", "ambient", transpileScript);
runScript(exportsObj, React, ambientProps);
```

### Parameter Injection & Access Controls:

1. **`exports`**: A CommonJS module container. The dynamic script exports its default component onto this object.
2. **`React`**: Explicitly injected React instance containing safe hooks (`useState`, `useEffect`, `useMemo`, etc.).
3. **`ambient`**: The isolated SDK context constructed specifically for this widget. It includes:
   - `ambient.react`: Safe React hooks.
   - `ambient.components`: Tailwind-styled pre-built components (`Card`, `Button`, `TextField`, etc.).
   - `ambient.html`: Dynamic HTML template markup rendering engine (`htm`).
   - `ambient.graph`: Authorized query subscription and database mutation actions.
   - `ambient.mcp`: Controlled Model Context Protocol tool execution.

Injecting API hooks through function parameters blocks direct script access to global window variables, credentials, or malicious DOM modifications.

## 2. On-the-Fly React+HTM Transpilation

Widgets lack a build-time step; they are transpiled on-the-fly when mounted on the dashboard:
1. **Transpilation Engine**: Uses `@babel/standalone` loaded in the browser to compile the JSX components and ES exports.
2. **Error Boundary**: Script compilation, module evaluation, and component rendering are wrapped inside a React `ErrorBoundary`. If any syntax error or runtime exception occurs, it is captured locally in the widget grid without affecting the canvas or sidebar.

## 3. Zero-Remount Fullscreen Lifecycle

When a Widget transitions to fullscreen layout, Ambient Agent **does not remount (Zero Remount)** the React DOM tree. 
Fullscreen transitions are handled entirely by CSS Transforms and canvas container translations, enabling instantaneous shifts. This guarantees 100% preservation of in-memory React states, active WebSocket query listeners, and keyboard cursor focus.
