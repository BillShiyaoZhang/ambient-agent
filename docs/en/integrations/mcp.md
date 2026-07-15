# MCP Integration

Model Context Protocol (MCP) connects the LLM with local command-line tools. The backend executes a stdio-based JSON-RPC client to delegate executions safely.

## 1. Running Architecture

The FastAPI backend manages external CLI processes through `StdioJsonRpcClient`:

```mermaid
sequenceDiagram
    participant Widget as Sandbox JS (ambient.mcp)
    participant FE as Frontend WebSocket
    participant BE as Backend (BackendManager)
    participant Daemon as MCP Daemon (CLI Process)

    Widget->>FE: ambient.mcp.callTool('git_status', {})
    FE->>BE: WS: mcp_call_tool
    BE->>BE: Check & Request permissions
    BE->>Daemon: Stdio stdin: {"jsonrpc": "2.0", "method": "tools/call", ...}
    Daemon-->>BE: Stdio stdout: {"jsonrpc": "2.0", "result": {...}}
    BE-->>FE: WS: mcp_call_response
    FE-->>Widget: Promise.resolve(result)
```

## 2. API Usage

Inside the widget's `<js-script>` scope:

```javascript
ambient.mcp
  .callTool("git_status", { repo_path: "/workspace" })
  .then((result) => {
    console.log("Git details:", result);
  });
```
