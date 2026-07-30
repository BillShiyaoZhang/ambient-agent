# Agent Skills：安装、激活与安全边界

Ambient Agent 支持 [Agent Skills 规范](https://agentskills.io/specification)中的 `SKILL.md` 格式。Skill 是可按需加载的说明与工作流知识，不是可执行代码、模型 Tool、Capability grant 或 App。Catalog 可以聚合系统随附 Market、管理员配置的本地 Market，以及固定到不可变 Git commit 和预期 SHA-256 的 GitHub 来源。让一个来源可被发现，不等于信任其中的包：外部包安装后默认停用并隔离，必须经过绑定内容摘要的 `agent.context.inject` 授权。发现、下载、安装和更新都不会扩大 Agent、Widget 或 Runtime 的权限。

## 1. 领域边界

| 对象 | 作用 | 执行与授权 |
| --- | --- | --- |
| Instruction Skill | 告诉 Agent 在何时、按什么流程完成一类任务 | 只影响相关 turn 的上下文；自身不产生副作用 |
| Model Tool | Agent 可调用的受信本地函数 | 必须已注册到 Tool Gateway，并继续检查 effect、scope、approval、timeout 和幂等 |
| Executable Capability | MCP 或远端 Agent 暴露的结构化 action | 通过 Capability Manifest、输入/结果 schema、Durable Run 和 adapter permission 执行 |
| App UI | 在 Widget Runtime 中运行的交互界面 | 只能使用 Manifest V2 中精确批准的 grants |

这些对象可以组合，但不能互相冒充。Skill 可以说明如何使用一个已经存在的 Tool，也可以引用一个已经安装的 Capability action；它不能创建 Tool、改变 ToolSpec、声明新的 adapter 或把安装操作变成运行权限。没有 UI 的 Skill 仍然只是 Instruction Skill，绝不因此成为 system-level Tool。

Instruction Skill 使用独立的 `agent-skill:<namespace>:<name>` catalog ID 空间；既有 `CapabilityManifest.kind = "skill"` 继续使用 `skill:<provider>:<id>`。这种结构性隔离避免两个独立 Registry 通过并发 check-then-write 获得同一个 ID。

设计结论是：把 Skill 做成可安装、可版本化、按需注入的 instruction layer 是合理的，因为它能复用现有 Agent 与 Durable Run；把同一份文本 manifest 同时当作可执行插件、权限声明或 UI 包则不合理，因为这会绕过已经存在的 Capability、approval 和 Widget 安全边界。因此 MVP 只实现 context-only Skill。

## 2. `SKILL.md` 契约

标准 Skill 至少包含一个 `SKILL.md`：

```text
skill-name/
├── SKILL.md
└── market.json      # Ambient Market 描述符；不属于 Agent Skills 标准
```

`SKILL.md` 由 YAML frontmatter 和 Markdown 正文组成。MVP 遵循标准字段：

- `name` 与父目录名相同，使用小写字母、数字和连字符，长度不超过 64；
- `description` 同时说明 Skill 做什么以及何时使用，长度不超过 1024；
- `license`、`compatibility` 和字符串键值 `metadata` 为可选公开元数据；
- `allowed-tools` 是实验性兼容声明，只能用于描述预期环境。

Agent Skills 标准还允许 `references/`、`assets/` 和 `scripts/` 等可选目录。为保持首个安装器的攻击面和 snapshot 契约足够小，Ambient MVP 的 Market 条目只接受上面两个普通文件；出现任何额外文件或目录都会拒绝安装。也就是说，本版本兼容标准的 metadata、正文和正文按需加载，不读取可选资源，更不会执行 script。

Ambient 对包再施加以下约束：

- `SKILL.md` 只接受上述 Agent Skills 标准字段；Ambient 扩展放在相邻的 `market.json`；
- `market.json` 声明 `market_id`、`catalog_id`、title、provider、version、tags/icon/accent、`ontology_refs`、triggers、`surfaces` 和 provenance；MVP 的 `surfaces` 只接受 `agent_context`；
- 安装记录中的内容 digest、验证结果和时间由 Installer 生成，不能信任包自报；只有随发行版加载的内置 Market 会被 loader 标记为 `bundled/verified`，`SKILL_MARKET_DIR` 中的条目一律标记为 `local/unverified`，即使 `market.json` 自称 verified；
- `agent-skill:ambient-agent:*` 命名空间只保留给 loader 推导出的内置包，外部 Market 不能占用；
- `market.json.ontology_refs` 只能引用当前 `ambient-context` 中已经注册的 canonical entity ID；
- Skill 目录、`SKILL.md` 和 `market.json` 都必须是真实目录/普通文件；符号链接、无效 UTF-8、重复或未知字段以及超出大小上限的内容都会被拒绝；
- `SKILL.md` 不作为 Jinja 或其他模板执行，frontmatter 和正文都按数据处理；
- 安装不会运行 hook、下载依赖或注册 Tool。GitHub Provider 只能读取管理员预先固定且验过 SHA-256 的单个 `SKILL.md`；包正文不能触发第二次网络读取。

`allowed-tools` 不会预批准任何调用。实际 turn 没有提供该 Tool、当前 scope 不满足或 effect 需要 interaction 时，Skill 中的声明不会改变拒绝结果。

## 3. Market、安装状态与内容快照

Skill Market 与 App Center 是两个不同的视图：

- `GET /api/skill-market` 列出可安装包的版本、摘要、来源、信任类别和 digest；
- `POST /api/skills/install` 按 `market_id` 安装当前 Market 版本，也用于显式更新；`PATCH /api/skills/{catalog_id}` 修改可信或已授权安装的启用状态，`PATCH /api/skills/{catalog_id}/authorization` 授予、变更或撤销外部上下文注入，`DELETE /api/skills/{catalog_id}` 卸载；
- `GET /api/app-store` 仍是已安装 App、Skill 和 executable capability 的启动器及布局，不是远程 Market 索引。

默认 Catalog 总是包含系统随附 Market。`SKILL_MARKET_DIR` 继续作为兼容配置加入 Catalog，而不再替换内置来源。管理员还可以用 `SKILL_CATALOG_CONFIG` 指向一个版本化的 JSON 配置，声明一个或多个固定 GitHub Provider。配置只让包可被发现，并不让包获得信任或授权。安装器仍不接受来自 API 的任意 URL、Git 仓库或压缩包。安装流程为：

1. 解析并验证 `SKILL.md`、文件边界和公开 metadata；
2. 根据 `SKILL.md` 与 `market.json` 的精确内容计算稳定 digest；
3. 写入不可变、内容寻址的本地 snapshot；
4. 在 workspace SQLite 中原子写入安装、信任、授权及启用状态；
5. 只有完整事务提交后，Skill 才出现在已安装目录和 Agent metadata 投影中。

授权状态机刻意区分内置与外部包：

- loader 验证的内置 Skill 是 `trusted + implicit`，保留既有启用/停用行为；
- 外部 Skill 首次安装为 `quarantined + none + disabled`；
- 授权 `explicit_only` 后只能通过显式 `/skill <name>` 选中；授权 `implicit` 后还允许确定性的相关性匹配；
- 授权会再次验证不可变 package，并在同一个 SQLite transaction 中记录精确 authorized digest 和启用状态；
- 撤销不需要读取可能已经损坏的 package，直接原子切换为 `quarantined + none + disabled`；
- 外部包只要更新内容，就会撤销旧授权并隔离新字节。版本号不是授权身份。

不可变 principal 是 `catalog_id@skill_digest`。grant 审计摘要是以下精确 canonical JSON 的 SHA-256：

```json
{
  "activation_policy": "explicit_only | implicit",
  "capability": "agent.context.inject",
  "catalog_id": "agent-skill:<namespace>:<name>",
  "skill_digest": "sha256:<package-digest>"
}
```

该摘要是审计/CAS 身份，不是签名，也不是 Widget grant。Skill 授权 mutation 必须携带当前 Skill registry revision；其他 mutation API 为兼容性仍允许省略，而 App Center 总会提交。并发授权、更新、启用、撤销或卸载因此产生 conflict，而不是静默覆盖。

这个 MVP consent channel 假设单用户 workspace：trusted Host 展示审查确认，再提交绑定 digest/revision 的决定。它不会用密码学证明具体的人类 actor，也不是 pending Durable Run interaction。若 Market 进入多用户或远程管理场景，必须再加入已认证 actor、append-only decision audit 和一次性 interaction challenge，才能把它视为委托 consent。

安装状态与内容职责分离：

- `workspace/.ambient/skills.db` 是 `catalog_id`、版本、digest、source、enabled、授权状态/策略/digest 和安装时间的控制面事实源；Market API 将安装记录与当前 Market 比较，派生 `not_installed | installed | update_available | market_older | integrity_conflict`，不把未安装条目写入数据库；
- `workspace/.ambient/skills/packages/<sha256>/` 只保存本次安装实际验证过的 `SKILL.md` 与 `market.json`；
- 相同 ID、版本和 digest 的安装是幂等操作；只有 SemVer precedence 更高时显示 `update_available` 并允许显式更新；较低版本显示 `market_older` 且普通 install API 拒绝降级；相同 precedence（包括只改变 build metadata）但 digest 不同显示 `integrity_conflict` 并拒绝覆盖；
- 更新先生成新 snapshot，再原子切换 installation record；失败时旧版本保持可用；
- 卸载只移除 installation record；内容寻址 package 作为不可变缓存保留，避免请求路径清理与并发重装发生竞态。未来 GC 必须使用持锁或租约、宽限期和 mark-and-sweep。已开始的 Run 使用自身固定的正文副本，不依赖安装目录继续存在。

Market 暂时不可用时，已安装 snapshot 仍可工作。损坏、缺失或 digest 不匹配的 snapshot 会使该 Skill 变为不可用，而不是回退到同名最新内容。

### 3.1 多源 Catalog 与供应链边界

`SkillCatalog` 是发现层聚合器，不是新的执行 Runtime。每个 Provider 只实现“列出经过标准化的候选快照”，并返回独立健康状态：

| Provider | 默认状态 | 可安装内容 | 信任语义 |
| --- | --- | --- | --- |
| `bundled` | 必需、随发行版加载 | `SKILL.md + market.json` | 只有该 loader 可以产生 `bundled/verified` |
| `local` | 通过 `SKILL_MARKET_DIR` 可选 | 两个普通文件 | 始终是 `local/unverified` |
| `github` | 通过 `SKILL_CATALOG_CONFIG` 可选 | 固定 commit 下、预期 SHA-256 的单个 `SKILL.md` | 始终是 `local/unverified`；commit/hash 证明可复现，不证明作者可信 |
| 未来 registry/search | 默认关闭 | 只返回 discovery metadata，或先转换成上述不可变快照 | 排名、下载量、平台审核和上游扫描都只是 advisory signal |

Provider 的最小契约是稳定 `source_id`、`kind`、`required`、`list_entries()` 和有界错误；Catalog 负责确定性排序、跨来源 `market_id/catalog_id` 冲突检查以及故障隔离。必需来源失败会使 Catalog 请求失败；可选来源失败只在 `GET /api/skill-market.sources` 中标为 `unavailable`，其他来源仍可发现，已安装快照始终不受影响。相同 ID 出现在两个健康来源时必须整体失败关闭，不能按 Provider 顺序偷偷覆盖。

GitHub 配置版本为 `1`，Provider/entry 均为管理员控制的数据。entry 必须声明：

```json
{
  "repository": "owner/repository",
  "commit": "40-character-lowercase-git-sha",
  "path": "skills/example",
  "sha256": "sha256:<SKILL.md-sha256>",
  "files": ["SKILL.md"],
  "namespace": "github-owner-repository",
  "title": "Example",
  "provider": "Publisher",
  "tags": ["example"],
  "triggers": ["example workflow"],
  "ontology_refs": []
}
```

Host 只构造 `raw.githubusercontent.com/<repo>/<commit>/<path>/SKILL.md`，限制响应大小、超时和 redirect，校验精确 SHA-256 后写入 workspace 的内容寻址 Catalog cache。`files` 必须精确为 `["SKILL.md"]`，因此带有 `scripts/`、`references/`、`assets/`、hook、依赖或可执行入口的上游 Skill 在当前 profile 下标为不兼容且不能安装。缓存命中仍会重新校验 hash；网络失败时只允许读取同一预期 hash 的已验证缓存，绝不回退到 branch、tag、HEAD 或另一个 commit。

Catalog 来源身份和安装信任必须分开显示：

- `catalog_source` 记录 Provider ID/kind、`source_uri`、`source_revision`、`upstream_hash` 和 `update_strategy`；
- `provenance.digest` 是 Ambient 对合成 `market.json` 与精确 `SKILL.md` 计算的 package digest；
- Git commit 与上游文件 hash 只是供应链 pin，不会把 `provenance.verified` 变成 true，也不会跳过隔离授权；
- 本地 Market 继续用 SemVer 比较；GitHub 快照的 installation `version` 直接等于 commit，不伪造 SemVer，并使用 `content_hash` 更新策略。相同 commit 下字节变化是完整性冲突，不同 commit/hash 是显式 `update_available`，更新后旧授权必然撤销；
- API 响应保持 `version = 1`，新增 `sources`、`catalog_source` 和 `package_compatibility` 都是 additive 字段。

接入成熟市场时先实现新的 Provider adapter，不把其权限模型、安装命令或执行器嵌入 Ambient。`skills.sh` 适合作为未来的搜索与审计信号来源，但结果仍须解析为固定 commit/hash 的 standalone snapshot；无法得到不可变 revision 或完整文件清单时只能展示，不能安装。需要 scripts、MCP、网络、文件或 UI 的条目应转成 Capability/Plugin/Widget，并分别走其 sandbox 和 grant 通道，不能扩展 `agent.context.inject`。

仓库随附一个**默认关闭**的 Anthropic 官方来源配置：
[`backend/catalogs/anthropic.json`](../../backend/catalogs/anthropic.json)。Host
开发可设置 `SKILL_CATALOG_CONFIG=backend/catalogs/anthropic.json`，Docker 可设置
`SKILL_CATALOG_CONFIG=/app/backend/catalogs/anthropic.json`。该配置固定到
`anthropics/skills` 的精确 commit，并且只收录该 revision 下目录中唯一文件为
`SKILL.md` 的 `doc-coauthoring`；同仓库其他顶层 Skill 带有 scripts、references
或 assets，因此不进入当前 profile。该 curated 列表是可审计兼容清单，不是对
Anthropic 内容的 Ambient 授权，首次安装仍会隔离。

## 4. Progressive disclosure 与 Run 快照

Ambient 遵循 Agent Skills 的 progressive disclosure，并区分不同信任通道：

1. **内置全局 metadata**：只把 loader 验证的内置 Skill 的有界 metadata 投影给 Router。外部 metadata 本身也是不可信自然语言，即使正文已经获用户授权，也绝不进入 `SystemCapabilityCatalog`。
2. **确定性选择**：`SkillManager` 不使用 LLM 来选择已启用 Skill。`explicit_only` 外部 Skill 必须通过 `/skill <name>`；`implicit` Skill 可以匹配有界 triggers、身份 metadata 或 description terms。
3. **相关 turn 的正文**：只有选中后才加载完整且有界的 `SKILL.md`。选择过程不解释 `allowed-tools`，也不能创建 Capability。

Skill 选中后，Durable Run 固化：

```text
catalog_id + version + digest + bounded instruction content
+ trust class + activation policy + authorized digest + principal/grant digest
```

若该 Run 进入 `converse`，后续模型调用只使用这份快照；MVP 不把 Skill 正文注入 Widget plan、Graph mutation 或其他 effect workflow。Skill 在 Run 中途更新、禁用或卸载，不会让恢复后的 converse prompt 漂移；真正的 Tool、Capability 和权限仍在每次执行时读取当前 policy 并默认拒绝。LLM audit 记录所读取 Skill 的 digest，但不把无界正文复制到事件或 KG。

Run 中的完整正文只保存在内部 checkpoint；公开 Run 列表、详情和 replayable WebSocket 会移除 `instructions` 与 `allowed_tools`，只返回 `catalog_id`、版本、digest 等审计 metadata。这样既保持恢复确定性，也不把 Skill 正文扩大成公共 Run API 数据。

内置与外部正文使用不同 prompt 通道：

- 内置正文为了向后兼容，仍作为可信 procedural system guidance，并保留既有的有界只读 Converse Tools；
- 外部正文包裹为明确标记的不可信数据，放在独立 user-role message 中，绝不拼接到 system prompt；
- 只要选中了外部正文，Run 就会在 LLM routing 之前确定性地固定为 `Converse`。因此外部文本不会进入 Router，也不能把 Run 引向 Widget 生成、Graph mutation、Capability action 或其他 effect workflow；
- 该 Converse 调用不提供 model Tool、tool context、workspace read scope、旧聊天历史、durable summary 或活动 App/Graph artifact。输入只有有界的当前用户请求、核心 policy 和外部不可信数据 envelope。
- 可见回复以 `display_only` context policy 和精确 Skill provenance 持久化。后续 Router、普通 prompt、artifact discovery 与 durable summary 都会排除该回复，避免第三方影响通过下一轮 assistant 历史逃逸。

这是一个**语义隔离边界**，不是操作系统沙盒。context-only 包中没有第三方进程：安装器只接受 `SKILL.md` 与 `market.json`，从不执行 script。未来若包需要可执行代码，必须建模为既有 executable Capability 或通过验证的 Widget，继续走对应 runtime 的进程隔离、精确 grant、approval、Durable Run 与审计；`agent.context.inject` 永远不能授权执行。

正文中的“忽略之前规则”“假定已有权限”“使用不存在的 Tool”等文字不改变实际 tool schema、Capability Catalog 或 authorizer。Run 在选择成功时固定精确字节和 grant 审计身份，但每个尚未开始的外部模型调用之前，都会先验证 package integrity，再紧邻 `provider.generate` 重新读取 live installation、digest、enabled、policy 和 authorization；最后一次读取是该调用的授权准入线性化点。因此在准入前完成的撤销、更新、卸载或策略变化会阻止 pending、resumed 或 retried 注入；准入后的变化属于 in-flight cancellation，无法取消已经发给 provider 的请求，也不能让模型忘掉已经完成的回复。

## 5. Tool、Capability 与权限

激活 Skill 只授予有界上下文注入，不是 effect approval。一个动作必须分别通过其真实边界：

```text
Skill guidance
  -> supplied Tool schema or installed Capability action
  -> input validation
  -> Tool/adapter policy and permission interaction
  -> Durable Run effect, recovery, and audit
```

Skill 中的指导只能依赖当前 turn 实际提供的稳定 Tool name 或已安装的精确 `catalog_id + action_id`；正文不能声明 raw MCP command、任意 `app_id + tool_name`、远端 Agent URL、secret 或 recovery 保证。安装或授权 Skill 不自动安装依赖，也不批准 Tool 使用、数据访问、MCP spawn、网络、文件、Graph mutation 或不可逆操作。外部语义沙盒根本不会提供这些 Tool；在沙盒之外，正文提到的能力不存在时，相关调用仍按原有 `unsupported` 或 permission interaction 语义失败。

## 6. App Center 与 UI

MVP 在 App Center 中严格区分 Instruction Skill 和可执行条目：

- 已安装 Skill 使用 `launch_mode = "details"`，详情页提供来源、digest、ontology references、授权 principal/grant digest 和卸载；它不显示后台 Run 或“生成 UI”按钮；
- 外部 Skill 显示明确的隔离警告，而不是普通 Enable 开关。用户可以授权仅显式激活、授权隐式匹配、切换策略或撤销；每次确认都明确 `agent.context.inject` 不授予 Tool、数据、网络或文件访问；
- 可信内置 Skill 保留既有启用/停用控件；
- Skill 通过相关 Agent turn 生效，是否有独立 UI 不影响它作为上下文说明的可用性；
- 需要结构化 action 或 UI 的工作流复用已有 executable capability 或 generated App；Capability 的无 UI action、Durable UI-generation Run、最小 `capability.invoke` grant、staging、verification 和原子 promotion 都保持原语义；
- 本版本不创建 Skill-to-UI binding，也不会让安装 Skill 自动生成或发布 App。

Market 包不能携带一个绕过 Widget 验证直接加载的 live Controller。详细 App 规则见 [Widget 与应用中心](/architecture/apps.md)。

## 7. Ontology 与知识图谱

`market.json.ontology_refs` 只是 Skill 对现有 canonical schema 的引用提示：

- 安装、启用、更新或卸载 Skill 不增长 `ambient-context` ontology；
- 引用未知 entity 不会自动创建 schema，而会使引用无效或 Skill 不可用；
- `ontology_refs` 不构成 `graph.query` 或 `graph.mutate` 权限；
- Skill manifest、正文、digest、来源、安装状态、授权决定和配置都不写入 KG。

授权是 `.ambient/skills.db` 中的控制面安全状态，不是领域知识，也不会新增 Capability Ontology 节点。`ontology_refs` 仍然只是对已有 canonical entity 的验证后引用。

Skill 执行过程中真正产生的用户上下文事实，例如 `Task`、`Event` 或 `Note`，仍必须复用 canonical entity，并通过现有 schema alignment、Graph preflight、用户 interaction 和原子 mutation 后才能进入 KG。Skill 自身的存在是系统安装状态，不是用户上下文事实。

## 8. 威胁模型与失败语义

| 风险或失败 | 必须保持的语义 |
| --- | --- |
| 恶意正文进行 prompt injection | 只按需加载；核心 policy 优先；执行边界仍默认拒绝 |
| 外部 metadata 在正文选择前攻击 Router | 外部 Skill 绝不投影到 `SystemCapabilityCatalog`，只由 `SkillManager` 确定性选择 |
| 外部正文试图路由到 effect workflow | 在 Router 之前固定为有界 Converse；不提供 Tool、历史、summary、workspace scope 或 artifact |
| Skill 影响的回复在下一轮变成可信历史 | 回复保留展示和审计，但标记 `display_only`，Router、prompt、artifact 与 summary 都不复用 |
| 额外文件、symlink、超大内容或损坏 UTF-8 | 安装失败且不产生可见部分状态 |
| 安装即授权或伪造 `allowed-tools` | 外部安装默认停用并隔离；授权也只授予上下文注入 |
| 旧授权与 package 更新发生竞态 | 要求精确 package digest 与 registry revision；不匹配即 conflict，更新必然撤销旧授权 |
| failed Run 在撤销或更新后被 retry | 模型使用前重新验证 live 精确 grant 与 package，不能只相信 snapshot 自带的 hash |
| 外部包冒充内置身份 | loader 推导 trust、保留命名空间、精确 source/catalog 校验共同失败关闭 |
| 用户撤销已经损坏的外部包 | 撤销不加载 package 字节，并原子停用和隔离记录 |
| Market 中出现更高版本 | 显示 `update_available`；只有显式更新才切换已验证 snapshot |
| 同版本内容的 digest 改变 | 列表可提示内容不一致，但 install 作为完整性冲突拒绝，不能覆盖已安装 snapshot |
| Market 版本低于已安装版本 | 显示 `market_older` 并拒绝普通更新；本版本没有隐式或静默降级路径 |
| 上下文 token DoS | 全局只投影 metadata；正文、Skill 数量和总字符数都有上限 |
| 更新或卸载与活跃 Run 竞态 | Run 使用已固定的有界正文，不重新读取安装目录 |
| snapshot 缺失或 digest 不匹配 | Skill 变为 unavailable；隐式选择跳过，显式选择报完整性错误，且不读取同名其他版本 |
| checkpoint 的 selection marker 与 snapshot 矛盾 | 恢复以 `invalid_skill_snapshot` 失败，绝不静默改成空选择或重新解析当前安装状态 |
| 正文引用不存在的 Tool 或未授权 adapter | 调用按现有 interaction/失败语义处理；不伪装成功 |
| Skill 被误当成可执行或 UI 条目 | App Center 只打开详情；执行和 UI 生成仍属于现有 Capability/App |
| 可选 Catalog 来源不可达 | 已安装 Skill 正常工作；该来源标为 `unavailable`，其他来源继续返回 |
| 相同 ID 出现在多个来源 | Catalog 整体失败关闭，直到管理员消除歧义 |
| GitHub commit/hash 不匹配或 redirect | 拒绝候选；只可使用同一预期 hash 的已验证缓存 |
| 上游包包含 scripts/references/assets | 标为不兼容且禁止安装；需要执行的能力走 Capability/Plugin/Widget |

安装、更新、启停与卸载在 SQLite transaction 中串行化并原子提交；幂等安装和稳定 catalog ID 避免产生重复记录。Registry 或 snapshot 损坏时系统报告错误，不能把校验失败当成空目录或同名最新内容继续执行。

## 9. 兼容性

- Manifest V2、Capability Ontology、Tool Gateway、Capability action 和 Durable Run 协议保持不变；
- 既有 `CapabilityManifest.kind = "skill"` 条目继续按 executable capability 解释并保留 `skill:` ID；Instruction Skill 使用 `agent-skill:` ID，不能被静默互转；
- 旧 workspace 没有 Skill installation 表或 snapshot 时等价于“未安装 Skill”；迁移是 additive，不改动 Apps、Capabilities、layout 或 KG。既有内置记录获得 loader 推导的 `trusted + implicit`；所有 legacy external/local 记录都停用并隔离，避免历史 `enabled` 位被误当成授权；
- 新 Run 在创建时显式写入 `skill_selection_state = pending`，route 后变为 `pinned | pinned_none`；只有 marker 与 snapshot 都缺失的旧 checkpoint 才按 `pinned_none` 恢复，因此不会补注入后来安装的 Skill；未知 marker、缺失的 pinned snapshot 或其他矛盾组合均失败关闭；
- App Center 的既有条目和布局字段保持可读；Instruction Skill 作为 `details` 启动模式的向后兼容扩展，不改变 Capability 的 action/UI 行为；
- Capability Ontology 与 Widget grant schema 保持不变。`agent.context.inject` 是 Skill 控制面决策，刻意不创建第二套 executable-grant 词汇；
- Skill manifest/version、安装 registry revision 和内容 digest 使用独立版本域，不能复用或误增 App Manifest、Capability Ontology 或 Runtime Contract 版本。

## 10. MVP 非目标

以下内容不属于本版本：

- 从请求携带的任意网络 URL、可变 Git branch/tag 或社区压缩包安装；
- 自动枚举 GitHub 仓库，或直接执行第三方 Market 的安装命令；
- 把 `skills.sh`、社区 registry、流行度、publisher badge 或上游恶意软件扫描当作 Ambient trust grant；
- 打包或读取标准中的可选 `references/`、`assets/`、`scripts/` 目录；
- 运行 Skill 自带的 script、安装 hook、shell 或动态 Python/JavaScript Tool；
- 自动安装、认证或批准 Skill 依赖；
- 后台主动运行 Skill、定时任务或无用户 Run 的副作用；
- 为 Instruction Skill 直接生成 UI，或建立 Skill-to-UI lifecycle binding；
- Market 支付、评分、评论、发布者自助上传和自动更新；
- 将 Skill 安装目录建模为第二套 ontology 或写入 KG；
- 用多个 Skill 的无界正文做全局 prompt，或在没有稳定快照时自动组合 Skill。
