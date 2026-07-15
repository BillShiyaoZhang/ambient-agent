# System Overview Architecture

This document describes the high-level system architecture of Ambient Agent, focusing on how the React frontend and FastAPI backend communicate, and the data flows that govern dynamic widget layouts.

## System Architecture Modules (Summary-Detail Structure)

### 1. High-Level Macro Overview (Summary)

Shows the macro communication paths between the Frontend Canvas workspace, Backend Orchestrator, Storage layer, and External services:

```mermaid
graph TB
    Frontend["Frontend Canvas & Sandbox"] <-->|WebSocket: /ws/chat| Backend["Backend FastAPI Orchestrator"]
    Frontend -->|HTTP POST: /api/graph/mutate| Backend
    Frontend -->|"HTTP GET: /api/apps/{id}"| Backend
    Backend <-->|SQLModel ORM| Data["Data & Storage (SQLite graph.db & Disk)"]
    Backend <-->|JSON-RPC / HTTPS| External["External Integration (MCP / LLM)"]
```

### 2. Frontend Subsystem Details (Detail - Frontend)

Shows the coordination of frontend state, grid canvas workspace, and isolated widget container:

```mermaid
graph TB
    subgraph Frontend["Frontend (React 19 + TypeScript + Vite)"]
        App["App.tsx<br/>(Main Coordinator)"] --> Canvas["DashboardCanvas.tsx<br/>(Canvas Workspace)"]
        Canvas --> Sandbox["SandboxWidget.tsx<br/>(Sandbox Container)"]
        App <--> WSClient["websocket.ts<br/>(WebSocket Client)"]
    end
    WSClient <-->|/ws/chat| BE["Backend Server"]
    Sandbox -->|/api/graph/mutate| BE
    Sandbox -->|"/api/apps/{id}"| BE
```

### 3. Backend Subsystem Details (Detail - Backend)

Shows the WebSocket ASGI entry point, lifecycle coordinator orchestrator, and dynamic code parser:

```mermaid
graph TB
    subgraph Backend["Backend (FastAPI)"]
        Main["main.py<br/>(Web & WS Entry)"] <--> Orchestrator["AgentOrchestrator<br/>(Orchestrator Lifecycle)"]
        Orchestrator --> Parser["AgentParser<br/>(XML Widget Parser)"]
        Orchestrator --> AppMgr["AppManager<br/>(Widget File Manager)"]
        Main <--> BackendMgr["BackendManager<br/>(MCP Daemon & Security)"]
    end
    FE["Frontend WebSocket"] <-->|/ws/chat| Main
    Orchestrator <-->|HTTPS| LLM["LLM Service"]
    BackendMgr <-->|stdio| MCP["MCP Server"]
```

### 4. Data Layer & External Integration (Detail - Data & Integration)

Shows how files are stored on disk, SQLModel mapping in SQLite, and stdio execution for MCP daemons:

```mermaid
graph TB
    subgraph Backend["Backend Core Services"]
        AppMgr["AppManager"]
        Main["main.py / WS"]
        BackendMgr["BackendManager"]
        Orchestrator["AgentOrchestrator"]
    end
    subgraph Data["Data & Storage Layer"]
        SQLiteDB[("SQLite graph.db<br/>(Graph Storage)")]
        DiskApps[("Local Filesystem<br/>(workspace/apps/{app_id}/*)")]
    end
    subgraph External["External Integration"]
        LLM["LLM Service<br/>(Ollama / MiniMax / OpenAI)"]
        MCPServer["MCP Servers<br/>(Stdio CLI subprocesses)"]
    end
    AppMgr <-->|Read/Write HTML, CSS, JS| DiskApps
    Main <-->|SQLModel ORM| SQLiteDB
    BackendMgr <-->|JSON-RPC 2.0 via Stdio| MCPServer
    Orchestrator <-->|HTTPS Client httpx| LLM
```

## Communication Methods

1.  **WebSockets** (`/ws/chat`): Provides bidirectional, real-time message broadcasting, canvas layout syncing, graph query subscription pushes, and MCP execution calls.
2.  **REST HTTP API**: Handles file retrieval for card mounting (`GET /api/apps/{app_id}`), listing available apps, and transactional database modifications (`POST /api/graph/mutate`).

## Sandboxing & Apps Architecture

For detailed information on the decoupled architecture of Widgets (Apps) including UI, Controller, and Data layers, please see [Widget Apps Architecture](/en/architecture/apps.md). For details on the security sandbox and compiled scoping, see [Sandbox Isolation](/en/widgets/sandbox) and [ambient SDK Reference](/en/widgets/sdk).
