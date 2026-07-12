# Ambient Agent Backend UML & Architecture

本项目后端使用 FastAPI + SQLModel (SQLite) 构建，支持多端 WebSocket 实时同步，并集成了动态沙箱 Widget 管理和 LLM 传输审计。

## 1. 后端类图 (Class Diagram)

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
        +create_or_update_app(app_id: str, title: str, html: str, css: str, js: str) void
        +get_app_files(app_id: str) dict
        +list_apps() List~dict~
        +delete_app(app_id: str) bool
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

    class ContextManager {
        -db: WorkspaceStorage
        -app_manager: AppManager
        +build_llm_prompt(session_id: str) List~dict~
        -_prune_message_content(content: str) str
    }

    class AgentParser {
        +parse_widgets(text: str) List~dict~
    }

    class LLMService {
        +generate_agent_response(messages: List~dict~) str
        +call_llm_api(provider: str, model: str, messages: List~dict~, tools: List~dict~|None) dict
    }

    class IntentPlan {
        +kind: IntentKind
        +confidence: float
        +rationale: str
        +app_id: str
        +instruction: str
        +actions: List~dict~
        +query: dict
        +clarification_message: str
        +clarification_options: List~dict~
        +deprecated: bool
        +to_dict() dict
        +from_dict(data) IntentPlan
        +from_tool_call_args(args) IntentPlan
        +tool_schema() dict
    }

    class IntentKind {
        <<enum>>
        +WIDGET_CREATE
        +WIDGET_MODIFY
        +GRAPH_MUTATION
        +GRAPH_QUERY
        +PLAN_AND_ACT
        +CLARIFY
        +CONVERSE
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

    class IntentRouter {
        +route(content, context) IntentPlan
        +route_legacy(content, existing_apps) IntentPlan
    }

    class MutationTicketManager {
        +record(session_id, forward_actions, snapshot_before) MutationTicket
        +pin(session_id, ticket_id) bool
        +rollback(session_id, ticket_id) List~dict~
        +get(session_id, ticket_id) MutationTicket
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
    }

    class PromptManager {
        +prompts_dir: Path
        +env: Environment
        +get_prompt(template_name, kwargs) str
    }

    ChatSession "1" --* "0..*" ChatMessage : contains
    ContextManager --> AppManager : references
    ContextManager --> ChatMessage : queries database
    AgentOrchestrator --> ContextManager : constructs message history
    AgentOrchestrator --> AppManager : references
    AgentOrchestrator --> AgentParser : extracts XML widgets
    AgentOrchestrator --> PromptManager : loads system prompt
    AgentOrchestrator --> IntentRouter : classifies user intent
    AgentOrchestrator --> PlanExecutor : dispatches to coding/mutation
    IntentRouter --> IntentPlan : returns structured plan
    IntentRouter --> RouterContext : consumed
    RouterContext --> GraphSnapshot : embeds snapshot
    IntentPlan --> IntentKind : kind is an enum value
    PlanExecutor <|-- CodingPlanExecutor
    PlanExecutor <|-- MutationPlanExecutor
    MutationPlanExecutor --> MutationTicketManager : records rollback tickets
    MutationTicketManager --> AgentOrchestrator : consumed in graph mutation paths
    LLMService --> LLMAuditLog : writes prompt audit logs
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
*   **IntentPlan / IntentKind (`agent/intent_plan.py`)**: 用 `classify_intent` function-calling 协议引导 LLM 返回的结构化输出。是 `IntentRouter` 与 `AgentOrchestrator` 之间的契约。
*   **IntentRouter (`agent/router.py`)**: 根据消息内容与 RouterContext 产出 IntentPlan。优先用 LLM function-calling，失败时降级为正则+回退到 `converse`，同时收尾处会做 widget_modify 重名检测，必要时降级为 clarify。
*   **PlanExecutor / CodingPlanExecutor / MutationPlanExecutor (`agent/plan_executor.py`)**: 把"计划-审批-执行-校验"流水线抽象为策略类。`CodingPlanExecutor` 包装原有 widget 流；`MutationPlanExecutor` 在用户审批后批量执行 graph_mutation，并把每次调用登记为一个可撤销的 mutation ticket。
*   **MutationTicketManager (`mutation_tickets.py`)**: 为每次 graph_mutation 提供 60s 软默认 + 用户可星标为永久的撤销窗口。撤销逻辑使用 graph 数据库层新增的 `graph_mutation_history` 表。

### 2.3 实时多端同步层 (`main.py` WebSocket)
*   通过长连接管理不同的 Session 连接。
*   一端发送消息时，服务端接收并广播给同一 `session_id` 的所有客户端，实现多端画布、对话气泡的强实时一致性同步。
