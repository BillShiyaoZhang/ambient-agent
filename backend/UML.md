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
        +create_or_update_app(app_id: str, title: str, html: str, css: str, js: str, kwargs) void
        +get_app_files(app_id: str) dict
        +list_apps() List~dict~
        +delete_app(app_id: str) bool
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

    class AgentOrchestrator {
        +db: WorkspaceStorage
        +app_manager: AppManager
        +context_manager: ContextManager
        +run_opencode_agent_acp_fn: function
        +handle_message(session_id: str, content: str, on_update: Callable) tuple
        -_run_callback(callback: Callable, data: Any) void
    }

    class PromptManager {
        +prompts_dir: Path
        +env: Environment
        +get_prompt(template_name, kwargs) str
    }

    ChatSession "1" --* "0..*" ChatMessage : contains
    ContextManager --> AppManager : references
    AppManager --> AppRecordStore : persists lifecycle timestamps
    ContextManager --> ChatMessage : queries database
    AgentOrchestrator --> ContextManager : constructs message history
    AgentOrchestrator --> AppManager : references
    AgentOrchestrator --> AgentParser : extracts XML widgets
    AgentOrchestrator --> PromptManager : loads system prompt
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

### 2.3 实时多端同步层 (`main.py` WebSocket)
*   通过长连接管理不同的 Session 连接。
*   一端发送消息时，服务端接收并广播给同一 `session_id` 的所有客户端，实现多端画布、对话气泡的强实时一致性同步。
