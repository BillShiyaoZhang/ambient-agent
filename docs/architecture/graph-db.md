# 图数据库

`GraphDatabase` 使用 `workspace/graph.db`（SQLite + WAL）保存 Widget 与 Agent 共享的结构化数据。`graph.json` 仅用于一次性旧数据迁移，迁移后会改名为备份。

## 1. 表与职责

| 表 | 主键 | 职责 |
| --- | --- | --- |
| `graph_schemas` | `id` | schema 名称、描述、属性类型与 core 标记 |
| `graph_nodes` | `id` | 节点类型、JSON properties、namespace、时间戳 |
| `graph_edges` | `(from_id, to_id, type)` | 有向关系与 JSON properties |
| `graph_mutation_history` | `id` | 正向/反向 action、执行前快照、pin/consume 状态 |
| `graph_effects` | `idempotency_key` | action 输入 hash 与已提交结果，防止重复副作用 |

内置 core schema 为：

- `Task`: `title`, `description`, `status`, `due_date`（均为 string）；
- `Event`: `title`, `description`, `start_time`, `end_time`, `location`（均为 string）；
- `Note`: `title`, `content`, `tags`（均为 string）。

应用可以提议扩展 schema，但 core schema 不能通过普通删除流程移除。

## 2. 查询

Widget 使用 `ambient.graph.subscribe(query, callback)` 注册实时查询。后端的 `GraphSubscriptionManager` 保存订阅，在 mutation 后重新执行查询并推送变化。取消订阅函数会注销 WebSocket 持久消息与浏览器监听器。

Agent 只读查询经 `graph_query_engine.execute_graph_query` 执行。路由前的 `RouterContext` 会创建有上限的 `GraphSnapshot`，避免把整个数据库放入 Prompt。

## 3. Mutation

公开 action 为：

- `create_node`
- `update_node_property`
- `delete_node`
- `create_edge`
- `delete_edge`

`preflight_actions` 先解析临时节点依赖、检查端点与 schema 类型；`apply_actions_atomic` 在一个 transaction 中提交整批 action，并生成反向 action。任一 action 失败会回滚整批操作。

Widget 的 HTTP mutation 自动携带 `widget:<app-id>:<uuid>` idempotency key。Durable workflow 使用自己的稳定 effect key。相同 key 配相同输入返回已提交结果；相同 key 配不同输入会报错。

## 4. Schema 验证与回滚

Widget 创建/修改流程从 `controller.js` 提取 subscribe、query 和 mutate 使用，生成 `VerificationDiff`。缺失类型或属性通过 schema proposal 与用户确认处理；批准的 schema 变更和应用发布都有恢复快照。

Mutation history 保存 forward/reverse actions。`MutationTicketManager` 可以展示预览、回滚、pin 或 consume ticket；回滚本身也经过后端检查。内部 `replace_node` 与 `restore_node` 只用于反向操作，不属于公开 Widget action。

## 5. 约束

- 所有公开节点写入必须符合已注册 schema 的属性名和类型。
- Edge 端点必须存在，或在同一批 action 中先创建。
- 前端声明和 manifest 的 `schema_refs` 只提供上下文，不是授权；最终校验始终在后端。
- 不要直接写 SQLite，也不要生成已弃用的 `ambient.model` 或 `graph.json` 接口。
