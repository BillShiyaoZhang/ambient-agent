# ambient SDK Reference

The `ambient` object injected in the widget's JS environment exposes API namespaces to communicate with the host application.

## 1. Chat & Window Control

- `ambient.sendMessage(text: string)`: Sends a message query pretending to be the user.
- `ambient.fullscreen()`: Stretches the card layout to cover the full canvas.
- `ambient.minimize()`: Restores the card to grid layout.

## 2. Database Operations (`ambient.graph`)

- `ambient.graph.subscribe(query: object, callback: Function)`: Subscribes to real-time database queries. Fires updates over WS when mutations occur.
- `ambient.graph.mutate(actions: array)`: Submits Graph Database mutations (`POST /api/graph/mutate`).
  ```javascript
  await ambient.graph.mutate([
    {
      action: "create_node",
      type: "Task",
      properties: { title: "Buy milk", status: "pending" },
    },
  ]);
  ```

## 3. MCP Tools (`ambient.mcp`)

- `ambient.mcp.callTool(name: string, args: object)`: Resolves an asynchronous MCP tool call.
  ```javascript
  const weather = await ambient.mcp.callTool("fetch_weather", { location: "Beijing" });
  ```

## 4. Background Runs (`ambient.runs`)

- `ambient.runs.start(catalogId, actionId, input)`: Starts a durable background Run and returns its snapshot.
- `ambient.runs.get(runId)`: Reads current status, progress, and structured result.
- `ambient.runs.cancel(runId)`: Requests cooperative cancellation.
- `ambient.runs.subscribe(runId, callback)`: Subscribes to durable events and returns an unsubscribe function.
- `ambient.capabilities.invoke(catalogId, input, actionId?)`: Convenience wrapper that starts a Run and waits for its terminal result.

Closing a Widget removes its local listeners but does not cancel the Run.

## 5. Built-in React & UI Support

The `ambient` object exposes the React environment itself as well as a pre-built styled component library powered by Tailwind CSS:

- **`ambient.react`**: Exposes standard React Hooks (`useState`, `useEffect`, `useMemo`, `useRef`, `useCallback`).
- **`ambient.components`**: Exposes pre-designed React components. Includes:
  - `Card` (card container)
  - `Button` (interactive button)
  - `TextField` (input field)
  - `Checkbox` (checkbox control)
  - `List` (list wrapper)
  - `Table` (data table)
  - `Column` / `Row` (flex layout containers)
  - `Text` (typography text wrapper)
- **`ambient.html`**: A declarative template markup rendering utility using `htm`.
