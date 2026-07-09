# Agent Harness 架构与执行流程

本文档介绍了 `backend/agent/` 目录下重构后的 Agent Harness 框架的设计与执行序列。

## 1. 组件关系图

Agent Harness 实现了执行流、意图路由、上下文组装以及大模型通信之间的解耦。以下是各组件之间的关系：

```mermaid
classDiagram
    class AgentOrchestrator {
        +db: Session
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
        +generate(messages, db_session) str
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

    AgentOrchestrator --> IntentRouter : 消息意图分类
    AgentOrchestrator --> BaseLLMProvider : 请求模型生成
    AgentOrchestrator --> ContextManager : 组装上下文 Prompt
    AgentOrchestrator --> AppManager : 读写小程序文件
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
    
    Orch -->|1. 识别并路由意图| Router[router.py: IntentRouter]
    Router -->|is_coding = True| CodingFlow[代码生成流: 创建/修改小程序]
    Router -->|is_coding = False| ConvFlow[常规对话流: 问答/闲聊]
    
    %% 代码生成流
    subgraph Coding Pipeline [小程序代码执行流水线]
        CodingFlow -->|2. 运行 OpenCode 命令行客户端| OC[opencode_service: run_opencode_agent_acp]
        OC -->|实时推送执行日志| WS
        OC -->|修改磁盘代码文件| AppsDir[(backend/apps/)]
        Orch -->|3. 读取最新生成的代码文件| AppMgr[app_manager.py: AppManager]
        AppMgr -->|读取文件| AppsDir
        Orch -->|4. 持久化 ChatMessage & CodeMessage| DB[(db.sqlite3 数据库)]
    end
    
    %% 常规对话流
    subgraph Conversational Pipeline [常规问答执行流水线]
        ConvFlow -->|2. 组装对话上下文| ContextMgr[context_manager.py: ContextManager]
        ContextMgr -->|查询历史消息| DB
        ContextMgr -->|注入活跃小程序源码| AppMgr
        Orch -->|3. 调用模型生成应答| Provider[providers.py: BaseLLMProvider]
        Provider -->|请求大模型 API| LLM[Ollama / 云端大模型]
        Provider -->|记录审计日志| DB
        Orch -->|4. 解析返回文本中的 XML 标签| Parser[agent_parser.py: parse_widget_from_text]
        Orch -->|5. 若包含标签则写入小程序到磁盘| AppMgr
        Orch -->|6. 持久化 Agent 应答 & Code 消息| DB
    end

    Orch -->|返回 Tuple: agent_msg, widget_to_send| WS
    WS -->|发送最终文本应答消息| User
    WS -->|若有更新则发送最新的 widget 数据| User
```

---

## 3. 目录结构说明

重构后的代码包结构如下：

- [__init__.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/__init__.py): Python 包初始化文件。
- [harness.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/harness.py): 实现核心编排器 `AgentOrchestrator`，负责串联整体生命周期。
- [router.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/router.py): 实现意图路由 `IntentRouter`，进行消息类别识别。
- [providers.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/providers.py): 面向对象封装的大模型服务客户端（包含 Ollama 本地服务和 OpenAI 兼容云端接口）。
- [tools.py](file:///Users/shiyaozhang/Developer/ambient-agent/backend/agent/tools.py): 类似 Hermes 风格的工具注册表，支持解析 Python 函数的签名和 docstring 自动生成工具 Schema。
