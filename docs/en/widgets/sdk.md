# ambient SDK

`SandboxWidget` injects the following object as both a component prop and a module-execution argument. This page documents only interfaces present in the current code.

## 1. Host and theme

| API | Behavior |
| --- | --- |
| `ambient.sendMessage(text)` | Send a user message through the chat WebSocket |
| `ambient.fullscreen()` | Switch the current app window to maximized mode |
| `ambient.minimize()` | Switch the current app window to floating mode |
| `ambient.theme.preference` | Current preference: `system`, `light`, or `dark` |
| `ambient.theme.effective` | Current effective theme, normally `light` or `dark` |

These APIs depend on host callbacks. They are not the browser fullscreen API and do not store domain data.

## 2. Graph

### `ambient.graph.subscribe(query, callback)`

Register a persistent WebSocket query and pass query results to the callback initially or after data changes. It returns an unsubscribe function; return it directly from the component effect:

```javascript
useEffect(() => ambient.graph.subscribe({ type: "Task" }, setTasks), []);
```

### `ambient.graph.mutate(actions)`

Submit an atomic action batch to `POST /api/graph/mutate`. Public actions are `create_node`, `update_node_property`, `delete_node`, `create_edge`, and `delete_edge`.

```javascript
await ambient.graph.mutate([{
  action: "update_node_property",
  id: taskId,
  properties: { status: "done" }
}]);
```

The host generates an idempotency key per call. The backend still validates schemas, endpoints, and actions.

## 3. Durable Runs and capabilities

| API | Result/purpose |
| --- | --- |
| `ambient.runs.start(catalogId, actionId, input)` | Create a Run and return its snapshot |
| `ambient.runs.get(runId)` | Fetch the latest Run snapshot |
| `ambient.runs.cancel(runId)` | Request Run cancellation |
| `ambient.runs.subscribe(runId, callback)` | Subscribe to browser events for the Run; returns unsubscribe |
| `ambient.capabilities.invoke(catalogId, input, actionId?)` | Create a Run and wait for its terminal result |

Prefer `capabilities.invoke` for a normal backend capability call. Use `runs.*` when the Widget needs progress, cancellation, or explicit lifecycle management.

## 4. App-scoped external data

`ambient.net.request(sourceId, request)` reaches a `data_sources` entry declared by the current App's `manifest.json` through the guarded backend gateway. `sourceId` is an App-private logical name, not a capability preinstalled by Ambient.

```javascript
const forecast = await ambient.net.request("forecast", {
  path: "/v1/forecast",
  method: "GET",
  query: { latitude: 31.23, longitude: 121.47, hourly: "temperature_2m" }
});
```

The result is upstream JSON. Rejections expose `code`, `hint`, and `details`; the UI should show a retryable error state. A controller cannot provide a full URL, override the host, or call `fetch` directly.

## 5. MCP

`ambient.mcp.callTool(name, args)` requests an MCP tool declared by the app manifest through the chat WebSocket and returns a Promise:

```javascript
const result = await ambient.mcp.callTool("calendar.list_events", { limit: 20 });
```

The frontend-supplied `name` is not authorization. The backend revalidates app identity, manifest, server lifecycle, and permission rules.

## 6. React, HTM, and components

- `ambient.html`: an HTM tag bound to React `createElement`.
- `ambient.react`: `useState`, `useEffect`, `useMemo`, `useRef`, `useCallback`, `useContext`, and `useReducer`.
- `ambient.components`: `Column`, `Row`, `Card`, `Text`, `Button`, `TextField`, `Checkbox`, `List`, and `Table`.

`Row` and `Column` accept `gap`, `padding`, `align`, `justify`, `wrap`, and `style`. The boolean `wrap` prop maps to flex wrapping; these layout props are consumed by the component and are not forwarded as invalid DOM attributes.

Controllers may also use the injected `React`. The SDK does not include a fetch cache, `ambient.model`, arbitrary file-system access, or secret-reading APIs.

## 7. Lifecycle and errors

- Cancel Graph/Run subscriptions and custom timers in `useEffect` cleanup.
- `graph.mutate`, `net.request`, `capabilities.invoke`, `runs.*`, and `mcp.callTool` may reject; show retryable errors in the UI.
- A Run may enter `waiting_user` or `needs_attention` and require action in the Task Drawer. A Widget must not assume automatic completion.
- These methods are convenience interfaces; permission enforcement lives in the backend. See [Runtime Boundary](/en/widgets/sandbox.md).
