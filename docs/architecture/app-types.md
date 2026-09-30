# App 类型与功能标准

App 是 Agent 可使用的数据与处理能力，以及用户可操作的界面。类型是用户认识和发现 App 的稳定入口；相同类型可以有不同实现、界面和功能组合。类型目录可扩展，一个 App 可以声明多个类型。类型不决定权限，也不安装私有 ontology。

## 契约

Manifest V2 新增可选 `app_spec`。缺少该字段的旧 App 保持可读、可运行，显示为未分类；不从名称、Schema 或权限推断类型。类型标准独立版本化，当前 `spec_version` 为整数 `1`。

```json
{
  "app_spec": {
    "spec_version": 1,
    "types": ["calendar", "tasks"],
    "features": [
      {"id": "calendar.events", "status": "implemented", "surfaces": ["data", "ui"], "notes": "事件存储与日历视图"},
      {"id": "calendar.reminders", "status": "planned", "surfaces": [], "notes": "尚未接入提醒"},
      {"id": "tasks.items", "status": "partial", "surfaces": ["ui"], "notes": "仅有列表界面"}
    ]
  }
}
```

`types` 是非空、有序、不重复的类型 ID 列表；首项为主要展示类型。标准类型包含 calendar、tasks、notes、contacts、documents、messaging、finance、media、dashboard、utility。自定义类型使用 `custom:<namespace>`，自定义功能使用 `custom:<namespace>.<feature>`（小写 kebab-case namespace/feature），且必须声明对应 namespace 的自定义类型。标准功能必须属于已声明类型；未知的标准 ID、重复 ID、不支持的版本及未知字段均拒绝。每份声明最多 20 个类型、100 个功能，ID 最多 200 字符，单项 notes 最多 2000 字符。

功能状态为 `implemented`、`partial`、`planned`。已实现与部分实现至少声明一个实际提供的 surface：`data`（存储/数据）、`tools`（Agent 可调用的处理工具）、`ui`（可视化交互）。仅计划中的功能必须使用空 surfaces，避免将未来功能计为已实现。`notes` 为可选说明。未声明的标准功能显示为 `not_declared`，不意味着整个 App 不合格；类型标准是比较词汇，不要求实现该类型的所有功能。

功能状态与 surfaces 是作者声明，校验确保结构与分类一致，不证明功能运行正确。应用详情必须清楚标注“实现声明”，不显示认证徽章。权限继续使用 `capabilities` grants，数据继续复用 canonical `schema_refs`。声明 `tools` 不会创建工具或扩大权限。

## 公开接口与应用中心

- `GET /api/app-types` 返回 `{spec_version: 1, types: [...]}`。每个类型含 `id`、中英双语 `title` / `description`、标准 `features`（`id`、双语 `title`）。目录由后端提供，前端与 Coding Agent 共用，避免复制不同标准。
- `GET /api/apps`、`GET /api/apps/{app_id}` 与 App Center 条目携带声明的 `app_spec`；未分类 App 可以省略或返回 null。App Center state 增加 `app_type_catalog`，以免分类依赖另一项网络请求。
- `PATCH /api/apps/{app_id}` 可增加/修改 `app_spec`，应用相同 Manifest 校验；null 清除分类。更新仅修改 Manifest 元数据，不能修改代码、grants 或 schema refs。UI 配置只编辑 types；功能声明由代码生成/修改产生，API 允许作者提交完整声明。
- 应用中心保留 generated_app / skill / mcp 的来源过滤，另提供用户用途类型过滤与未分类入口。搜索包含类型与功能名称；组合 App 可从任一类型找到。过滤不改变持久启动器布局。
- 详情显示类型、标准功能的实现声明与 surfaces、未声明功能、自定义功能。旧 App 显示未分类；未分类和未声明都是诚实的缺省值。
- 绑定到 executable capability 的 UI，其 app_spec 也进入对应 App Center 条目，避免绑定后分类丢失。

## 生成与分发

Coding Agent 的 Manifest 模板、生成提示提供当前类型标准。新建或修改 App 应依实际交付选类型和功能，不得仅因取得 grant 就声称实现功能；编辑现有 App 时保留并调整声明。Manifest 校验在既有 staging / verification / promotion 流程中执行。Agent 可通过只读类型目录和应用列表理解声明。

只读 `list_app_specs(app_id?)` tool 返回 App 的身份、说明、版本和完整声明。小型目录可省略参数；大型目录先用 `list_available_apps` 获取 ID，再按 `app_id` 逐个读取，避免多个合法声明合计超过工具输出上限。找不到指定 App 时返回空列表。

`app_spec` 随 Manifest 文件携带，因而可作为未来商店包的分类与功能比较元数据。本次增加标准、验证、生成与应用中心展示；现有 App Center 仍是 workspace 的已安装目录，Skill Market 仍独立。不声称已经实现在线 App 发布、跨用户安装或运行正确性认证。

## 验收

覆盖标准与自定义类型、多类型、功能与类型关联、实现状态/surfaces、边界和错误输入、旧 Manifest 的 round-trip、元数据更新原子性、catalog API 与 Agent 工具、App Center 分类/搜索/详情、绑定 UI 元数据和 Coding Agent 提示。所有新行为先写失败测试再实现，并通过既有回归与 UML 验证。
