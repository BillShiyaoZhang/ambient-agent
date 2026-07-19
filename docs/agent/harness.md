# Agent Harness 架构与执行流程

本文档介绍了 `backend/agent/` 目录下重构后的 Agent Harness 框架的设计与执行序列。

## 1. 组件关系图

Agent Harness 实现了执行流、意图路由、上下文组装以及大模型通信之间的解耦。以下是各组件之间的关系：

```mermaid
graph TB
    ChatSession -->|"contains"| ChatMessage
    ContextManager -->|"references"| AppManager
    ContextManager -->|"queries database"| ChatMessage
    AgentOrchestrator -->|"constructs message history"| ContextManager
    AgentOrchestrator -->|"references"| AppManager
    AgentOrchestrator -->|"references"| WorkspaceStorage
    AgentOrchestrator -->|"extracts XML widgets"| AgentParser
    AgentOrchestrator -->|"loads system prompt"| PromptManager
    AgentOrchestrator -->|"classifies user intent (LLM #1) & refines sub_intents (LLM #2)"| IntentRouter
    AgentOrchestrator -->|"widget build pipeline"| WidgetDAG
    AgentOrchestrator -->|"dispatches to coding/mutation"| PlanExecutor
    AgentOrchestrator -->|"structured diff (Direction A)"| SchemaVerificationService
    IntentRouter -->|"returns structured plan"| IntentPlan
    IntentRouter -->|"consumed"| RouterContext
    IntentPlan -->|"0..* sub_intents"| SubIntent
    SubIntent -->|"kind enum"| SubIntentKind
    IntentPlan -->|"kind enum"| IntentKind
    RouterContext -->|"embeds snapshot"| GraphSnapshot
    CodingPlanExecutor -->|"inherits"| PlanExecutor
    MutationPlanExecutor -->|"inherits"| PlanExecutor
    MutationPlanExecutor -->|"records rollback tickets"| MutationTicketManager
    MutationTicketManager -->|"consumed in graph mutation paths"| AgentOrchestrator
    WidgetDAG -->|"0..* nodes"| TaskNode
    TaskNode -->|"runs produce results"| TaskResult
    SchemaVerificationService -->|"returns"| VerificationDiff
    LLMService -->|"writes prompt audit logs"| LLMAuditLog
    LLMConfigStore -->|"resolves provider profiles"| ModelRunContext
    AgentOrchestrator -->|"uses primary/fast snapshots"| ModelRunContext
    ModelRunContext -->|"injects selected model"| OpenCodeACP
    PlanExecutor -->|"returns"| PlanPhaseResult
```

## 2. 消息执行逻辑流程图

下面的流程图展示了 `AgentOrchestrator.handle_message()` 在处理来自 WebSocket 的新消息时的详细执行顺序：

```mermaid
graph TD
    User([用户 WebSocket 消息]) --> WS[main.py: websocket_chat]
    WS -->|实例化| Orch[harness.py: AgentOrchestrator]
    WS -->|调用 handle_message| Orch

    Orch -->|1. 收集上下文| CtxBuild[router_context.py: RouterContext.build]
    CtxBuild -->|app_manifests + GraphSnapshot| Router
    Orch -->|2. 路由 LLM#1| Router[router.py: IntentRouter.route]
    Router -->|function-calling| LLMR[LLM classify_intent]
    LLMR -->|IntentPlan| Plan

    Plan -->|kind=MULTI_INTENT / PLAN_AND_ACT| Refiner[router.py: refine_sub_intents LLM#2]
    Refiner -->|refined sub_intents| PlanRefined

    PlanRefined -->|widget_create / widget_modify| BuildFlow[widget DAG 流水线]
    PlanRefined -->|graph_mutation| MutFlow[graph_mutation 直路]
    PlanRefined -->|graph_query| QueryFlow[graph_query 直路]
    PlanRefined -->|plan_and_act| PlanFlow[plan_and_act 通用流]
    PlanRefined -->|multi_intent| MultiFlow[multi_intent 按 sub_intent 分发]
    PlanRefined -->|clarify| ClarifyFlow[澄清回弹给用户]
    PlanRefined -->|converse| ConvFlow[常规对话流]

    %% Widget DAG (Direction B)
    subgraph BuildFlow [Widget DAG: 6 个任务节点]
        B1[plan 生成计划]
        B2[align_schemas 数据库 Schema 对齐]
        B3[regen_code OpenCode 生成代码]
        B4[verify SchemaDiff 结构化校验]
        B5[decode_user_intent 解读用户反馈]
        B6[apply_user_actions 应用扩展/修复]
        B1 --> B2 --> B3 --> B4
        B4 -.rework_code/schema/plan.-> B5
        B5 --> B6
        B6 -.invalidates regen_code + verify.-> B3
    end

    %% graph_mutation 流
    subgraph MutFlow [graph mutation 直路]
        M1[MutationTicketManager.record]
        M2[subscription_manager.broadcast_updates]
        M1 --> M2
    end

    %% graph_query 流
    subgraph QueryFlow [graph query 直路]
        Q1[execute_graph_query]
        Q2[渲染文本回复]
        Q1 --> Q2
    end

    %% plan_and_act 流
    subgraph PlanFlow [plan_and_act 通用流]
        P1[MutationPlanExecutor.run_plan]
        P2[等待 plan_approval_request]
        P3[批量执行 actions]
        P1 --> P2 --> P3
    end

    %% multi_intent 流
    subgraph MultiFlow [multi_intent 按 sub_intent 顺序执行]
        MI1[对每个 sub_intent 选择 SubExecutor]
        MI2[graph_mutation → _handle_graph_mutation]
        MI3[graph_query → _handle_graph_query]
        MI4[widget_* → _handle_widget_build_sub]
        MI1 --> MI2
        MI1 --> MI3
        MI1 --> MI4
    end

    Orch -->|返回 Tuple: agent_msg, widget_to_send| WS
    WS -->|最终应答消息| User
    WS -->|若有 widget 数据| User
    WS -->|mutation_preview / rollback 响应| User
```

## 3. 关键变更（与之前版本对比）

### Direction A：结构化 Schema Diff

`backend/schema_verification.py` 的 `verify()` 旧接口现在通过 `diff()` 提供
**结构化输出**：`VerificationDiff` 对象，列出
`unknown_props[]` / `type_mismatches[]` / `unknown_types[]`。

前端可以基于 `VerificationDiff.to_per_field_payload()` 渲染 per-field
checkbox 列表，让用户逐字段确认是否要扩展 Schema。

### Direction B：Widget DAG 运行时

替换了 `while current_state != "done"` 状态机，引入 `WidgetDAG`（在
`backend/agent/dag.py`），6 个任务节点：

| 节点                 | 作用                                                      |
| -------------------- | --------------------------------------------------------- |
| `plan`               | 调 `PlanGenerationService` 生成计划，问用户审批           |
| `align_schemas`      | 调 `SchemaAlignmentService` 对齐数据库 schema，问用户审批 |
| `regen_code`         | 调 `run_opencode_agent_acp` 生成代码                      |
| `verify`             | 调 `SchemaVerificationService.diff` 结构化校验            |
| `decode_user_intent` | 解读用户 rework 反馈                                      |
| `apply_user_actions` | 应用 schema 扩展 / 代码修复                               |

节点的 `invalidates` 字段定义了"本节点重跑时哪些下游节点也要重 dirty"，
所以下游节点自动跟随。

### Direction D：Multi-Intent Router

`IntentKind` 新增 `MULTI_INTENT`；`SubIntent` 数据类 + `SubIntentKind` 枚举
定义每条 sub-action。两层 LLM：

1. `IntentRouter.route()` 用 `classify_intent` 函数 schema 调用 LLM #1
   获取顶层 `kind` + `sub_intents[]`。
2. 当 `kind ∈ {MULTI_INTENT, PLAN_AND_ACT}`，harness 调用
   `IntentRouter.refine_sub_intents()`（LLM #2），用 `refine_sub_intent.md`
   把 `sub_intents` 细化成具体 actions / extend_schema_props。

每次 `handle_message()` 启动时固定会话主模型和快速模型快照。顶层意图路由使用快速模型；
refine、计划、Schema、校验和最终对话使用主模型。会话在运行中切换模型只影响下一次请求。
Widget 代码生成启动 OpenCode ACP 子进程时，会用进程级 `OPENCODE_CONFIG_CONTENT` 注入同一主模型
及其临时凭据/端点；该配置不写入项目文件，也不会改变并行会话的模型。

`AgentOrchestrator._handle_multi_intent` 按 `sub_intents` 顺序分发给
各 SubExecutor。

## 4. 目录结构说明

- [backend/agent/__init__.py](../../backend/agent/__init__.py): Python 包初始化文件。
- [backend/agent/harness.py](../../backend/agent/harness.py): 实现核心编排器 `AgentOrchestrator`，负责串联整体生命周期。
- [backend/agent/dag.py](../../backend/agent/dag.py): 轻量级 runtime DAG（plan/align_schemas/code/verify/decode/apply），由 harness 在 widget 路径上驱动。
- [backend/agent/router.py](../../backend/agent/router.py): 实现意图路由 `IntentRouter`，两层 LLM（`route` + `refine_sub_intents`）。
- [backend/agent/intent_plan.py](../../backend/agent/intent_plan.py): `IntentPlan` 与 `IntentKind` 枚举，新增 `SubIntent` + `SubIntentKind`；含 function-calling schema。
- [backend/agent/plan_executor.py](../../backend/agent/plan_executor.py): 抽象 `PlanExecutor` 与 `CodingPlanExecutor` / `MutationPlanExecutor` 实现，对应 widget / graph mutation 流水线。
- [backend/schema_diff.py](../../backend/schema_diff.py): 结构化 SchemaDiff 数据类 + JS 提取器（regex-first，括号配对）。
- [backend/schema_verification.py](../../backend/schema_verification.py): 旧 `verify()` 接口保留（返回 markdown 文本），新增 `diff()` 返回结构化 `VerificationDiff`。
- [backend/agent/providers.py](../../backend/agent/providers.py): 面向对象封装的大模型服务客户端。
- [backend/agent/tools.py](../../backend/agent/tools.py): Hermes 风格的工具注册表。
- [backend/router_context.py](../../backend/router_context.py): 收集路由所需的轻量级上下文。
- [backend/mutation_tickets.py](../../backend/mutation_tickets.py): graph_mutation 撤销票。

## 5. 测试覆盖

| 模块                      | 测试文件                                                | 测试数 |
| ------------------------- | ------------------------------------------------------- | ------ |
| Schema Diff               | `tests/backend/test_schema_diff.py`                     | 13     |
| Widget DAG                | `tests/backend/test_dag.py`                             | 5      |
| IntentPlan / SubIntent    | `tests/backend/test_intent_plan.py`                     | 10     |
| Router（含 multi_intent） | `tests/backend/test_router.py` + `test_multi_intent.py` | 17     |
| Harness / rework loops    | `tests/backend/test_rework_loops.py` 等                 | 12     |

总计 **57 个核心单元/集成测试全部通过**（全量后端测试集共计 200+ 个测试全部通过）。
