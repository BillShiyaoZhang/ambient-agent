# 图数据可视化

图数据可视化是一套由可信 React Host 使用的共享探索能力。它服务于知识图谱、本体、Agent 编排、隐私数据地图与 Widget Schema 审批，不属于 Widget SDK，也不改变 Graph、Run、Audit 或 Capability 的授权边界。

## 1. 共享图模型与组件

所有场景先投影到同一只读展示模型：

```text
GraphDataset
├── nodes[]: id, label, kind, status, summary, details, badges, action
├── edges[]: id, source, target, label, kind, status, details
└── metadata: title, description, counts, time window, coverage, blind spots
```

`GraphExplorer` 使用成熟的 React Flow 画布实现平移、缩放、拖动、框选、MiniMap、适应视图和键盘操作；业务适配器不复制画布实现。组件还必须提供：

- 按节点名称、类型、摘要和详情搜索；
- 按节点类型显隐，选中节点后聚焦一跳邻居；
- 水平/垂直布局切换与一键恢复完整拓扑；
- 节点和边的详情面板、图例、空状态、错误状态和数据截断提示；
- 状态除颜色外还使用文字/图标表达，并遵循 Host 的亮色、暗色与 reduced-motion 设置；
- 窄屏时画布与详情面板纵向排列，所有控件有 accessible name。

节点位置只是当前浏览器会话的展示状态，不写入 KG、Run 或 Manifest。任何场景都不得从图的显示状态推导权限或业务事实。

## 2. Ontology 与 KG Explorer

系统栏提供“图谱探索”入口，并在一个工作台中区分两个视图：

- **Ontology**：每个规范本体实体是一个节点，`subclass_of` 是有向边；详情显示描述、属性类型、规范 IRI、外部等价 IRI、core/abstract 标记和对应 record 数。
- **Knowledge Graph**：每个 `ContextRecord` 是一个节点，真实 Graph edge 是有向边；详情显示实体类型和 record 属性。基础设施 effect、rollback、migration 与 ontology 存储节点永不出现在此视图。

`GET /api/graph/explorer?record_limit=N` 只供可信系统 Host 使用，响应带 `Cache-Control: no-store`。所有请求必须来自 loopback 或 `AMBIENT_TRUSTED_HOST_PEERS` 显式配置的容器/代理网段；带 `Origin` 的浏览器请求还必须匹配 `AMBIENT_FRONTEND_ORIGINS` 白名单，伪造允许的 Origin 不能绕过 peer 边界。无 `Origin` 的同机原生工具和诊断请求仍可访问。后端通过 Graph adapter 的公开契约读取数据，不能访问 SQLite 私有表；`record_limit` 被限制在安全上限内，并且 KG 响应最多返回 1,000 条关系。响应必须同时给出全量计数、实际返回数、节点/关系上限和 `truncated`，这样有限快照不会被误解为整张 KG。详情值另受字符串、集合、深度和单值字节预算限制；非有限浮点数会替换为明确的 JSON-safe 标记；裁剪通过独立的 `detail_truncation` 元数据和 UI 提示报告，不能混入拓扑 `truncated` 语义。关系只有两个端点都在返回快照中时才会输出。节点 ID、关系端点和关系类型按存储值精确保留，显示文本的清理不会改变图身份。

这个系统视图不授予 Widget Graph 权限，也不绕过 App grant。Widget 仍只能使用获批的 `ambient.graph` scope。

## 3. Agent 编排设计与运行

Agent 编排视图使用同一组件展示 durable workflow：

- 设计视图显示 phase、连接、分支条件、等待用户、终态和返工环；
- 节点详情包含职责、实现文件、symbol 与可打开的源代码链接；
- 运行视图用 Run event 的 `step_id`、当前 checkpoint、结果和 error 覆盖设计图，明确区分未开始、运行中、等待、成功和失败；
- dataset 保留 Run 的 `workflow_type`、`workflow_version` 与可视化 descriptor 版本；版本不一致时继续保留证据节点，但明确提示拓扑与 phase 覆盖可能不准确；
- 缺失 event 不代表 phase 没有执行；详情必须说明事件窗口或旧版本 Run 可能不完整。

任务中心选择一个 Run 后显示运行图，不移除原有输入、结果、artifact 与错误详情。未知 workflow/phase 仍作为可检查节点显示，不能丢弃数据。

## 4. 隐私数据地图

隐私数据地图回答“哪些类别的数据可能或已经流向哪里”，不替代原始 Audit Log。它在请求时从已保留的 Audit metadata 与当前 App Manifest 派生，不创建第二份日志或持久化拓扑。

三种语义必须同时可见且不可混淆：

- `observed`：由当前保留窗口内的运行证据聚合出的流，显示次数、最早/最晚时间与类别；
- `declared`：由 Manifest capability/schema 声明推导的潜在流，只表示声明，不表示权限获批、合规或真实运行；
- `unknown`：覆盖范围之外或未插桩的可能流，表示盲区；缺失 evidence 绝不表示没有数据流。

数据地图的边和详情只包含最小元数据，如本地来源类别、stage、目标 provider/capability 类别、计数和时间。不得包含 prompt、response、tool 参数、record 属性、凭据、原始 provider payload 或它们的片段/摘要/hash。Raw Audit Log 没有到图响应的 payload 路径。

保留、访问和删除遵循以下规则：

- 地图只覆盖源 Audit Log 当前的保留窗口，不延长 retention；
- 仅可信系统 Host 调用 `GET /api/data-map`；浏览器 `Origin` 使用同一显式 Host 白名单，响应 `no-store`，接口不注入 Widget；
- 原始 `GET /api/audit-logs` 使用同一 peer/Origin 双重边界与 `no-store`；无 `Origin` 的原生请求仅允许来自 loopback 或显式可信 peer，避免绕过脱敏地图读取原始 payload；
- Audit Log 清理、App 删除或 Manifest 更新后，下一次派生结果自动消失或变化，没有额外删除流程；
- 响应明确列出时间窗口、证据数、声明 App 数、插桩范围、已知盲区和限制；
- Audit Log 继续承担原始证据检查，Capability authorizer 继续承担执行时权限，数据地图不承担合规判定。

## 5. Widget Schema 审批

Schema 与 Capability 对齐对话框在原有可编辑表单上方增加图形预览：

- 复用实体、新实体、父实体、Graph grant 与其他 capability 使用不同节点类型；
- `subclass_of`、`graph.query`、`graph.mutate` 与 capability scope 使用带标签的边；
- proposal 外的父实体先显示为 `unknown`，避免把未确认引用误标为已存在；dangling grant、重复实体和后端对当前提交返回的缺失父实体错误显示为 warning/error，并阻止批准；
- 用户编辑实体 ID、父实体、属性或 grant 后，图与错误列表立即从同一 proposal 重建；
- 编辑后，上一次提交对应的后端诊断仍作为“已过期”上下文显示，但不永久锁死批准；当前可重算的客户端依赖错误继续阻塞，重新提交时后端仍执行权威校验；
- 图是理解与导航层，不另存 proposal，也不能绕过现有表单、自然语言 refine 或审批动作。

## 6. 验收标准

- 一个共享 `GraphExplorer` 被 Ontology/KG、Agent 编排、隐私数据地图和 Schema 审批适配器复用。
- 用户可以缩放、平移、拖动、搜索、过滤、切换布局、聚焦邻居并检查节点/边详情。
- Ontology 与 KG 使用存储无关且有上限的后端快照，清楚显示完整计数与截断状态。
- Agent 设计图显示连接、条件与实现入口；选中 Run 后可见运行状态、结果与错误。
- 隐私地图区分 `observed`、`declared`、`unknown`，展示窗口与盲区，并且响应中不存在 raw payload。
- Schema 审批编辑会实时更新拓扑，现有 validator 与授权边界保持有效。
- 新行为有纯适配器测试、组件交互测试、API 契约测试；完整前端测试、构建、Ruff、后端测试和文档校验通过。
