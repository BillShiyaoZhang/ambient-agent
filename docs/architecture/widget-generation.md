# Widget 生成信息契约

本页定义 Widget 从用户意图到发布、运行观测的端到端信息协议。目标不是增加更多 prompt，而是让 Plan、Schema、Capability、代码和验证报告共享同一份可校验的设计事实，任何编辑都能明确失效哪些下游产物。

## 1. 已发现的结构问题

当前生产链路是 `Plan → Plan 审批 → Schema/Capability → Schema 审批 → Runtime Contract → Coding → Verify → Promote`。它有四个系统性缺口：

1. Plan 在没有 Schema、Capability Catalog、现有 Manifest 与数据流信息时生成，只描述 UI，无法证明功能可由最终授权实现。
2. Schema 与 Capability 虽在同一 proposal 中，但 UI 编辑 Schema 时没有维护 grant 引用。删除实体可能留下悬空的 `graph.query` / `graph.mutate` scope。
3. Runtime Contract 表达授权范围，却没有完整表达 SDK 调用语法。比如 grant operation `create` 对应 controller action `create_node`，两者不是同一个 payload 值。
4. Coding、静态校验和 Schema 校验产生的诊断没有统一的结构化修复分类，容易在多个 `/repair` Run 中重复同一错误。

实际天气 Widget 历史验证了这四点：用户删除四个新实体后，Graph grant 仍引用它们，Run 在批准后终止；后续 Coding Agent 又把获批 operation `create` 误写为 `action: "create"`，连续多次未通过 capability verifier。

## 2. 设计决策：联合生成、原子批准

Plan、Schema 与 Capability 应联合生成，并作为一个 `WidgetDesignSpec` 原子批准。这里的“一起”不是把三段自然语言拼进一次模型输出，而是共享 feature ID、数据流和约束的同一份结构化设计：

```json
{
  "design_version": 1,
  "app_id": "weather-app",
  "goal": "展示并保存天气信息",
  "features": [
    {
      "id": "forecast",
      "acceptance": ["显示未来七天预报", "请求失败时可重试"],
      "data_flow_ids": ["forecast-network", "forecast-history"]
    }
  ],
  "data_flows": [
    {
      "id": "forecast-network",
      "source": {"kind": "network", "source_id": "open-meteo", "path": "/v1/forecast", "method": "GET"},
      "sink": {"kind": "ui"},
      "freshness": "manual_refresh",
      "failure_ui": "inline_retry"
    },
    {
      "id": "forecast-history",
      "source": {"kind": "feature", "feature_id": "forecast"},
      "sink": {"kind": "graph", "entity": "Document", "operation": "create"},
      "fields": ["title", "content", "captured_at"]
    }
  ],
  "plan": {"ui_summary": "...", "non_goals": [], "degradations": []},
  "schema_proposal": {"reused_schemas": [], "new_schemas": []},
  "capability_proposal": [],
  "coverage": [
    {"feature_id": "forecast", "status": "covered", "evidence": ["forecast-network", "forecast-history"]}
  ]
}
```

联合设计带来三项保证：

- 每项用户可见功能都能追溯到数据来源、Schema 字段和 grant scope。
- 用户修改 Schema 或权限时，系统能指出受影响的 feature，而不是让 Coding Agent 猜测。
- 审批固化的是规范化设计 digest；Coding Agent 不能在 Manifest 或代码中扩大它。

## 3. 各环节必须携带的信息

| 环节 | 必需输入 | 必需输出 | 确定性检查 |
| --- | --- | --- | --- |
| Intent | 用户原文、会话语言、目标 App、现有 App/Manifest 摘要、最近诊断 | 结构化目标、修改类型、app_id、验收意图、待澄清项 | app_id、目标存在性、操作类型 |
| Design synthesis | Intent、现有 ontology、Capability Catalog、当前 grants、可用 installed capabilities、运行边界 | `WidgetDesignSpec`：feature、data flow、Plan、Schema、grant、coverage、degradation | 结构、引用、版本、最小权限 |
| Design approval | 完整 spec、相对当前发布版本的 diff、风险、lint 结果 | 用户编辑后的完整 spec、approval identity、design digest | 所有引用闭合；无 blocker |
| Contract compile | approved design、catalog/ontology 版本 | 不可变 Runtime Contract、Manifest 模板、SDK call contract、design digest | 规范化 grant、digest、scope 子集 |
| Coding | 用户目标、approved design、Runtime Contract、现有允许文件、精确 SDK grammar、最近结构化诊断 | staging artifact、代码生成 provenance | 只写允许文件；Manifest 等于 contract |
| Static verify | staging、Runtime Contract、SDK AST rules | capability/syntax/security findings | 代码使用是 grants 子集；所有资源标识可静态证明 |
| Schema verify | controller AST、approved effective schemas、data-flow fields | 字段/type diff、feature 影响范围 | 实体与属性存在；类型兼容 |
| Repair | 原 artifact、finding code、位置、expected/observed、修复类别、剩余预算 | 同一 staging 的新 revision | finding 是否消失；禁止扩大 contract |
| Promote | verified artifact、schema proposal、contract/digest、幂等键 | 原子发布结果、artifact hash、schema snapshot、审计事件 | 最后一次 TOCTOU 重验；失败补偿 |
| Observe | runtime error、feature/data-flow ID、manifest revision、contract digest | 有界诊断、重试/重新设计建议 | 脱敏、聚合、版本关联 |

## 4. 联合设计的 lint 规则

进入审批前和用户每次编辑后都运行同一个纯函数 validator：

1. `graph.query` / `graph.mutate` 的 entity 必须存在于复用或新增 Schema 集合。
2. data flow 的 Graph entity、字段和 operation 必须被 Schema 与 grant 同时覆盖。
3. network flow 的 source/path/method 必须精确存在于 `network.request` scope。
4. installed capability flow 的 catalog/action 必须当前可用并获批。
5. 每个 feature 至少有一条完整 data flow；无法实现时必须标记 `degraded` 或 `blocked`，不能静默使用假数据。
6. App 私有缓存、UI state、secret 和 provider raw payload 不得建模为用户上下文本体。
7. 新设计相对现有 App 的权限只能在审批中改变；Coding 与 Repair 只能使用已批准子集。

lint 结果应是结构化 finding：`code`、`severity`、`path`、`feature_ids`、`message`、`suggested_actions`，不能只返回异常字符串。

## 5. 编辑与失效传播

| 变更 | 必须失效或重算 |
| --- | --- |
| 用户目标/验收标准 | 整个 design、contract、code、所有 verification |
| feature/Plan | 关联 data flow、Schema/grant coverage、contract、code、verification |
| Schema 实体 ID 删除/重命名 | Graph flow、Graph grant 引用、coverage、contract、code、verification |
| Schema 属性 | 对应 data flow field、contract、code、Schema verification |
| Capability scope | coverage/degradation、contract、code、static verification |
| Catalog/ontology 版本 | design lint、contract；不兼容时重新审批 |
| Controller 修复 | static/schema verification；不重新审批未改变的 design |

UI 对安全且确定的引用变换可以自动执行：重命名实体时同步重命名 Graph scope；删除实体时从 scope 中删除引用并移除空 grant。任何会增加权限或改变功能语义的修复必须回到联合设计审批。

## 6. Runtime Contract 的职责

Runtime Contract 是 approved design 编译出的不可变执行信封，至少包含：

- `app_id`、`design_digest`、`contract_version`、`catalog_version`、`ontology_revision`；
- 完整 effective schemas 与规范化 capability grants；
- Manifest V2 完整模板及其安全字段映射；
- SDK call contract，包括 `graph.mutate` 的 action DSL、literal 要求和允许的 host surface；
- allowed files、资源上限与验证策略版本。

Grant operation 与 SDK action 必须分别表达。例如 `graph.mutate.operations=["create"]` 授权 `{action:"create_node"}`，不得让模型从单词相似性推断 payload。

## 7. 验证与修复闭环

验证顺序固定为：

1. Artifact/Manifest shape。
2. Controller syntax 与禁止 API。
3. Capability AST subset。
4. Schema/entity/property/type diff。
5. 可选的受控 smoke test。
6. promotion 前重新计算 artifact、contract 与 grants digest。

所有自动修复在同一 staging、同一 Run 中有界执行。finding 必须进入 repair prompt，并记录 signature；同一 signature 连续出现时升级策略：第一次局部修复，第二次要求全文件同类扫描，第三次判定 contract/design 不可满足并返回联合设计，而不是无限生成或要求用户反复输入 `/repair`。

Capability、安全边界和未知实体错误不可 bypass。只有不影响 Graph 写入合法性的展示级警告可以由用户显式接受。

## 8. Durable state

Widget checkpoint 应保存：

- `design_candidate`、`design_revision`、`design_findings`；
- `approved_design`、`design_digest`、批准者与时间；
- `runtime_contract`、contract digest、catalog/ontology 版本；
- `staged_app`、artifact revision/hash；
- `verification_findings`、finding signatures、repair count；
- promotion effect ledger 与 schema snapshot。

状态机不再把 Plan 和 Schema 当作彼此独立的事实。旧字段可在迁移期投影为 `plan_candidate` / `schema_candidate`，但唯一真相是 versioned design。

## 9. 迁移顺序

1. 立即：Schema 编辑同步 Graph grant 引用；后端把无效编辑返回审批而非终止 Run；Catalog 与 prompt 发布精确 SDK grammar。
2. 近期：引入 `WidgetDesignSpec`、统一 lint 与 design digest；现有两个审批框先改为读取同一 design candidate。
3. 中期：合并为一个原子 Design Approval，并加入 feature/data-flow/coverage UI。
4. 后续：结构化 repair finding、同 Run 自动修复、受控 smoke test与运行诊断回流。

第一阶段不改变授权边界，只减少无效审批和错误诊断；第二、三阶段需要提升 durable workflow version，并为等待中的旧 Run 保留兼容 reducer。
