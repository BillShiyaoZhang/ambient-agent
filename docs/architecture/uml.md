# Ambient Agent Backend UML & Architecture

本项目后端使用 FastAPI + SQLModel (SQLite) 构建，支持多端 WebSocket 实时同步，并集成了动态沙箱 Widget 管理和 LLM 传输审计。

## 1. 后端类图 (Class Diagram)

## 1. 后端类图 (Class Diagram - 总-分结构)

为了提高架构图在网页端的可读性，我们将复杂的后端类图拆分为系统宏观调用图与 5 个核心功能模块的详细类图。

### 1.1 后端宏观关系图 (总)

展示了后端各个服务与核心管理器的整体依赖与协作关系：

```mermaid
classDiagram
    class AgentOrchestrator {
        +db: WorkspaceStorage
        +app_manager: AppManager
        +context_manager: ContextManager
        +handle_message() tuple
    }
    class WorkspaceStorage {
        +workspace_dir: str
    }
    class AppManager {
        +apps_dir: str
    }
    class IntentRouter {
        +route() IntentPlan
        +refine_sub_intents() IntentPlan
    }
    class PlanExecutor {
        +run_plan() PlanPhaseResult
    }
    class WidgetDAG {
        +step() TaskResult
    }
    class BackendManager {
        +workspace_dir: str
    }

    AgentOrchestrator --> WorkspaceStorage : references
    AgentOrchestrator --> AppManager : references
    AgentOrchestrator --> IntentRouter : references
    AgentOrchestrator --> PlanExecutor : references
    AgentOrchestrator --> WidgetDAG : references
    AgentOrchestrator --> BackendManager : references
```

### 1.2 存储与会话模块 (分 - Storage & Session)

管理多会话对话历史、本地磁盘的 App Widget 源码持久化以及 SQLite 实体底层读写操作：

```mermaid
classDiagram
    class ChatSession {
        +id: str (PK)
        +title: str
        +created_at: datetime
        +updated_at: datetime
    }

    class ChatMessage {
        +id: int (PK)
        +session_id: str (FK)
        +role: str
        +sender: str
        +content: str
        +timestamp: datetime
    }

    class LLMAuditLog {
        +id: int (PK)
        +timestamp: datetime
        +provider: str
        +model: str
        +prompt: str
        +response: str
    }

    class AppManager {
        +apps_dir: str
        +create_or_update_app(app_id: str, title: str, html: str, css: str, js: str, kwargs) void
        +get_app_files(app_id: str) dict
        +list_apps() List~dict~
        +delete_app(app_id: str) bool
        +get_manifest(app_id: str) AppManifest|None
    }

    class AppRecordStore {
        +db_path: Path
        +serialized() Iterator
        +get(transaction, app_id) AppRecord
        +put(transaction, app_id, created_at, updated_at) AppRecord
        +delete(transaction, app_id) void
    }

    class WorkspaceStorage {
        +workspace_dir: str
        +sessions_dir: str
        +apps_dir: str
        +get(model_class, obj_id) BaseModel
        +add(obj) void
        +commit() void
        +refresh(obj) void
        +get_sessions() List
        +get_messages(session_id) List
        +get_audit_logs() List
        +delete_session(session_id) bool
        +get_canvas_config() dict
        +save_canvas_config(config) void
    }

    ChatSession "1" --* "0..*" ChatMessage : contains
    AppManager --> AppRecordStore : persists lifecycle timestamps
```

### 1.3 意图路由与上下文模块 (分 - Intent Routing & Context)

处理大语言模型输入提示词的剪枝合并，进行对话输入的多层意图路由与函数规划：

```mermaid
classDiagram
    class ContextManager {
        -db: WorkspaceStorage
        -app_manager: AppManager
        +build_llm_prompt(session_id: str) List~dict~
        -_prune_message_content(content: str) str
    }

    class PromptManager {
        +prompts_dir: Path
        +env: Environment
        +get_prompt(template_name, kwargs) str
    }

    class IntentRouter {
        +route(content, context) IntentPlan
        +route_legacy(content, existing_apps) IntentPlan
        +refine_sub_intents(plan, context) IntentPlan
    }

    class RouterContext {
        +app_manifests: List~dict~
        +graph_snapshot: GraphSnapshot
        +session_recent: List~dict~
        +build(app_manager, graph_db, session_messages) RouterContext
        +render_for_prompt() str
    }

    class GraphSnapshot {
        +type_counts: Dict
        +recent_nodes_by_type: Dict
        +schema_manifest: List~dict~
        +node_count: int
        +edge_count: int
        +from_db(db, recent_per_type) GraphSnapshot
    }

    class IntentPlan {
        +kind: IntentKind
        +confidence: float
        +rationale: str
        +app_id: str
        +instruction: str
        +actions: List~dict~
        +query: dict
        +sub_intents: List~SubIntent~
        +clarification_message: str
        +clarification_options: List~dict~
        +deprecated: bool
        +to_dict() dict
        +from_dict(data) IntentPlan
        +from_tool_call_args(args) IntentPlan
        +tool_schema() dict
    }

    class SubIntent {
        +kind: SubIntentKind
        +app_id: str
        +instruction: str
        +actions: List~dict~
        +query: dict
        +extend_schema_props: dict
        +feedback: str
        +to_dict() dict
        +from_dict(data) SubIntent
    }

    class IntentKind {
        <<enum>>
        +WIDGET_CREATE
        +WIDGET_MODIFY
        +GRAPH_MUTATION
        +GRAPH_QUERY
        +PLAN_AND_ACT
        +MULTI_INTENT
        +CLARIFY
        +CONVERSE
    }

    class SubIntentKind {
        <<enum>>
        +GRAPH_MUTATION
        +GRAPH_QUERY
        +WIDGET_CREATE
        +WIDGET_MODIFY
        +WIDGET_EXTEND_SCHEMA
        +WIDGET_FIX_CODE
        +WIDGET_REWRITE
    }

    IntentRouter --> IntentPlan : returns structured plan
    IntentRouter --> RouterContext : consumed
    RouterContext --> GraphSnapshot : embeds snapshot
    IntentPlan --> IntentKind : kind is an enum value
    IntentPlan --> SubIntent : 0..* sub_intents
    SubIntent --> SubIntentKind : kind is an enum value
```

### 1.4 Widget DAG 与 Schema 校验模块 (分 - Widget DAG & Verification)

由有向无环图运行环境驱动的卡片生命周期处理流程，以及对卡片代码的数据完整性验证：

```mermaid
classDiagram
    class WidgetDAG {
        +_nodes: Dict
        +_order: List
        +_dirty: Set
        +register(node) void
        +dirty(names) void
        +idle() bool
        +pending() List
        +step(ctx) TaskResult
    }

    class TaskNode {
        +name: str
        +run: Callable
        +needs_outputs_from: Set
        +invalidates: Set
    }

    class TaskResult {
        +success: bool
        +outputs: dict
        +error: str
        +ask_user: dict
        +invalidates_if_redo: Set
    }

    class VerificationDiff {
        +unknown_props: List
        +type_mismatches: List
        +unknown_types: List
        +is_clean: bool
        +to_markdown() str
        +to_per_field_payload() List
    }

    class SchemaVerificationService {
        +diff(app_id, widget_code, schemas) VerificationDiff
        +verify(app_id, widget_code, schemas) str
    }

    WidgetDAG --> TaskNode : 0..* registered tasks
    TaskNode --> TaskResult : runs produce results
    SchemaVerificationService --> VerificationDiff : returns structured diff
```

### 1.5 执行计划与核心编排模块 (分 - Execution & Orchestration)

系统总控制器、动作执行执行器、数据库变更回撤管理器以及底层 LLM 服务提供者：

```mermaid
classDiagram
    class AgentOrchestrator {
        +db: WorkspaceStorage
        +app_manager: AppManager
        +context_manager: ContextManager
        +run_opencode_agent_acp_fn: function
        +handle_message(session_id: str, content: str, on_update: Callable) tuple
        -_run_callback(callback: Callable, data: Any) void
        -_handle_graph_mutation(plan, session_id, on_update) tuple
        -_handle_graph_query(plan, session_id, on_update) tuple
        -_handle_plan_and_act(plan, session_id, on_update) tuple
        -_handle_multi_intent(plan, session_id, on_update) tuple
        -_handle_widget_build(plan, session_id, on_update) tuple
    }

    class PlanExecutor {
        <<abstract>>
        +run_plan(plan, instruction, on_update) PlanPhaseResult
    }

    class CodingPlanExecutor {
        +run_plan(plan, instruction, on_update) PlanPhaseResult
    }

    class MutationPlanExecutor {
        +run_plan(plan, instruction, on_update) PlanPhaseResult
    }

    class PlanPhaseResult {
        +success: bool
        +output: str
        +error: str
        +extra: dict
    }

    class MutationTicketManager {
        +record(session_id, forward_actions, snapshot_before) MutationTicket
        +pin(session_id, ticket_id) bool
        +rollback(session_id, ticket_id) List~dict~
        +get(session_id, ticket_id) MutationTicket
    }

    class LLMService {
        +generate_agent_response(messages: List~dict~) str
        +call_llm_api(provider: str, model: str, messages: List~dict~, tools: List~dict~|None) dict
    }

    PlanExecutor <|-- CodingPlanExecutor
    PlanExecutor <|-- MutationPlanExecutor
    MutationPlanExecutor --> MutationTicketManager : records rollback tickets
```

### 1.6 外部集成与 MCP 模块 (分 - MCP & Integration)

管理第三方模型上下文协议进程生命周期，并以 JSON-RPC 2.0 规范代理各种工具与资源请求：

```mermaid
classDiagram
    class StdioJsonRpcClient {
        +command: list~str~
        +args: list~str~
        +env: dict~str, str~|None
        +process: Process|None
        +read_task: Task|None
        +pending_requests: dict
        +next_id: int
        +lock: Lock
        +start() void
        -read_loop() void
        +call(method: str, params: dict) Any
        +stop() void
    }

    class BackendManager {
        +workspace_dir: str
        +permissions_file: Path
        +mcp_clients: dict~str, StdioJsonRpcClient~
        +pending_permissions: dict
        +permissions: dict
        -load_permissions() void
        -save_permissions() void
        +is_mcp_approved(app_id: str, command: list~str~, args: list~str~) bool
        +approve_mcp(app_id: str, command: list~str~, args: list~str~) void
        +is_agent_approved(app_id: str, agent_url: str) bool
        +approve_agent(app_id: str, agent_url: str) void
        +resolve_permission(request_id: str, approved: bool) void
        +request_permission(app_id: str, permission_type: str, value: dict|str, send_ws_message_func: Callable) bool
        +get_or_start_mcp_client(app_id: str, manifest: AppManifest, send_ws_message_func: Callable) StdioJsonRpcClient|None
        +handle_agent_message(app_id: str, manifest: AppManifest, message: dict, send_ws_message_func: Callable) void
        +shutdown() void
    }

    BackendManager --> StdioJsonRpcClient : manages MCP processes
```

## 2. 核心模块说明

### 2.1 数据库实体层 (`models.py`)
*   **ChatSession**: 管理多端用户会话。
*   **ChatMessage**: 存储对话历史，支持 `user`, `agent`, `code`, `system` 等不同角色的消息归档。
*   **LLMAuditLog**: 记录发送给 LLM 接口的原始 Payload 与响应，供审计面板展示。

### 2.2 服务与逻辑控制层
*   **AppManager (`app_manager.py`)**: 管理动态生成的小程序（Widget）。负责 Widget 代码文件在磁盘的读写、数据状态存储及文件路径寻址。
*   **ContextManager (`context_manager.py`)**: 负责将数据库中的对话上下文整合为 LLM 兼容的 Prompt。此层会自动剔除冗余代码段并动态注入当前运行中应用的最新源码，在控制 Token 大小的同时给 LLM 提供充分的运行环境上下文。
*   **AgentParser (`agent_parser.py`)**: 负责用正则表达式和 XML 解析器解析 LLM 返回文本流中携带的 `<ambient-widget>` 语法块，提取 HTML、CSS 和 JS 内容。
*   **LLMService (`llm_service.py`)**: 提供统一的大模型请求接口（支持本地 Ollama 与 OpenAI/MiniMax 兼容接口），并自动将请求原始数据记录至 `LLMAuditLog` 审计数据库。
*   **RouterContext (`router_context.py`)**: 收集路由所需的轻量级上下文：已存在的 widgets、Graph 类型与节点摘要、近期对话。供 IntentRouter 在调用 LLM 时一并注入。
*   **GraphSnapshot**: RouterContext 嵌入的图状态摘要，用于让 LLM 意识到现有数据，避免重复创建。
*   **IntentPlan / IntentKind / SubIntent / SubIntentKind (`agent/intent_plan.py`)**: 用 `classify_intent` function-calling 协议引导 LLM 返回的结构化输出。`SubIntent` 是 `multi_intent` 顶层意图下的子动作列表。
*   **IntentRouter (`agent/router.py`)**: 两层 LLM 路由：
    - `route()` 调用 LLM #1 获取顶层 `kind` + `sub_intents[]`
    - `refine_sub_intents()` 在 `multi_intent` / `plan_and_act` 时调用 LLM #2 细化 sub_intents
    - 失败时降级为 `converse`；widget_modify 重名检测必要时降级为 `clarify`
*   **WidgetDAG / TaskNode / TaskResult (`agent/dag.py`)**: 替换 widget 流水线的 `while current_state` 状态机。6 个任务节点：`plan`、`align_schemas`、`regen_code`、`verify`、`decode_user_intent`、`apply_user_actions`。节点的 `invalidates` 字段定义了"重跑时连带 dirty 哪些下游节点"。
*   **SchemaVerificationService / VerificationDiff (`schema_verification.py` + `schema_diff.py`)**: 替代旧的纯文本 Markdown 报告，输出结构化 `VerificationDiff`（含 `unknown_props[]` / `type_mismatches[]`），让前端能渲染 per-field checkbox UI。
*   **PlanExecutor / CodingPlanExecutor / MutationPlanExecutor (`agent/plan_executor.py`)**: 把"计划-审批-执行-校验"流水线抽象为策略类。`CodingPlanExecutor` 包装原有 widget 流；`MutationPlanExecutor` 在用户审批后批量执行 graph_mutation，并把每次调用登记为一个可撤销的 mutation ticket。
*   **MutationTicketManager (`mutation_tickets.py`)**: 为每次 graph_mutation 提供 60s 软默认 + 用户可星标为永久的撤销窗口。撤销逻辑使用 graph 数据库层新增的 `graph_mutation_history` 表。

### 2.3 实时多端同步层 (`main.py` WebSocket)
*   通过长连接管理不同的 Session 连接。
*   一端发送消息时，服务端接收并广播给同一 `session_id` 的所有客户端，实现多端画布、对话气泡的强实时一致性同步。

### 2.4 后端服务代理层 (`backend_manager.py`)
*   **StdioJsonRpcClient**: 负责通过 stdio 与外部启动的 MCP 命令行子进程进行 JSON-RPC 2.0 通信，提供异步的工具调用及资源读取接口。
*   **BackendManager**: 统筹协调外部服务连接。负责按需启动并缓存 MCP 子进程、与外部 Agent URL 执行 SSE 事件流式代理、管理以及持久化用户针对敏感后端操作（启动 MCP 命令或连接 Agent URL）的授权配置（存储在 `workspace/backend_permissions.json`）。

