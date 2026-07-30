# 意图路由

`IntentRouter` 把用户输入转换为结构化 `IntentPlan`。它决定 durable workflow 走哪条 phase 路径，但不直接执行工具、写 Graph 或发布应用。

## 1. 路由上下文

路由输入由以下信息组成：

- 当前用户消息、会话语言和模型快照；
- 已安装应用的 manifest、intent 与 schema 引用；
- 有上限的 `GraphSnapshot`；
- 可用 capability 摘要；
- fast model（未配置时回退到会话 primary model）。

模型必须通过 `classify_intent` tool schema 返回结构化参数。解析失败、未知 kind、低置信或不安全的旧分类会降级为 `clarify`，不会猜测并执行副作用。

## 2. 顶层 IntentKind

| Kind | 用途 | 主要后续路径 |
| --- | --- | --- |
| `converse` | 普通对话和有界只读 tool loop | Converse phase |
| `graph_query` | 只读结构化查询 | Graph query phase |
| `graph_mutation` | 一批明确 Graph action | preflight → 必要确认 → atomic apply |
| `widget_create` | 创建新应用 | plan → confirm → staging → verify → publish |
| `widget_modify` | 修改已有应用 | plan → confirm → staging → verify → publish |
| `multi_intent` | 有顺序的多个子动作 | 全量预检后按 saga step 执行 |
| `plan_and_act` | 需要显式计划的复合动作 | 与 durable multi-step 路径汇合 |
| `clarify` | 缺少必要信息或无法安全分类 | 创建用户 interaction 或澄清消息 |

`IntentPlan` 还包含 `confidence`、`rationale`，并按 kind 使用 `app_id`、`instruction`、`actions`、`query`、`sub_intents` 或 `clarification_*` 字段。

## 3. SubIntent

`multi_intent` 与 `plan_and_act` 可以包含：

- `converse`
- `graph_mutation`
- `graph_query`
- `widget_create`
- `widget_modify`
- `widget_extend_schema`
- `widget_fix_code`
- `widget_rewrite`

Reducer 在第一个副作用前预检完整列表，然后顺序执行并在每一步 checkpoint。前一步输出可以供后一步使用；失败时根据已持久化的 effect 和 recovery 数据继续或进入 `needs_attention`，而不是依赖旧的内存 DAG。

## 4. 路由与执行的边界

```mermaid
flowchart LR
    Message[用户消息] --> Context[RouterContext]
    Context --> Router[IntentRouter]
    Router --> Plan[IntentPlan]
    Plan --> Reducer[DurableAgentWorkflow]
    Reducer --> Read[只读结果]
    Reducer --> Interaction[用户 interaction]
    Reducer --> Effect[Graph / Tool / OpenCode effect]
```

- 路由结果只是计划，不是授权。
- Graph action 仍须经过 schema preflight。
- Widget 仍须经过 staging、controller 验证和 schema verification。
- Tool/MCP/OpenCode 仍须经过对应权限与 lifecycle policy。
- 同一 Run 使用启动时冻结的模型选择；中途修改会话模型只影响下一个 Run。

## 5. `/` 显式路由命令

`/` 命令是一层轻量路由 DSL，不是新的执行器。前端通过
`GET /api/chat/commands` 获取同一份命令定义、参数结构，以及当前全部 App/Skill ID；
后端重新解析原始消息并编译成 `IntentPlan`，因此不能通过伪造前端 payload 绕过
Router、审批或 durable reducer。

| 命令 | 编译结果 | 参数 |
| --- | --- | --- |
| `/ask` | `converse` | 自然语言指令 |
| `/app` | `widget_modify` | 已安装 App ID + 指令 |
| `/create` | `widget_create` | 新 App ID + 指令 |
| `/query` | `graph_query` | 自然语言查询；受约束 Router 生成结构化 `query` |
| `/mutate` | `graph_mutation` | 自然语言变更；受约束 Router 生成结构化 `actions` |
| `/skill` | `converse` | 已安装 Skill ID + 指令 |

一条消息最多包含 8 个命令。多个命令按书写顺序编译为一个 `multi_intent`，完整预检后
由 saga 顺序执行，例如：

```text
/query 列出未完成任务 /mutate 新建“发布版本”任务 /app planner 增加周视图
```

普通文字出现在第一个命令前时，会作为最前面的 `converse` 子步骤保留。若指令需要包含
形如 `/query` 的字面量，可写成 `\/query`。未知 `/word` 始终保留为普通文本。

命令只提高意图表达精度，不扩大权限：

- `/query` 与 `/mutate` 只约束结构化路由结果；前者仍为只读，后者仍须 Graph
  preflight 与用户确认。
- `/app` 与 `/create` 仍进入计划、Schema、staging、校验和发布流程。
- `/skill` 可在同一消息中出现多次；所有 Skill ID 在 route phase 一次性解析并固定。
  外部 Skill 仍强制进入只读语义沙箱，不能与同一消息中的 effect 命令组合执行。
- 包含多个只读对话步骤时，每一步只看到自己的指令；客户端最终只得到一条按顺序合并的
  durable 回复，恢复不会重复调用或重复投影。

执行细节见 [Agent Harness](/agent/harness.md) 和[持久 Run](/architecture/runs.md)。
