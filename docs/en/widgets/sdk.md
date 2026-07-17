# ambient SDK Reference

The `ambient` object injected in the widget's JS environment exposes API namespaces to communicate with the host application.

## 1. Chat & Window Control

- `ambient.sendMessage(text: string)`: Sends a message query pretending to be the user.
- `ambient.fullscreen()`: Stretches the card layout to cover the full canvas.
- `ambient.minimize()`: Restores the card to grid layout.

## 2. Sandboxed State (`ambient.state`)

- `ambient.state.get(pointer: string)`: Resolves target value using RFC 6901 JSON pointer syntax.
- `ambient.state.set(pointer: string, value: any)`: Mutates local state and broadcasts `STATE_DELTA` updates.
- `ambient.state.onChange(pointer: string, callback: Function)`: Subscribes to value changes. Returns `unsubscribe()` function.

## 3. Database Operations (`ambient.graph`)

- `ambient.graph.subscribe(query: object, callback: Function)`: Subscribes to real-time database queries. Fires updates over WS when mutations occur.
- `ambient.graph.mutate(actions: array)`: Submits Graph Database mutations (`POST /api/graph/mutate`).
  ```javascript
  await ambient.graph.mutate([
    {
      action: "create_node",
      type: "Task",
      properties: { title: "Buy milk", completed: false },
    },
  ]);
  ```

## 4. MCP Tools (`ambient.mcp`)

- `ambient.mcp.callTool(name: string, args: object)`: Resolves an asynchronous MCP tool call.
- `ambient.mcp.readResource(uri: string)`: Fetches static text from target MCP source URIs.

## 5. Multi-Agent Cooperation (`ambient.agent`)

Allows the widget to communicate directly with background agents and external webhooks.

### `ambient.agent.connect()`

Establishes a connection handshake with the background agent.

### `ambient.agent.send(msg: object)`

Sends a custom JSON message payload to the connected background agent.

### `ambient.agent.on(eventType: string, callback: Function)`

Listens for incoming events dispatched by the background agent.
- **Parameters**:
  - `eventType`: Event type to filter (e.g. `"STATE_SNAPSHOT"`, `"STATE_DELTA"`, or `"*"` to listen to all events).
  - `callback`: Invoked with the event data payload: `(eventData) => void`.
- **Returns**: `unsubscribe()` function.
