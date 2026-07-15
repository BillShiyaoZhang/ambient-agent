# System Overview Architecture

This document describes the high-level system architecture of Ambient Agent, focusing on how the React frontend and FastAPI backend communicate, and the data flows that govern dynamic widget layouts.

```mermaid
graph TB
    subgraph Frontend["Frontend (React 19 + TypeScript + Vite)"]
        direction TB
        App["App.tsx<br/>(Main Coordinator / State)"]
        Canvas["DashboardCanvas.tsx<br/>(Grid Layout Canvas)"]
        Sandbox["SandboxWidget.tsx<br/>(Sandboxed App Container)"]
        WSClient["websocket.ts<br/>(Native WebSockets)"]
    end

    subgraph Backend["Backend (FastAPI + Uvicorn)"]
        direction TB
        Main["main.py<br/>(ASGI Web & WS Server)"]
        Orchestrator["AgentOrchestrator<br/>(Workspace & LLM Router)"]
        Parser["AgentParser<br/>(XML XML-to-Widget Compiler)"]
        AppMgr["AppManager<br/>(Widget Disk & Records Storage)"]
        BackendMgr["BackendManager<br/>(MCP Daemon & SSE Proxy)"]
    end

    subgraph Data["Data & Storage Layer"]
        SQLiteDB[("SQLite graph.db<br/>(SQLModel Graph Storage)")]
        DiskApps[("Local Filesystem<br/>(workspace/apps/{app_id}/*)")]
    end

    subgraph External["External Integration"]
        LLM["LLM Service<br/>(Ollama / MiniMax / OpenAI)"]
        MCPServer["MCP Servers<br/>(Stdio CLI subprocesses)"]
    end

    %% Connections
    App --> Canvas
    Canvas --> Sandbox
    App <--> WSClient
    WSClient <-->|WebSocket: /ws/chat| Main
    
    %% API Calls
    Sandbox -->|"HTTP POST: /api/graph/mutate"| Main
    Sandbox -->|"HTTP GET: /api/apps/{app_id}"| Main
    
    %% Backend Flow
    Main <--> Orchestrator
    Orchestrator --> Parser
    Orchestrator --> AppMgr
    Main <--> BackendMgr
    
    %% Database / Disk
    AppMgr <-->|Read/Write HTML, CSS, JS| DiskApps
    Main <-->|SQLModel ORM| SQLiteDB
    BackendMgr <-->|JSON-RPC 2.0 via Stdio| MCPServer
    Orchestrator <-->|"HTTPS Client (httpx)"| LLM
```

## Communication Methods

1.  **WebSockets** (`/ws/chat`): Provides bidirectional, real-time message broadcasting, canvas layout syncing, graph query subscription pushes, and MCP execution calls.
2.  **REST HTTP API**: Handles file retrieval for card mounting (`GET /api/apps/{app_id}`), listing available apps, and transactional database modifications (`POST /api/graph/mutate`).
