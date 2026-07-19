# 系统架构概述

Ambient Agent 围绕动态的 **GUI 卡片工作区 (Canvas Workspace)** 架构进行设计。系统中的“Apps”是指大模型动态生成的、兼容 React 的**微型交互卡片（Widgets）**。本页面概述了这些卡片小程序的总体架构、前后端连接方式以及所涉及的技术框架。

## 1. 架构模块图

为了更清晰地展示系统各模块的权责，我们将整体架构图拆分为系统宏观总览与各个子系统的细节视图。

### 1.1 系统宏观架构总览

展示了前端工作区、后端编排器、存储层与外部集成服务之间的宏观通信链路：

```mermaid
graph TB
    Frontend["前端"] <-->|WebSocket| Backend["后端"]
    Frontend -->|HTTP| Backend
    Backend <-->|SQLModel ORM| Data["数据与存储层"]
    Backend <-->|JSON-RPC / HTTPS| External["外部集成 (MCP / LLM)"]
```

### 1.2 前端工作区与沙箱架构

展示前端主控制、画布网格以及隔离沙箱在渲染卡片时的层级与通信关系：

```mermaid
graph TB
    subgraph Frontend["前端"]
        App["主控协调"] --> Canvas["画布工作区"]
        Canvas --> Sandbox["安全沙箱容器"]
        App <--> WSClient["WebSocket 客户端"]
    end
    WSClient <-->|/ws/chat| BE["后端接口"]
    Sandbox -->|/api/graph/mutate| BE
    Sandbox -->|"/api/apps/{id}"| BE
```

### 1.3 后端核心编排与执行流

展示后端 WebSocket 连接分发、生命周期编排器以及动态解析编译的层级关系：

```mermaid
graph TB
    subgraph Backend["后端"]
        Main["Web & WS 入口"] <--> Orchestrator["编排调度"]
        Orchestrator --> Parser["XML 动态代码解析"]
        Orchestrator --> AppMgr["卡片磁盘读写"]
        Main <--> BackendMgr["MCP 守护进程与授权"]
    end
    FE["前端 WebSocket"] <-->|/ws/chat| Main
    Orchestrator <-->|HTTPS| LLM["外部 LLM 服务"]
    BackendMgr <-->|stdio| MCP["MCP 服务端"]
```

### 1.4 数据存储与外部集成

展示后端服务如何读写磁盘/数据库，以及如何集成外部大语言模型与 MCP 工具服务：

```mermaid
graph TB
    subgraph Backend["后端核心服务"]
        AppMgr["AppManager"]
        Main["main.py / WS"]
        BackendMgr["BackendManager"]
        Orchestrator["AgentOrchestrator"]
    end
    subgraph Data["数据与存储层"]
        SQLiteDB[("图数据库存储")]
        DiskApps[("本地磁盘目录")]
    end
    subgraph External["外部集成服务"]
        LLM["大模型 API 服务"]
        MCPServer["MCP 服务端"]
    end
    AppMgr <-->|读写卡片源码| DiskApps
    Main <-->|SQLModel ORM 映射| SQLiteDB
    BackendMgr <-->|JSON-RPC 2.0 stdio| MCPServer
    Orchestrator <-->|HTTPS 客户端 httpx| LLM
```

## 2. 动态卡片生命周期序列

本序列图描绘了 Widget 卡片的完整生命周期，包括用户输入、后端解析落盘、WebSocket 广播、前端沙箱挂载及后续的数据交互：

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant FE as 前端
    participant BE as 后端
    participant LLM as 大语言模型
    participant DB as SQLite 数据库 / 磁盘

    %% 阶段 1：卡片生成
    User->>FE: 输入：“创建一个待办列表卡片”
    FE->>BE: 通过 WebSocket 发送对话消息
    BE->>LLM: 组装上下文并调用 Chat Completion 接口
    LLM-->>BE: 返回携带 <ambient-widget> XML 语法的流
    BE->>BE: AgentParser 自动解析 HTML、CSS 和 JS 代码段
    BE->>DB: AppManager 将源码文件写入本地磁盘目录
    BE-->>FE: 通过 WebSocket 广播新卡片元数据并更新画布布局

    %% 阶段 2：卡片挂载渲染
    FE->>FE: DashboardCanvas 挂载 SandboxWidget(id)
    FE->>BE: 发起 GET /api/apps/{app_id} 获取源码文件
    BE-->>FE: 返回文件源码内容
    FE->>FE: 执行 CSS Scoping 隔离样式
    FE->>FE: 将 HTML 挂载入独立的卡片 DOM 节点
    FE->>FE: 通过 'new Function("root", "ambient", ...)' 安全沙箱执行 JS

    %% 阶段 3：数据交互与查询订阅
    FE->>BE: WS: graph_subscribe (订阅 Task 类别数据)
    BE->>DB: SQLite: 注册实时图查询订阅句柄
    DB-->>BE: 返回首屏查询数据
    BE-->>FE: 通过 WebSocket 推送查询数据
    FE->>FE: 渲染卡片界面并展示待办数据

    %% 阶段 4：数据变更
    User->>FE: 点击“完成待办”按钮
    FE->>BE: 发送 POST /api/graph/mutate 变更请求
    BE->>DB: SQLite: 事务修改节点属性 (completed=true)
    BE->>BE: 检测到变更，触发关联订阅重跑与广播
    BE-->>FE: 推送最新的查询结果数据
    FE->>FE: 重新渲染局部视图，任务显示已完成
```

## 3. 通信链路划分

前后端在处理 Widget 卡片时使用两种通信协议协同：

### A. 双向长连接 WebSockets (接口: `/ws/chat`)

负责高实时、双向的数据流同步：

- **对话与布局同步**：广播用户的聊天消息、卡片在 Canvas 上被拖拽/缩放/固定后的网格布局设置。
- **响应式查询订阅**：卡片 JS 通过 `ambient.graph.subscribe()` 订阅的数据流均走此通道推送。
- **MCP 命令行回调**：当 Widget 通过 SDK 触发 MCP 调用时，后台在执行完子进程后通过 WS 发回响应。

图查询订阅由 WebSocket 客户端按连接生命周期管理：Widget 可以在连接进入 `OPEN` 前注册；若组件在此期间卸载，注册会被取消且不会发送无效的订阅或退订消息。连接建立或会话切换导致重连时，客户端只重放当前仍有效的订阅。普通对话、审批和工具调用命令不进入该重放集合，避免断线期间的操作在稍后被意外执行。

### B. 事务型 REST HTTP APIs

处理结构化文件读取或非实时突发请求：

- `GET /api/apps` 与 `GET /api/apps/{app_id}`：拉取卡片列表或用于前端沙箱挂载读取的代码文件。
- `DELETE /api/apps/{app_id}`：卸载特定卡片并清理磁盘空间。
- `POST /api/graph/mutate`：原子事务型修改图数据库中的节点或关系边。

## 4. 沙箱与 `ambient` 开发包

为保障系统安全与组件样式绝对隔离，所有 Widget 的交互逻辑均在前端 `SandboxWidget` 内部的容器沙箱中执行。有关 Widget (Apps) 的 UI/Controller/Data 架构设计，请参见[Widget 应用架构设计](/architecture/apps.md)。有关沙箱编译机制与 `ambient` 提供的多维度数据交互 API，请参阅[沙箱隔离机制](/widgets/sandbox)及[ambient SDK 参考手册](/widgets/sdk)。

## 5. 技术栈与所用框架

本系统基于以下现代开源技术栈构建：

### 前端技术栈

1.  **React 19**：现代核心前端框架，支持并发渲染与灵活的 Hooks 挂载。
2.  **TypeScript**：强类型保障静态接口安全与逻辑推导。
3.  **Vite**：闪电般快速的模块打包器与本地热重载开发服务器。
4.  **Tailwind CSS v4**：样式实用类，通过 `@tailwindcss/vite` 在构建时自动编译出 scoped 卡片样式。
5.  **原生 WebSockets API**：浏览器标准双向通信接口，避免了 socket.io 的冗余开销。

### 后端技术栈

1.  **FastAPI**：超高性能的 Python 异步 Web/WebSocket 框架。
2.  **Uvicorn**：轻量级 ASGI 服务器。
3.  **SQLModel (SQLAlchemy + Pydantic)**：用于 SQLite 的 ORM 框架，完美兼容 Pydantic 的类型验证与对象关系映射。
4.  **HTTPX**：用于与云端大模型或 Ollama 之间执行高效的异步 HTTP 请求通信。
5.  **Agent Client Protocol (ACP)**：规范智能体协作与工具委派的数据契约。
