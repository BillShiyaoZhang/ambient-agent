# Agent Skills：安装、激活与安全边界

Ambient Agent 支持 [Agent Skills 规范](https://agentskills.io/specification)中的 `SKILL.md` 格式。Skill 是可按需加载的说明与工作流知识，不是可执行代码、模型 Tool、Capability grant 或 App。MVP 只从系统随附或管理员配置的本地可信 Market 安装 Skill；发现、安装和更新不会扩大 Agent、Widget 或 Runtime 的权限。

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
- `market.json.ontology_refs` 只能引用当前 `ambient-context` 中已经注册的 canonical entity ID；
- Skill 目录、`SKILL.md` 和 `market.json` 都必须是真实目录/普通文件；符号链接、无效 UTF-8、重复或未知字段以及超出大小上限的内容都会被拒绝；
- `SKILL.md` 不作为 Jinja 或其他模板执行，frontmatter 和正文都按数据处理；
- 安装不会运行 hook、下载依赖、读取远程内容或注册 Tool。

`allowed-tools` 不会预批准任何调用。实际 turn 没有提供该 Tool、当前 scope 不满足或 effect 需要 interaction 时，Skill 中的声明不会改变拒绝结果。

## 3. Market、安装状态与内容快照

Skill Market 与 App Center 是两个不同的视图：

- `GET /api/skill-market` 列出可信源中可安装的包、版本、摘要、来源和 digest；
- `POST /api/skills/install` 按 `market_id` 安装当前 Market 版本，也用于显式更新；`PATCH /api/skills/{catalog_id}` 只修改 `enabled`，`DELETE /api/skills/{catalog_id}` 卸载；
- `GET /api/app-store` 仍是已安装 App、Skill 和 executable capability 的启动器及布局，不是远程 Market 索引。

MVP 默认使用系统随附 Market；管理员也可以用 `SKILL_MARKET_DIR` 指向一个本地可信 Market 根目录。它不接受任意 URL、Git 仓库或未验证压缩包。安装流程为：

1. 解析并验证 `SKILL.md`、文件边界和公开 metadata；
2. 根据 `SKILL.md` 与 `market.json` 的精确内容计算稳定 digest；
3. 写入不可变、内容寻址的本地 snapshot；
4. 在 workspace SQLite 中原子写入安装记录及启用状态；
5. 只有完整事务提交后，Skill 才出现在已安装目录和 Agent metadata 投影中。

安装状态与内容职责分离：

- `workspace/.ambient/skills.db` 是 `catalog_id`、版本、digest、source、enabled 和安装时间的事实源；Market API 将安装记录与当前 Market 比较，派生 `not_installed | installed | update_available | market_older | integrity_conflict`，不把未安装条目写入数据库；
- `workspace/.ambient/skills/packages/<sha256>/` 只保存本次安装实际验证过的 `SKILL.md` 与 `market.json`；
- 相同 ID、版本和 digest 的安装是幂等操作；只有 SemVer precedence 更高时显示 `update_available` 并允许显式更新；较低版本显示 `market_older` 且普通 install API 拒绝降级；相同 precedence（包括只改变 build metadata）但 digest 不同显示 `integrity_conflict` 并拒绝覆盖；
- 更新先生成新 snapshot，再原子切换 installation record；失败时旧版本保持可用；
- 卸载只移除 installation record；内容寻址 package 作为不可变缓存保留，避免请求路径清理与并发重装发生竞态。未来 GC 必须使用持锁或租约、宽限期和 mark-and-sweep。已开始的 Run 使用自身固定的正文副本，不依赖安装目录继续存在。

Market 暂时不可用时，已安装 snapshot 仍可工作。损坏、缺失或 digest 不匹配的 snapshot 会使该 Skill 变为不可用，而不是回退到同名最新内容。

## 4. Progressive disclosure 与 Run 快照

Ambient 遵循 Agent Skills 的 progressive disclosure：

1. **全局 metadata**：只把已安装 Skill 的稳定 ID、name、description、版本、enabled 和 availability 投影给 Router；只有 enabled 且 available 的条目可被选择，不把 digest 或所有正文塞入每个 prompt。
2. **相关 turn 的正文**：只有显式选择或被当前请求判定为相关的 Skill，才即时加载完整且有界的 `SKILL.md`。

Skill 选中后，Durable Run 固化：

```text
catalog_id + version + digest + bounded instruction content
```

若该 Run 进入 `converse`，后续模型调用只使用这份快照；MVP 不把 Skill 正文注入 Widget plan、Graph mutation 或其他 effect workflow。Skill 在 Run 中途更新、禁用或卸载，不会让恢复后的 converse prompt 漂移；真正的 Tool、Capability 和权限仍在每次执行时读取当前 policy 并默认拒绝。LLM audit 记录所读取 Skill 的 digest，但不把无界正文复制到事件或 KG。

Run 中的完整正文只保存在内部 checkpoint；公开 Run 列表、详情和 replayable WebSocket 会移除 `instructions` 与 `allowed_tools`，只返回 `catalog_id`、版本、digest 等审计 metadata。这样既保持恢复确定性，也不把 Skill 正文扩大成公共 Run API 数据。

Skill 正文作为低于 Ambient 核心 system policy 的受限说明注入。正文中的“忽略之前规则”“假定已有权限”“使用不存在的 Tool”等文字不改变实际 tool schema、Capability Catalog 或 authorizer。

## 5. Tool、Capability 与权限

激活 Skill 本身是只读上下文操作，不是 effect approval。一个动作必须分别通过其真实边界：

```text
Skill guidance
  -> supplied Tool schema or installed Capability action
  -> input validation
  -> Tool/adapter policy and permission interaction
  -> Durable Run effect, recovery, and audit
```

Skill 中的指导只能依赖当前 turn 实际提供的稳定 Tool name 或已安装的精确 `catalog_id + action_id`；正文不能声明 raw MCP command、任意 `app_id + tool_name`、远端 Agent URL、secret 或 recovery 保证。安装 Skill 不自动安装依赖，也不批准 MCP spawn、网络、文件、Graph mutation 或不可逆操作。正文提到的能力不存在时，相关调用按原有 `unsupported` 或 permission interaction 语义失败。

## 6. App Center 与 UI

MVP 在 App Center 中严格区分 Instruction Skill 和可执行条目：

- 已安装 Skill 使用 `launch_mode = "details"`，详情页提供来源、digest、ontology references、启用/停用和卸载；它不显示后台 Run 或“生成 UI”按钮；
- Skill 通过相关 Agent turn 生效，是否有独立 UI 不影响它作为上下文说明的可用性；
- 需要结构化 action 或 UI 的工作流复用已有 executable capability 或 generated App；Capability 的无 UI action、Durable UI-generation Run、最小 `capability.invoke` grant、staging、verification 和原子 promotion 都保持原语义；
- 本版本不创建 Skill-to-UI binding，也不会让安装 Skill 自动生成或发布 App。

Market 包不能携带一个绕过 Widget 验证直接加载的 live Controller。详细 App 规则见 [Widget 与应用中心](/architecture/apps.md)。

## 7. Ontology 与知识图谱

`market.json.ontology_refs` 只是 Skill 对现有 canonical schema 的引用提示：

- 安装、启用、更新或卸载 Skill 不增长 `ambient-context` ontology；
- 引用未知 entity 不会自动创建 schema，而会使引用无效或 Skill 不可用；
- `ontology_refs` 不构成 `graph.query` 或 `graph.mutate` 权限；
- Skill manifest、正文、digest、来源、安装状态、权限和配置都不写入 KG。

Skill 执行过程中真正产生的用户上下文事实，例如 `Task`、`Event` 或 `Note`，仍必须复用 canonical entity，并通过现有 schema alignment、Graph preflight、用户 interaction 和原子 mutation 后才能进入 KG。Skill 自身的存在是系统安装状态，不是用户上下文事实。

## 8. 威胁模型与失败语义

| 风险或失败 | 必须保持的语义 |
| --- | --- |
| 恶意正文进行 prompt injection | 只按需加载；核心 policy 优先；执行边界仍默认拒绝 |
| 额外文件、symlink、超大内容或损坏 UTF-8 | 安装失败且不产生可见部分状态 |
| 安装即授权或伪造 `allowed-tools` | 安装成功也不授予 Tool、Capability 或 adapter permission |
| Market 中出现更高版本 | 显示 `update_available`；只有显式更新才切换已验证 snapshot |
| 同版本内容的 digest 改变 | 列表可提示内容不一致，但 install 作为完整性冲突拒绝，不能覆盖已安装 snapshot |
| Market 版本低于已安装版本 | 显示 `market_older` 并拒绝普通更新；本版本没有隐式或静默降级路径 |
| 上下文 token DoS | 全局只投影 metadata；正文、Skill 数量和总字符数都有上限 |
| 更新或卸载与活跃 Run 竞态 | Run 使用已固定的有界正文，不重新读取安装目录 |
| snapshot 缺失或 digest 不匹配 | Skill 变为 unavailable；隐式选择跳过，显式选择报完整性错误，且不读取同名其他版本 |
| checkpoint 的 selection marker 与 snapshot 矛盾 | 恢复以 `invalid_skill_snapshot` 失败，绝不静默改成空选择或重新解析当前安装状态 |
| 正文引用不存在的 Tool 或未授权 adapter | 调用按现有 interaction/失败语义处理；不伪装成功 |
| Skill 被误当成可执行或 UI 条目 | App Center 只打开详情；执行和 UI 生成仍属于现有 Capability/App |
| Market 不可达 | 已安装 Skill 正常工作；Market 列表显式返回错误 |

安装、更新、启停与卸载在 SQLite transaction 中串行化并原子提交；幂等安装和稳定 catalog ID 避免产生重复记录。Registry 或 snapshot 损坏时系统报告错误，不能把校验失败当成空目录或同名最新内容继续执行。

## 9. 兼容性

- Manifest V2、Capability Ontology、Tool Gateway、Capability action 和 Durable Run 协议保持不变；
- 既有 `CapabilityManifest.kind = "skill"` 条目继续按 executable capability 解释并保留 `skill:` ID；Instruction Skill 使用 `agent-skill:` ID，不能被静默互转；
- 旧 workspace 没有 Skill installation 表或 snapshot 时等价于“未安装 Skill”，迁移必须幂等且不能改动 Apps、Capabilities、layout 或 KG；
- 新 Run 在创建时显式写入 `skill_selection_state = pending`，route 后变为 `pinned | pinned_none`；只有 marker 与 snapshot 都缺失的旧 checkpoint 才按 `pinned_none` 恢复，因此不会补注入后来安装的 Skill；未知 marker、缺失的 pinned snapshot 或其他矛盾组合均失败关闭；
- App Center 的既有条目和布局字段保持可读；Instruction Skill 作为 `details` 启动模式的向后兼容扩展，不改变 Capability 的 action/UI 行为；
- Skill manifest/version、安装 registry revision 和内容 digest 使用独立版本域，不能复用或误增 App Manifest、Capability Ontology 或 Runtime Contract 版本。

## 10. MVP 非目标

以下内容不属于本版本：

- 从任意网络 URL、Git 仓库或社区压缩包安装；
- 打包或读取标准中的可选 `references/`、`assets/`、`scripts/` 目录；
- 运行 Skill 自带的 script、安装 hook、shell 或动态 Python/JavaScript Tool；
- 自动安装、认证或批准 Skill 依赖；
- 后台主动运行 Skill、定时任务或无用户 Run 的副作用；
- 为 Instruction Skill 直接生成 UI，或建立 Skill-to-UI lifecycle binding；
- Market 支付、评分、评论、发布者自助上传和自动更新；
- 将 Skill 安装目录建模为第二套 ontology 或写入 KG；
- 用多个 Skill 的无界正文做全局 prompt，或在没有稳定快照时自动组合 Skill。
