# LLM Provider 与模型切换

Ambient Agent 使用工作区级 Provider Registry 管理大模型连接。Provider 配置不再读取旧的
`LLM_PROVIDER`、`LLM_MODEL`、`LLM_API_KEY` 或 `LLM_API_URL` 环境变量。

## 配置与秘密

- 非秘密配置写入 `workspace/llm/config.json`。
- UI 输入的凭据写入 `workspace/llm/secrets.json`；POSIX 文件权限为 `0600`，Windows 使用
  禁止继承、仅允许文件所有者的受保护 DACL。临时文件在写入秘密前设置权限，替换后再次验证设置；
  已有秘密文件启动时重新收紧权限，失败时终止保存而不静默放宽。REST API 只返回
  `configured` 与掩码，不返回秘密值。
- 凭据也可以引用环境变量。配置只保存变量名，运行时由服务端解析。
- 同一预设可以创建多个 Provider Profile，例如个人 OpenAI 与公司 Azure。

Provider Profile 包含 `id`、显示名、预设、连接参数、凭据引用与模型列表。模型使用
`provider_id/model_id` 作为稳定身份，并记录 API 模式、工具调用、图像、推理、上下文窗口及
验证状态。模型发现优先请求实时 provider API，其次使用 LiteLLM 元数据；用户始终可以手工
添加模型 ID。

API Provider 的重新发现按模型 ID 合并，不覆盖已有 API 模式、手工显示名、来源或已验证能力。
同一次发现中的重复 ID 只保留一项；发现没有返回的已有模型仍保留，以免破坏默认和会话引用。
网络请求结束后以最新配置为合并基准，保留请求期间用户保存的设置。
若等待期间 Provider 的连接、预设、启用状态或凭据发生变化，则丢弃旧响应与元数据，
只返回当前模型列表，避免将旧账号或 endpoint 的模型混入新连接。

### MiniMax 区域

MiniMax 的国际站和中国站是两个独立 Provider 预设，API Key 与请求域名不能混用：

- `MiniMax Global` 使用 `https://api.minimax.io/v1`。
- `MiniMax 中国（minimaxi.com）` 使用 `https://api.minimaxi.com/v1`。

两个预设均通过 OpenAI-compatible 文本接口调用，支持各自区域的 Token Plan Key。区域拆分前
创建的旧 `minimax` Profile 原本代表中国站；若它没有明确配置 `api.minimax.io`，配置升级时会
自动迁移为 `minimaxi`。新建国际站配置使用 `minimax`。模型发现只保留适用于 Agent 对话的
`MiniMax-M*` 模型，不将语音、音乐或视频模型混入聊天模型选择器。

## 模型选择规则

### 统一连接与用途分层

配置分为连接/账号（API 凭据或托管 Codex 登录）、模型目录与传输（API 或原生推理）、
用途绑定（主模型、快速模型、编码模型），以及编码执行器（ACP）四层。同一登录可供多个
用途使用，目录和执行能力仍由对应适配器验证。借鉴 CC Switch 的集中连接管理，不复制
OAuth token、不改写桌面 CLI 配置。

`POST /api/llm/connections/codex/sync` 将已安装且已登录的托管 Codex 投影到 Provider Registry：
复用第一个已启用的 `codex_native` Profile；没有原生 Profile 时创建 `ambient-codex`，ID 冲突
则使用递增后缀。若只有已禁用的原生 Profile，返回 `llm_provider_unavailable`，由用户显式
启用，不绕过禁用。响应为脱敏 Profile，连接与凭据为空。

同步与原生 Provider 发现统一使用编码 CLI 的可见目录作为模型 ID、名称和顺序的来源；
主/快速模型与编码选择器展示同一批当前模型。每个模型可保存可选 `availability`：
`native_inference` 与 `coding` 表示对应用途的兼容性预检，`reason` 仅允许
`native_catalog_missing`、`native_profile_unsupported`、`coding_catalog_missing` 或空值。
主/快速兼容性仍由固定主模型 CLI 的实际目录及公开模型元数据校验确定，不能因为编码
目录出现新模型就扩大原生推理支持。用途预检不替代 entitlement 或工具能力验证；普通
API Provider 和缺少该字段的旧配置维持既有行为。明确不兼容的原生模型在主/快速选择器
可见但禁选并显示原因，后端解析也拒绝调用。历史绑定保留显示，不自动替换。

按当前编码目录顺序合并，刷新发现模型的名称，保留手工名称和已验证能力。目录中消失
的已有模型保留在末尾以保护引用，标记两用途不可用及 `coding_catalog_missing`；每次
发现更新用途状态，不能沿用旧兼容性。安装/登录缺失、
不支持的平台/profile、空或畸形目录、发现期间退出登录/禁用/删除连接时，失败或丢弃过时
响应；失败不创建空连接、不更改用途绑定。重复/并发同步复用同一连接。接口受
`workspace.manage` 门禁；同步不执行推理，不证明 entitlement 或工具能力，仍需指定模型测试。
同一配置 Store 以非持久的连接生命周期代次识别删除后重建、禁用后再启用等过时请求；名称
和模型编辑不使代次失效，合并仍读取最新编辑。认证操作 ID 另行保护退出后重新登录。
此代次属于单进程服务的并发控制，不改变磁盘配置格式，也不宣称跨进程写入协调。
API 同步、原生发现及连接测试的目录发现共用服务的托管 Runtime，认证代次必须来自
实际处理登录/退出的同一实例，不能用新实例的空代次冒充认证保护。
未指定模型且没有已绑定模型的连接测试优先选择兼容主/快速推理的条目；若统一目录全为
编码专用模型，返回 `ok=false` 与 `llm_capability_unsupported`，说明当前没有兼容主/快速
的模型，而不是提示用户添加目录中已经存在的模型。显式模型或现有绑定不自动替换。
退出登录执行期间立即暂停新同步：运行时报告未认证状态，界面同步按钮禁用；退出成功、
失败或取消均清理进行中标记，再由实际登录探测确定状态，不能用尚未退出的旧登录注册连接。

设置页提供“同步主/快速模型”，登录成功时自动执行一次；已登录用户可直接使用此操作，
无需手工新建 Provider。同步成功也刷新编码目录；手动刷新编码目录同时发现已有启用的
原生 Profile，随后更新主/快速选择器。打开设置本身不自动重建已删除连接。
关闭设置、退出或重新登录后，旧请求不得覆盖新界面；失败保留原选择并显示重试入口，不
循环请求。三个用途的模型选择仍分别保存，同步不自动设置默认/快速模型。

验收：只有 Codex 登录而没有 Provider 时可同步出主/快速可选模型；重复/并发无重复连接；
已有 API 连接、禁用配置、用途绑定与会话引用保留；认证缺失、目录失败/畸形、平台限制和
过时请求不产生错误可用状态；不同 CLI 目录的共同模型可选，编码专用模型可见并明确
禁选，旧引用保留，普通 API 行为兼容；刷新两侧新增模型，不读取或复制认证内容。

- 全局设置包含默认模型与可选快速模型。
- 每个会话可以覆盖默认模型；切换在下一次请求生效，已经运行的请求继续使用启动时快照。
- 快速模型仅用于意图路由和会话标题；未配置时回退到会话主模型。
- 规划、Schema 对齐、校验、普通对话与 Widget 生成使用会话主模型。
- 不执行跨 Provider 自动故障转移。鉴权、限流、超时、模型缺失与工具不兼容作为结构化错误返回。
- LiteLLM 的普通补全与工具调用路径都属于后端运行时能力；部署依赖必须同时包含工具调用路径所需的
  JSON 编解码组件，避免出现标题可生成但 Agent 对话在路由阶段失败的情况。

## REST API

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | `/api/llm/catalog` | 返回 Provider 预设及其声明式字段 |
| GET/POST | `/api/llm/providers` | 列出脱敏配置或创建 Profile |
| PATCH/DELETE | `/api/llm/providers/{id}` | 更新或删除 Profile |
| POST | `/api/llm/providers/{id}/discover-models` | 刷新并保存模型列表 |
| POST | `/api/llm/providers/{id}/test` | 测试连接或指定模型 |
| POST | `/api/llm/connections/codex/sync` | 将托管 Codex 登录同步为主/快速模型连接，不修改用途绑定 |
| GET/PATCH | `/api/llm/settings` | 读取或更新全局默认/快速模型 |
| PUT | `/api/sessions/{session_id}/model` | 更新会话主模型并广播变更 |

删除仍被默认设置、快速模型或会话引用的 Provider 或模型时返回 `409`。没有可用默认模型时，LLM
请求返回 `llm_configuration_required`，客户端应打开 Provider 设置页。

## Codex 模型目录自动刷新

编码模型切换以已保存的 `settings.agent_models` 为显示依据。保存成功的响应立即更新当前选择，后续刷新不能把保存前启动的旧请求结果覆盖回来；正在保存时禁止重复提交同一选择器，失败保留原选择并显示错误。`GET /api/coding-agents` 在状态探测结束后统一读取模型绑定，避免同一响应中的 `agents[].model_config` 与 `settings.agent_models` 分别来自切换前后。验收使用可控制完成顺序的请求验证“保存时旧刷新晚到”、状态探测期间切换，以及重新打开设置后保持已保存模型。

模型列表通过 Ambient 后端实际使用的 Codex `app-server` 的 `model/list` 发现，不在网页中写死模型名称，也不使用通用 OpenAI API 模型目录替代。连接依次发送 `initialize`、`initialized` 和分页 `model/list`，只展示可见条目及返回的默认模型。这里的 Codex 登录和状态目录由 Ambient 管理；容器部署读取容器内的目录，不自动读取 Windows 桌面 Codex 的账号或缓存。

Codex 已安装并登录时，每次打开模型设置，以及安装完成、版本变化或登录成功后，网页重新获取编码代理的原生模型目录；已启用的 Codex Native Provider 同时通过现有 `discover-models` 同步模型，再更新默认/快速模型选择器。新建的原生 Provider 也会触发一次发现。API Provider 保持现有手动发现行为，模型合并仍遵循上面的引用与能力保留规则。自动刷新不改变默认、快速或编码代理模型绑定，并继续提供手动刷新。

关闭设置或退出登录后，旧编码代理目录请求不能覆盖下一次打开或登录后的列表；退出登录清除网页中的编码代理目录。刷新失败显示错误并保留此前显示的数据，下一次打开设置或手动刷新可重试，不自动循环请求。验收使用模拟目录验证新增模型在重新打开与重新登录后出现，以及刷新期间关闭/退出登录时旧结果被忽略。

关闭设置不会取消正在进行的登录：同一登录操作的设备码结果可保留到重新打开设置，取消、退出登录、启动新登录或账号状态变化会使旧结果失效。登录期间父页面同步“正在登录”状态仍保留设备码和轮询，避免关闭或状态同步使登录界面停住。

`model/list` 可能使用 Codex 自身的缓存或内置目录，因此列表不是实时 entitlement 检查。模型在目录中出现不代表通过 Ambient 原生推理的版本、平台和能力校验；实际调用仍遵循下方原生主模型契约，并通过指定模型测试确认。

## 托管 Codex 安装校验

编码 CLI 目标版本为官方 `0.159.3`，ACP bridge 固定 `@agentclientprotocol/codex-acp@2.1.1`，位于 bridge 声明的 `^0.159.1` Codex 兼容范围内。即使官方发布更高版本，也需完成兼容验证后再更新项目固定版本；目录中的模型列表仍动态取得。

编码 CLI 的 `CODEX_HOME` 采用独立版本目录，隔离 `models_cache.json`、会话与配置；仅 `auth.json` 通过受信符号链接引用原 Ambient 托管登录文件，不读取或复制认证内容。登录与退出仍由原托管目录的 CLI 统一管理，退出删除源登录后编码目录也立即失效。已有错误链接或普通认证文件均拒绝使用，不能悄悄采用第二个账号。CLI 的登录文件原地保存行为须在升级兼容性验证中复核。

编码代理的目录发现和 ACP 执行使用独立、版本固定的新版 CLI；原生主/快速推理在安装后复用已适配的 `0.159.3` 二进制，未升级安装保留 `0.145.0` profile。两者状态目录分离。Codex 服务端返回的模型目录受客户端版本影响，旧 CLI 刷新目录不保证能出现桌面新版 CLI 的模型。因此展示目录统一读取编码 CLI，主/快速兼容性单独预检。设置页显示实际编码 CLI 版本；已有旧安装可通过“更新”操作安装项目验证的新版本，完成后自动重新发现模型。模型名称仍来自该执行 CLI 的 `model/list`，不复制桌面目录或加入未经执行 CLI 返回的名称。

新版编码 CLI 安装到独立的版本目录，保持原有 `bin/codex`、托管登录、主模型配置与模型绑定。首次安装先提供固定登录 CLI，再安装编码 CLI；已有旧安装只新增编码 CLI。两种用途共享 CLI 管理的 Ambient 登录，均不读取或复制桌面认证；公开模型缓存可由 CLI 刷新，主模型执行确切适配版本、配置与模型元数据校验，不自动接受未来编码 CLI 版本。显式 `CODEX_COMMAND` 由部署者管理，网页不得替换。下载、归档成员、精确尺寸与版本校验失败时保留已有 CLI 可用，操作显示失败并允许重试；并发更新复用同一操作。

网页安装固定官方 `0.145.0` CLI，先校验平台对应的归档 SHA-256，再仅复制该归档中名称精确匹配的普通文件。压缩下载仍受 160MiB 上限与逐块计数限制；展开文件须匹配每个固定发布包独立记录的精确尺寸，不能将压缩上限误用于展开 CLI。已核对的 Linux x86_64 包压缩为 113,724,150 bytes、CLI 为 310,730,800 bytes，因此合法安装不应被 160MiB 展开阈值拒绝。

尺寸不符（包括相差一字节）、错误路径、符号或硬链接、下载超限、SHA 不符和版本探测不符均失败，且清理本次安装 staging。校验通过后仍以 `0700` 权限写入既有托管目录并执行固定版本探测，不解包其他成员、不切换版本或模型、不读写原生认证。验收先用隔离的合成归档证明合法展开尺寸可安装及上述负例被拒，再由正常网页安装流程确认真实 Docker 持久卷安装。

## 原生 Codex 主模型接入契约

### 0.159.3 原生推理适配

新版原生主/快速推理使用同一固定 `0.159.3` 编码二进制，并在
`agents/codex/inference/0.159.3/state` 使用独立推理目录，隔离模型缓存、配置与会话。
`CodingAgentRuntime.inference_command`、`inference_state_dir`、`inference_environment` 提供
受信启动边界；仅 `auth.json` 以受信符号链接共享原托管登录，不读取或复制认证内容。
托管新版命令和 home 返回绝对路径，工作区或 Runtime 根为相对路径时仍能从项目外 cwd 启动。
推理目录及其版本祖先不能是符号链接或非目录；已有独立认证文件、错误链接均拒绝。
登录/退出仍由原托管状态与 CLI 管理。没有新版托管二进制时保留旧 `0.145.0` profile；
显式 `CODEX_COMMAND` 沿用部署者命令与状态目录，传输仍核对确切受支持版本。
一旦选择新版 profile，失败不自动回退旧 CLI、其他模型或 API。

传输按确切 CLI 与 initialize 版本选择独立安全 profile，两者均保持 Linux 执行限制、
项目外临时 cwd、ephemeral thread、只读/无网络沙箱、空环境/动态工具/能力根、空 MCP、
自建进程组、固定 deadline 与消息/字节预算、受信配置回读及严格最终 JSON。
新版关闭 `cloud.skills`、休眠、后台/界面及新增外部能力，显式使用文件认证存储，并核对
typed config 与原始 sessionFlags；旧 profile 的断言和配置保持独立。

新版 `code_mode_host=false` 保持 CodeMode host 禁用，不启动外部计算进程，也不宣称
旧版内嵌 V8 在新版仍可执行。模型可直接返回严格文本/JSON；尝试 CodeMode 会安全失败。
仅接受本 thread 的两条官方固定 `warning`：精确 `skip_host_skill_discovery` 启用提示
（路径须为本次捕获的 home/config.toml），以及 host 禁用且 CodeMode 会 fail closed 的提示。
须为确切 message/threadId 字段，不展示正文；其他警告、直接工具回退或未知路径均拒绝。

新版公开模型元数据允许 `code_mode_only` 与 `multi_agent_version=v1|v2`，但代理始终禁用。
实验工具仅允许已审计的 `clock`、`request_user_input_async` 与目录兼容旧名
`send_user_message_async`，其他工具或元数据形状拒绝。`clock` 只读取时间；模型元数据
驱动的异步用户消息仅进入本次临时 thread 的有界缓冲，不转发用户、不中断 Run、不产生
Ambient 工具调用。按新版 `agentMessage.delivery=async` 精确区分这类消息，验证公开字段，同一 item ID 的类型及 delivery 不得改变，
从最终结果中排除；旧 profile 不接受此扩展。最终仅接受一个普通最终回答，验证完整 JSON
及全部已注册 Ambient 工具参数后才返回。未知 delivery、工具 item、通知、callback、身份
或策略仍拒绝；不能通过忽略未知消息实现版本兼容。

新版 `model/list` 可伴随 `account/updated` 公开状态通知。仅接受确切的 `authMode`、
`planType` 两字段：认证为 `chatgpt` 或 null，套餐为固定协议的公开枚举或 null。
通知不保存账户数据、不替代 `account/read` 登录校验、不触发登录或重试；其他认证模式、
未知字段/枚举及旧 profile 的此通知均拒绝。

验收先以官方固定源码/协议 fixture 编写新版 GPT-6 文本、Ambient JSON 工具选择、异步
消息过滤、配置/元数据/身份/原生效果拒绝的 Red→Green；保留完整旧 profile 回归。
独立 Linux 无认证探针验证实际 CLI 与生效配置；随后使用现有托管登录做有界、无工作区
数据的指定 GPT-6 文本与工具选择探针，记录版本、精确模型与结果，不修改用途绑定。
本节升级范围为主/快速传输兼容性，不将它描述为完整 App、公网或恢复验收。
实际结果见 [0.159.3 验收记录](../verification/native-codex-0-159-3-2026-10-02.md)。

此节是2026-10-01公网验收发现能力缺口后、先于产品代码写入的接入与验收规范。每项能力只有实现、Red/Green与对应真实验收均通过后才能描述为已支持；阶段结果见[公网记录](../verification/remote-workspace-production-2026-10-01.md)，短Chat通过不能替代完整App或恢复验收。用户通过本机网页创建`codex_native` Provider，选择精确模型ID如`gpt-5.6-luna`，设置现有默认/快速模型和会话覆盖。它使用官方Codex独立登录/订阅；Coding Agent原生模型与Ambient主模型分别配置，不互相改写。

| 配置或接口 | 原生模式的规范行为 |
| --- | --- |
| 预设与模型模式 | 预设`codex_native`，模型`api_mode="codex_native"`；API Provider继续使用`chat_completions`/`responses`，两者不能混用 |
| 声明式字段 | `fields=[]`、`advanced_fields=[]`，Profile的`connection={}`、`credential_refs={}`及提交credentials均为空；不要求API Key，不接受endpoint、headers、profile、命令、启动参数、auth路径或环境变量 |
| 创建、更新与解析 | 现有`/api/llm/providers`及配置载入都验证原生约束；未知连接/凭据在写入前拒绝。`ResolvedModel.credentials={}`，保留精确原生ID，不添加LiteLLM prefix、不跨Provider回退 |
| 模型发现 | 现有`discover-models`要求稳定托管登录，读取编码app-server `model/list`作为统一展示目录，再以主推理目录和模型元数据预检用途兼容性；不以LiteLLM元数据替代。低层`NativeCodexTransport.discover_models`仍可独立于登录发现主推理目录。缓存/目录不证明entitlement或已验证能力，缺登录须明确显示，真实指定模型测试才可记验证通过 |
| 测试与管理 | 现有`/api/llm/providers/{id}/test`执行同一原生transport的有界调用；登录沿用Coding Agent操作，不复制到Provider秘密。远程仍受`workspace.manage`门禁，control-only不能改Provider、default/fast或原生登录配置 |

网页原生Provider的新增/编辑只展示名称和ID，不出现endpoint、凭据或高级连接字段，并提示“使用Ambient托管的Codex登录；在Coding Agent中登录，无需API Key”。OpenCode shared-binding模型列表排除原生Codex Provider；Ambient primary为native时，“继承Ambient主模型”禁用并说明需选择独立API Provider，显式API模型绑定仍可选。Codex自身的原生模型下拉框保持独立，可选择精确Luna，不能暗中切换Ambient primary或快速模型。

`NativeCodexTransport`复用`CodingAgentRuntime`受信命令与托管state目录，公开字段为`runtime`，公共方法为`generate(selection: ResolvedModel, messages, tools) -> LLMResult`、`discover_models() -> list[dict]`和`model_availability() -> list[dict]`。网页、Profile、远程消息与模型输出不能指定可执行文件、shell、auth位置、环境或CLI flags；API Provider凭据不进入原生进程。契约固定为已验证的 Linux 执行 profile，分别适配官方 `0.145.0` 和 `0.159.3` app-server 协议；Windows等其他执行平台在启动原生transport进程前安全拒绝。Windows Host0.159.2的独立CLI能力探针与Coding Agent模型保存不等价于Ambient原生主模型支持，不扩大版本或平台范围。缺安装/登录、不可用模型或不支持的版本/config/model须安全失败，不能降级为CLI exec、API代理或其他模型。

固定0.145.0 CLI不支持`--ignore-user-config`，原生transport不得传入此flag或忽略CLI参数错误。等价的隔离契约是：托管Codex state的`config.toml`必须不存在；任意文件、目录或symlink均在读取/覆盖前拒绝。原生auth文件保持原有CLI登录管理，不读取、复制或改写其内容。每次生成在项目祖先目录之外的全新临时inference cwd中创建ephemeral thread/turn，使用read-only策略、`project_doc_max_bytes=0`及所有强制受信config overrides，明确设置`environments=[]`、`dynamicTools=[]`、`selectedCapabilityRoots=[]`。在thread或模型推理开始前通过`config/read`核对实际生效配置与MCP为空；不支持限制、配置不符或启动参数错误均fail closed，不降级为用户配置或忽略未知flag。

在`turn/start`前核对`thread/start`实际响应的必需安全字段：model为本次精确选择，modelProvider为`openai`，cwd解析后等于本次拥有的临时目录，approvalPolicy为`never`，sandbox为read-only策略。缺失必需字段或实际策略不符均拒绝推理；`runtimeWorkspaceRoots`或`instructionSources`若返回非空也拒绝。按照对应固定版本协议处理可选字段的省略，不凭空要求未知或可选字段必定出现。协议假进程的正常响应也须包含真实required字段，不能用仅有thread/model的简化响应证明安全策略。

Provider名字不能单独证明官方推理入口。固定0.145的[配置校验](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/config/src/config_toml.rs)拒绝`model_providers.openai`同名内置Provider定义，[Provider合并](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/model-provider-info/src/lib.rs)也保留内置项；但独立顶层`openai_base_url`会覆盖内置入口，且在ChatGPT登录下仍优先于默认地址。原生模式在任何account/thread/turn请求前检查`config/read`全部原始layers，各layer与config必须为对象；`openai_base_url`或`chatgpt_base_url`的任何非null显式值均拒绝，包括官方地址、空字符串、错误类型、低优先级或已禁用层；仅省略或null保留内置默认入口。不能仅凭typed配置、Provider名字或登录类型推定官方路由，不开放UI/API覆盖、静默忽略或回退。进程环境继续使用Runtime安全白名单，不把未经固定版本源码证实的环境变量描述为官方endpoint覆盖能力。原生登录仍由CLI管理，不复制凭据。回归测试用合成layers验证这些入口在任何account/thread/turn请求前被拒绝。

不能假定`turn/start`的RPC响应总先于通知。官方0.145的[turn处理](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/app-server/src/request_processors/turn_processor.rs)先提交核心输入再构造响应，[事件处理](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/app-server/src/bespoke_event_handling.rs)独立发送`turn/started`；这是源码支持的顺序风险，不是本轮真实模型race复现。只在本次`turn/start`正在等待响应且thread已经固定时，允许该own-thread的首个`turn/started`绑定一次turn ID，后续RPC响应必须与它一致；响应先到时由响应正常绑定，后续通知仍须匹配。任何不同thread、turn重绑定、响应ID冲突、没有pending请求的未知turn或尚未绑定turn的item均安全拒绝。`thread/start`的实际源码先回复再发送`thread/started`，不因turn顺序兼容而放宽其他身份检查。

请求携带完整历史envelope，包括system/user/assistant/tool消息、先前函数选择与工具结果，不依赖续接的原生thread、旧prompt或项目指令。默认/快速/会话模型沿用现有`ModelSelection`/`ResolvedModel`快照；后续设置更改不能使运行中的Run漂移。Windows0.159.2独立exec探针接受的flag不构成0.145.0 app-server支持证据。

此原生契约目前只传输文本JSON历史，不把图片或其他多模态内容转换为原生输入，不宣称vision支持。模型目录中未知的视觉能力保持unknown，不因模型名字、目录存在或独立CLI探针映射为已支持。

启动前须关闭 Web/search、apps、skills、MCP、hooks、notify、memory、goals、multi-agent、交互式用户输入和文件/终端等工作区副作用能力，并确认固定profile支持这些限制。旧版精确 Luna 元数据可要求 `code_mode_only`：旧 profile 允许内部内存 Plan，以及仅暴露 Plan facade 的已验证 CodeMode exec/wait；该CodeMode是bare V8，不是Node/Deno，无imports或IO，不提供文件、网络或进程能力。这些内部计算不执行Ambient工具、不产生工作区副作用且不展示为用户tool call；通过条件是零工作区副作用，而非零原生tool item。未知原生工具请求、副作用审批、未允许执行项或无法确认限制的版本/config/model须终止并清理，不批准或猜测继续。

仅原生turn的outputSchema使用JSON`{text, tool_calls:[{name, arguments}]}`外层封装：text为字符串，arguments为包含JSON的字符串，外层及每个call的strict schema均为`additionalProperties=false`，name必须属于本次提供的已注册工具白名单。adapter将每个arguments字符串精确解析一次，结果必须为对象，并按对应`tool.function.parameters`的完整JSON Schema验证所有call之后，才能返回任何调用；required、类型、enum及其他约束不能略过。禁止解析外部/网络`$ref`或为验证访问网络，内部defs仅在本地验证后允许。未发布的原生预设不保留对象arguments wire兼容。

adapter再将已验证对象序列化为既有`LLMResult.tool_calls[*].function.arguments` JSON字符串。Ambient公开ToolGateway的逻辑参数仍为对象，通用API Provider wire不变。实际执行仍只经过Ambient注册工具循环、`ToolGateway`、Capability授权、Run、审批、幂等键和审计。未传入工具时tool_calls必须为空。无效外层或参数JSON、非对象参数、未知工具、参数schema不符、超限输出和缺失最终schema回复返回脱敏安全错误，不返回部分调用、不自动repair、再开turn、重放请求或执行正文中的命令。原生内部turn不等于单次底层模型请求，不能虚称整个Agent流程只有一次模型请求。

采用服务端固定、测试可注入的默认90秒deadline和有界stdio。成功、失败、超时与取消均等待关闭本次拥有的Linux app-server进程组并回收临时目录，不留孤儿进程；取消保留上层取消语义。父进程已退出不能跳过组清理，后代忽略SIGTERM时仍须有界强制终止；二次取消也不能使启动或清理失去拥有者。只操作本次创建的进程组，先停止进程再删除临时cwd，并以无模型的自建父子进程回归验证后代退出。Windows不以taskkill或父进程wait推定零孤儿保证，而是在transport入口拒绝。错误只含有界code/message，不返回stdout/stderr、auth、历史prompt、路径、设备码或回复正文。Usage仅在上游确有数据时标准化，不伪造缺失统计。

失败诊断只使用固定、白名单内的reason/category区分配置、版本、模型元数据、输入/输出预算、thread策略、身份、原生item、通知、callback、协议或最终输出等拒绝阶段。版本schema内已知但禁止的item/method可对应预定义安全枚举；未知method/type仅归入固定unsupported类别，绝不将原始名字、参数、上游错误、正文或路径拼入reason/message/audit。`LLMConfigError`在现有审计链路中保留该白名单reason，使配置拒绝与真实工具能力测试失败可以区分；不把通用`llm_capability_unsupported`直接解释为模型不支持工具。诊断不改变严格 fail-closed、版本对应的安全 profile 或无自动重试契约，不重放失败模型请求。先用注入协议的Red/Green验证每类理由、未知method/type与私有内容的脱敏、审计保留固定reason、owned进程清理及零追加推理，再将诊断用于用户授权的独立运行尝试。

实现先写注入process/protocol的确定性失败测试：预设/凭据拒绝、独立发现和登录、完整history、文本与已注册工具选择、原生arguments字符串精确解析一次、非对象/坏JSON/参数类型或required或enum不符拒绝、所有call验证前不返回部分调用、外部ref不解析/本地defs校验、严格外层与call字段、未知工具/不支持版本或非Linux平台、config.toml文件/目录/symlink拒绝且不读写、项目祖先隔离、实际config/read与MCP空校验、真实required字段的thread响应与错误model/provider/cwd/approval/sandbox拒绝、可选roots/instructionSources非空拒绝且正常省略兼容、turn通知先到/响应先到兼容与pending own-thread首次绑定、wrong-thread/重绑定/冲突响应/无pending/未绑定item拒绝、限制配置与允许Plan-only内部项、取消/超时/启动中取消与二次取消清理、模型快照、既有API兼容和无自动重试；另以无模型的Linux自建父子进程测试父先退出和后代忽略SIGTERM时仍零孤儿。单元测试不调用真实模型。真实验收另通过网页创建Provider并把default/fast设为精确`gpt-5.6-luna`，执行有界Chat、持久Run和App流程，再测远程恢复与撤销。UML公共子集见[后端UML](/architecture/uml.md)；[公网记录](../verification/remote-workspace-production-2026-10-01.md)的独立CLI探针不替代主Provider验收。

前端对`llm_capability_unsupported`显示通用、本地化提示：中文“所选模型或运行配置不支持这次 Agent 请求。”，英文“The selected model or runtime configuration does not support this agent request.”。保持现有错误code、事件处理与弹窗流程；不再将所有原生profile或协议拒绝误述为仅工具调用不兼容。先用前端本地化提示回归测试验证两语言及其他Provider错误兼容，再完成回归；该pure helper由App既有事件handler调用，不将helper测试描述为真实事件或公网验收。

固定0.145的普通`error`通知须按生成协议处理：必需`error`、`threadId`、`turnId`和布尔`willRetry`，只接受本次已绑定的own thread/turn。`TurnError`必需字符串`message`，可选`additionalDetails`为字符串/null，`codexErrorInfo`为固定枚举对象/字符串或null；只验证这些正文值的类型，不提取、显示或审计原始`message`/`additionalDetails`。未知shape、错误类型或未知枚举字符串仍fail closed。`willRetry=true`仅表示官方上游在当前turn内部重试；继续等待本次已有90秒deadline、字节与消息条数预算内的通知，不新增RPC、thread、Run、模型请求重放或Workspace工具执行。`willRetry=false`为terminal，只按固定`codexErrorInfo`/已知HTTP状态分类为脱敏provider、rate或timeout错误，然后清理本次进程；不读取错误正文猜测原因。

固定`CodexErrorInfo`字符串为`contextWindowExceeded`、`sessionBudgetExceeded`、`usageLimitExceeded`、`serverOverloaded`、`cyberPolicy`、`internalServerError`、`unauthorized`、`badRequest`、`threadRollbackFailed`、`sandboxError`、`other`。已知单key对象variant为`httpConnectionFailed`、`responseStreamConnectionFailed`、`responseStreamDisconnected`、`responseTooManyFailedAttempts`（内部可选uint16/null `httpStatusCode`），或`activeTurnNotSteerable`（内部必需`turnKind=review|compact`）。回归使用真实必需字段，验证同turn retry状态不产生新RPC、terminal净化映射、错thread/turn或未知shape/string拒绝、原始错误正文不入audit及owned清理；仍拒绝MCP、dynamic、shell等未允许工具。此独立协议兼容修正不证明本轮AlignData的真实失败原因，须由新的用户授权运行另行确认。

terminal正常化还使用既有`llm_auth_failed`处理固定`unauthorized`或已知HTTP401/403；这与原生独立登录错误一致，不读取错误正文判断。`usageLimitExceeded`/`sessionBudgetExceeded`或已知HTTP429映射rate，已知HTTP408/504映射timeout；其他已知枚举/variant（含`serverOverloaded`）以及合法省略/null info映射provider。auth仅是既有错误code的固定元数据分类，不增加工具、登录操作、模型回退或重试。

## 支持范围

预设覆盖 OpenAI、Anthropic、Google、xAI、Mistral、Cohere、DeepSeek、OpenRouter、Groq、
Together、Fireworks、Cerebras、Perplexity、NVIDIA、Hugging Face、Vercel、MiniMax Global、MiniMax 中国、Kimi、
Qwen、Doubao、GLM、SiliconFlow、Azure、Bedrock、Vertex AI、Databricks、watsonx、Cloudflare、
Ollama、LM Studio、vLLM、llama.cpp、TGI 与 Xinference。通用入口支持 OpenAI Chat、OpenAI
Responses、Anthropic-compatible、LiteLLM Proxy 和自定义 LiteLLM provider。

本机设置沿用单用户信任模型；通过云节点访问的远程客户端另受授权范围限制，control-only不能修改Provider。
自定义 endpoint 会让后端访问用户填写的 HTTP(S) 地址，因此只能在受信网络中开放设置 UI。
