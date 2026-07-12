# Agent Harness 架构与执行流程

本文档介绍了 `backend/agent/` 目录下重构后的 Agent Harness 框架的设计与执行序列。

## 1. 组件关系图

Agent Harness 实现了执行流、意图路由、上下文组装以及大模型通信之间的解耦。以下是各组件之间的关系：

```mermaid
classDiagram
    class WorkspaceStorage {
        +workspace_dir: str
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

    class AgentOrchestrator {
        +db: WorkspaceStorage
        +app_manager: AppManager
        +context_manager: ContextManager
        +run_opencode_agent_acp_fn: Callable
        +handle_message(session_id, content, on_update) Tuple
    }

    class IntentRouter {
        +route(content, existing_apps) Tuple
    }

    class BaseLLMProvider {
        <<abstract>>
        +model: str
        +generate(messages, db_session, tools) str
        #_log_to_db(db_session, provider, prompt, response) void
    }

    class ToolRegistry {
        +tools: Dict
        +schemas: Dict
        +register(func) Callable
        +execute(name, args, context) Any
    }

    class ContextManager {
        +build_llm_prompt(session_id) List
    }

    class AppManager {
        +list_apps() List
        +get_app_files(app_id) Dict
        +create_or_update_app(app_id, title, html, css, js) void
    }

    class PromptManager {
        +prompts_dir: Path
        +env: Environment
        +get_prompt(template_name, kwargs) str
    }

    AgentOrchestrator --> IntentRouter : 消息意图分类
    AgentOrchestrator --> BaseLLMProvider : 请求模型生成
    AgentOrchestrator --> ContextManager : 组装上下文 Prompt
    AgentOrchestrator --> AppManager : 读写小程序文件
    AgentOrchestrator --> PromptManager : 加载系统提示词
    IntentRouter --> PromptManager : 加载路由提示词
    BaseLLMProvider <|-- OllamaProvider : 继承扩展
    BaseLLMProvider <|-- CloudLLMProvider : 继承扩展
```

---

## 2. 消息执行逻辑流程图

下面的流程图展示了 `AgentOrchestrator.handle_message()` 在处理来自 WebSocket 的新消息时的详细执行顺序：

```mermaid
graph TD
    User([用户 WebSocket 消息]) --> WS[main.py: websocket_chat]
    WS -->|实例化| Orch[harness.py: AgentOrchestrator]
    WS -->|调用 handle_message| Orch

    Orch -->|1. 收集上下文| CtxBuild[router_context.py: RouterContext.build]
    CtxBuild -->|app_manifests + GraphSnapshot| Router
    Orch -->|2. 路由| Router[router.py: IntentRouter]
    Router -->|function-calling| LLMR[LLM classify_intent]
    LLMR -->|IntentPlan| Plan

    Plan -->|widget_create / widget_modify| CodingFlow[代码生成流]
    Plan -->|graph_mutation| MutFlow[graph_mutation 直路]
    Plan -->|graph_query| QueryFlow[graph_query 直路]
    Plan -->|plan_and_act| PlanFlow[plan_and_act 通用流]
    Plan -->|clarify| ClarifyFlow[澄清回弹给用户]
    Plan -->|converse| ConvFlow[常规对话流]

    %% 代码生成流
    subgraph Coding Pipeline [小程序代码执行流水线]
        CodingFlow -->|执行 PlanExecutor| CodingExec[plan_executor.py: CodingPlanExecutor]
        CodingExec -->|2a. 计划阶段| PlanGen
        CodingExec -->|3. Schema 对齐| SchemaAlign
        CodingExec -->|4. 运行 OpenCode| OC[opencode_service: run_opencode_agent_acp]
        CodingExec -->|5. 持久化消息| DB[(SQLite 数据库)]
    end

    %% graph_mutation 流
    subgraph Mutation Pipeline [graph mutation 直路]
        MutFlow --> MutMgr[mutation_tickets.py: MutationTicketManager]
        MutExec[harness._handle_graph_mutation] -->|写入| GraphDb[(graph.db)]
        MutExec --> MutMgr
        MutMgr -->|登记+广播| WSPreview[WS mutation_preview]
    end

    %% graph_query 流
    subgraph Query Pipeline [graph query 直路]
        QueryFlow --> QExec[harness._handle_graph_query]
        QExec -->|execute_graph_query| GraphDb
    end

    %% plan_and_act 流
    subgraph Plan_and_Act Pipeline [plan_and_act 通用流]
        PlanFlow --> MutPlanExec[plan_executor.py: MutationPlanExecutor]
        MutPlanExec -->|计划审批| WSApproval[WS plan_approval_request]
        MutPlanExec -->|用户同意后批量执行| GraphDb
        MutPlanExec --> MutMgr
    end

    Orch -->|返回 Tuple: agent_msg, widget_to_send| WS
    WS -->|最终应答消息| User
    WS -->|若有 widget 数据| User
    WS -->|mutation_preview / rollback 响应| User
```

---

## 3. 目录结构说明

重构后的代码包结构如下：

- [__init__.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/__init__.py): Python 包初始化文件。
- [harness.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/harness.py): 实现核心编排器 `AgentOrchestrator`，负责串联整体生命周期。
- [router.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/router.py): 实现意图路由 `IntentRouter`，使用 function-calling 协议产出 `IntentPlan`。
- [intent_plan.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/intent_plan.py): `IntentPlan` 与 `IntentKind` 枚举，含 function-calling schema。
- [plan_executor.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/plan_executor.py): 抽象 `PlanExecutor` 与 `CodingPlanExecutor` / `MutationPlanExecutor` 实现，对应 widget / graph mutation 流水线。
- [providers.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/providers.py): 面向对象封装的大模型服务客户端（包含 Ollama 本地服务和 OpenAI 兼容云端 API）。
- [tools.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/tools.py): 类似 Hermes 风格的工具注册表，支持解析 Python 函数的签名和 docstring 自动生成工具 Schema。
- [router_context.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/router_context.py): 收集路由所需的 widget inventory 与 Graph 状态摘要 (`GraphSnapshot.from_db`)，并渲染为 prompt 片段。
- [mutation_tickets.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/mutation_tickets.py): 每次 graph_mutation 都生成一个 60s 软默认 + 用户星标永久的撤销 ticket，配合 SQLite 的 `graph_mutation_history` 表实现可撤销数据变更。
