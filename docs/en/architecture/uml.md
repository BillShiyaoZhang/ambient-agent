# Backend Class Diagram & Architecture

The backend of Ambient Agent is built on FastAPI + SQLModel (SQLite), supporting multi-client WebSocket synchronization, dynamic widget execution sandboxing, and LLM query audits.

## 1. Class Diagram

## 1. Backend Class Diagrams (Summary-Detail Structure)

To improve readability on web views, the comprehensive class diagram has been split into a high-level system relationship diagram and 5 focused modular class diagrams.

### 1.1 Macro Class Diagram (Summary)

Shows the macro dependencies and relationships between all core backend services and managers:

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

### 1.2 Storage & Session Module (Detail - Storage & Session)

Manages multi-client chat sessions, persistent Widget App source code storage, and SQLite graph CRUD operations:

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

### 1.3 Intent Routing & Context Module (Detail - Intent Routing & Context)

Handles LLM prompt compilation/pruning, user message intent classification, and function routing:

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

### 1.4 Widget DAG & Schema Verification Module (Detail - Widget DAG & Verification)

Drives the Widget lifecycle compilation process using a Directed Acyclic Graph (DAG) runtime, and verifies code data schemas:

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

### 1.5 Execution & Orchestration Module (Detail - Execution & Orchestration)

Coordinates overall orchestration, schedules plan execution, registers database mutation rollback tickets, and integrates LLM clients:

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

### 1.6 External Integration & MCP Module (Detail - MCP & Integration)

Launches, caches, and routes JSON-RPC 2.0 requests to local Model Context Protocol (MCP) CLI processes:

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

## 2. Core Modules Description

### 2.1 Database Entity Layer (`models.py`)
*   **ChatSession**: Manages chat sessions across multiple devices.
*   **ChatMessage**: Stores dialog history supporting multiple sender roles (`user`, `agent`, `code`, `system`).
*   **LLMAuditLog**: Records raw payloads and responses exchanged with LLM providers for the audit log dashboard.

### 2.2 Service & Control Logic
*   **AppManager**: Coordinates dynamically compiled widget files (index.html, style.css, controller.js) in the local filesystem.
*   **ContextManager**: Prunes redundant message content and injects active widget source codes as prompts.
*   **AgentParser**: Extracts `<ambient-widget>` XML blocks from LLM stream responses using regex.
*   **IntentRouter**: Evaluates user prompts to output a structured `IntentPlan` indicating intended system actions.
*   **WidgetDAG**: Coordinates widget build cycles utilizing collapsible, linear task nodes with target invalidations.
