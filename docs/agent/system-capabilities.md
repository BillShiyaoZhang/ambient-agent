# Agent 系统能力目录

Agent 不能靠一段长期手写、容易过期的 prompt 猜测系统能力。系统维护一个结构化、版本化的 `SystemCapabilityCatalog`，并为不同 Agent 角色生成有界说明。本页定义其数据源、投影和使用规则。

## 1. 单一事实来源

Catalog 由运行时代码组合，不从自然语言文档反向解析：

- Capability Ontology：Widget 可申请的类目、scope schema 和 SDK 映射。
- `ambient-context` ontology：可读取/写入的 Graph 实体及属性。
- Tool registry：当前模型真正可调用的本地 tool 与 effect/scope。
- App Store：已安装 capability 的 catalog ID、action 和输入/结果 schema。
- Coding Agent registry：可用性、认证状态、模型约束和 staging policy。
- Durable Run contract：interaction、取消、恢复、幂等和 `needs_attention` 语义。

Catalog 有显式 `catalog_version` 和稳定排序。任何 prompt、UI 或 API 只消费投影，不能维护第二份类目列表。

## 2. 领域模型

```text
SystemCapabilityCatalog
├── runtime_contract
│   ├── durable_runs
│   ├── approvals
│   └── recovery
├── context_graph
│   ├── ontology_id
│   ├── entities
│   └── query/mutation grammar
├── widget_runtime
│   ├── manifest_version
│   ├── capability_categories
│   └── forbidden APIs
├── installed_capabilities
│   └── catalog_id -> actions + schemas
├── model_tools
│   └── name -> input schema + effect + scope
└── coding_agents
    └── id -> availability + model mode + artifact policy
```

每个条目至少包含稳定 ID、说明、availability、effect、机器可读的 `scope_contract`、approval 要求和限制。`scope_contract` 同时声明 required/optional 字段、类型、枚举或数值边界及一个最小合法示例；Schema Alignment Agent 不需要从字段名猜测嵌套结构。Secret、凭据、绝对路径、完整 Graph 记录和无界历史绝不进入 Catalog。

`graph.mutate.edge_types` 是可选字段：省略或空数组都规范化为“仅允许获批 entity 的节点操作，不允许任何边操作”。`network.request.sources` 不是字符串列表，而是以 kebab-case source ID 为键的对象；每个值必须完整声明无凭据 HTTPS origin、精确 path/method 白名单和响应大小上限。文件 scope 使用 `app://data` 下的相对路径模式，不携带 `app://data/` 前缀。

## 3. 按角色投影

| 角色 | 获得的内容 | 不获得的内容 |
| --- | --- | --- |
| Intent Router | 可选 intent、已有 App 摘要、Graph schema、能力是否存在 | Controller 源码、凭据、任意 adapter 内部细节 |
| Converse Agent | 只读 tool schema、Graph/App 摘要、Run 行为 | 写 tool、未批准 Widget grant、直接发布能力 |
| Schema Alignment Agent | 数据 ontology、Capability Ontology、当前 App grants、用户需求 | 运行时 secret、未安装能力的虚构接口 |
| Coding Agent | 精确批准的 schemas/grants、SDK 子集、产物约束、最近有界诊断 | 完整 host SDK、其他 App grants、任意文件/网络访问 |
| Verification Agent | 批准 contract、staging manifest、提取的代码使用 | 重新解释或扩大用户批准的权限 |

投影遵循最小信息原则并带 `catalog_version`；Widget 的精确 `grants_digest` 只随用户批准后的 Runtime Contract 提供给 Coding/Verification。Catalog 会在接受 proposal 时校验 Graph 实体、已安装 catalog ID、action ID 与当前 availability；模型引用不存在或不可用的 ID 时，调用方必须拒绝，不能尝试猜测。

## 4. Prompt 组合

Prompt 模板只描述角色与决策规则，动态能力块由 Catalog renderer 注入：

```text
[SYSTEM CAPABILITY CATALOG v1]
Durable execution: plan -> alignment approval -> staging -> verification -> promotion
Widget grant categories:
- graph.query: entities[]; read only
- graph.mutate: entities[], operations[], edge_types[]
...
Installed actions:
- mcp:calendar:create-event (available; approval required)
[END SYSTEM CAPABILITY CATALOG]
```

Renderer 必须：

- 使用固定字段顺序和确定性序列化，便于 hash、测试与审计。
- 对条目数量、schema 深度和文本长度设上限；超限时给摘要和可检索 ID。
- 明确区分 `available`、`unavailable`、`approval_required` 和 `unsupported`。
- 告诉 Agent 正确替代路径，例如认证网络访问应申请已安装 capability，而不是在 Widget 中嵌入 secret。
- 输出每个类目的完整 `scope_contract`，包括 `network.request.sources` 的嵌套对象结构、文件相对路径规则、Graph operation 枚举和大小上限；不能只输出字段名。

Schema 对齐返回的 JSON 若第一次违反该 contract，服务会把有界的具体校验错误和原响应反馈给同一模型修正一次。修正仍必须重新经过同一个后端 normalizer/authorizer；它不是放宽校验，也不会猜测或自动扩大权限。

## 5. Runtime Contract 给 Coding Agent

进入 staging 前，Workflow 生成不可变 contract：

```json
{
  "contract_version": 1,
  "app_id": "daily-planner",
  "catalog_version": 1,
  "schemas": [{"id": "Task", "properties": {"title": "string"}}],
  "capabilities": [
    {"id": "graph.query", "scope": {"entities": ["Task"]}}
  ],
  "grants_digest": "sha256:...",
  "allowed_files": ["controller.js", "manifest.json", "README.md"]
}
```

Coding Agent 必须原样写入批准 grants；不能新增类目、扩大 scope 或改用被禁止 API。Verifier 使用同一 contract，而不是重新询问模型“看起来是否安全”。

## 6. 更新规则与验收

- 新增系统能力时，先扩展结构化 Catalog/ontology 与测试，再更新 renderer 和对应文档。
- 删除能力时，同时删除 catalog 条目、SDK surface、prompt 旧说明和兼容 handler；不可只标记 deprecated 后永久保留。
- 快照测试验证 Catalog 的稳定字段与顺序；契约测试验证所有文档类目都存在于 ontology。
- Agent prompt 测试验证不同角色只获得允许的投影，并且运行时实际不可用的能力不会被描述为可用。
