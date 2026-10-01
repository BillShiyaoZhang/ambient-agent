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

重新发现按模型 ID 合并，不覆盖已有 API 模式、手工显示名、来源或已验证能力。
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
| GET/PATCH | `/api/llm/settings` | 读取或更新全局默认/快速模型 |
| PUT | `/api/sessions/{session_id}/model` | 更新会话主模型并广播变更 |

删除仍被默认设置、快速模型或会话引用的 Provider 或模型时返回 `409`。没有可用默认模型时，LLM
请求返回 `llm_configuration_required`，客户端应打开 Provider 设置页。

## 托管 Codex 安装校验

网页安装固定官方 `0.145.0` CLI，先校验平台对应的归档 SHA-256，再仅复制该归档中名称精确匹配的普通文件。压缩下载仍受 160MiB 上限与逐块计数限制；展开文件须匹配每个固定发布包独立记录的精确尺寸，不能将压缩上限误用于展开 CLI。已核对的 Linux x86_64 包压缩为 113,724,150 bytes、CLI 为 310,730,800 bytes，因此合法安装不应被 160MiB 展开阈值拒绝。

尺寸不符（包括相差一字节）、错误路径、符号或硬链接、下载超限、SHA 不符和版本探测不符均失败，且清理本次安装 staging。校验通过后仍以 `0700` 权限写入既有托管目录并执行固定版本探测，不解包其他成员、不切换版本或模型、不读写原生认证。验收先用隔离的合成归档证明合法展开尺寸可安装及上述负例被拒，再由正常网页安装流程确认真实 Docker 持久卷安装。

## 原生 Codex 主模型接入契约

此节是2026-10-01公网验收发现能力缺口后、先于产品代码写入的接入与验收规范。每项能力只有实现、Red/Green与对应真实验收均通过后才能描述为已支持；阶段结果见[公网记录](../verification/remote-workspace-production-2026-10-01.md)，短Chat通过不能替代完整App或恢复验收。用户通过本机网页创建`codex_native` Provider，选择精确模型ID如`gpt-5.6-luna`，设置现有默认/快速模型和会话覆盖。它使用官方Codex独立登录/订阅；Coding Agent原生模型与Ambient主模型分别配置，不互相改写。

| 配置或接口 | 原生模式的规范行为 |
| --- | --- |
| 预设与模型模式 | 预设`codex_native`，模型`api_mode="codex_native"`；API Provider继续使用`chat_completions`/`responses`，两者不能混用 |
| 声明式字段 | `fields=[]`、`advanced_fields=[]`，Profile的`connection={}`、`credential_refs={}`及提交credentials均为空；不要求API Key，不接受endpoint、headers、profile、命令、启动参数、auth路径或环境变量 |
| 创建、更新与解析 | 现有`/api/llm/providers`及配置载入都验证原生约束；未知连接/凭据在写入前拒绝。`ResolvedModel.credentials={}`，保留精确原生ID，不添加LiteLLM prefix、不跨Provider回退 |
| 模型发现 | 现有`discover-models`读取实际app-server `model/list`，目录发现可独立于登录状态；不以LiteLLM元数据替代。缓存/目录不证明entitlement或已验证能力，缺登录须明确显示，真实指定模型测试才可记验证通过 |
| 测试与管理 | 现有`/api/llm/providers/{id}/test`执行同一原生transport的有界调用；登录沿用Coding Agent操作，不复制到Provider秘密。远程仍受`workspace.manage`门禁，control-only不能改Provider、default/fast或原生登录配置 |

网页原生Provider的新增/编辑只展示名称和ID，不出现endpoint、凭据或高级连接字段，并提示“使用Ambient托管的Codex登录；在Coding Agent中登录，无需API Key”。OpenCode shared-binding模型列表排除原生Codex Provider；Ambient primary为native时，“继承Ambient主模型”禁用并说明需选择独立API Provider，显式API模型绑定仍可选。Codex自身的原生模型下拉框保持独立，可选择精确Luna，不能暗中切换Ambient primary或快速模型。

`NativeCodexTransport`复用`CodingAgentRuntime`受信命令与托管state目录，公开字段为`runtime`，公共方法为`generate(selection: ResolvedModel, messages, tools) -> LLMResult`和`discover_models() -> list[dict]`。网页、Profile、远程消息与模型输出不能指定可执行文件、shell、auth位置、环境或CLI flags；API Provider凭据不进入原生进程。契约固定为已验证的Linux执行profile及官方0.145.0 app-server协议；Windows等其他执行平台在启动原生transport进程前安全拒绝。Windows Host0.159.2的独立CLI能力探针与Coding Agent模型保存不等价于Ambient原生主模型支持，不扩大版本或平台范围。缺安装/登录、不可用模型或不支持的版本/config/model须安全失败，不能降级为CLI exec、API代理或其他模型。

固定0.145.0 CLI不支持`--ignore-user-config`，原生transport不得传入此flag或忽略CLI参数错误。等价的隔离契约是：托管Codex state的`config.toml`必须不存在；任意文件、目录或symlink均在读取/覆盖前拒绝。原生auth文件保持原有CLI登录管理，不读取、复制或改写其内容。每次生成在项目祖先目录之外的全新临时inference cwd中创建ephemeral thread/turn，使用read-only策略、`project_doc_max_bytes=0`及所有强制受信config overrides，明确设置`environments=[]`、`dynamicTools=[]`、`selectedCapabilityRoots=[]`。在thread或模型推理开始前通过`config/read`核对实际生效配置与MCP为空；不支持限制、配置不符或启动参数错误均fail closed，不降级为用户配置或忽略未知flag。

在`turn/start`前核对`thread/start`实际响应的必需安全字段：model为本次精确选择，modelProvider为`openai`，cwd解析后等于本次拥有的临时目录，approvalPolicy为`never`，sandbox为read-only策略。缺失必需字段或实际策略不符均拒绝推理；`runtimeWorkspaceRoots`或`instructionSources`若返回非空也拒绝。按照固定0.145协议处理可选字段的省略，不凭空要求未知或可选字段必定出现。协议假进程的正常响应也须包含真实required字段，不能用仅有thread/model的简化响应证明安全策略。

Provider名字不能单独证明官方推理入口。固定0.145的[配置校验](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/config/src/config_toml.rs)拒绝`model_providers.openai`同名内置Provider定义，[Provider合并](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/model-provider-info/src/lib.rs)也保留内置项；但独立顶层`openai_base_url`会覆盖内置入口，且在ChatGPT登录下仍优先于默认地址。原生模式在任何account/thread/turn请求前检查`config/read`全部原始layers，各layer与config必须为对象；`openai_base_url`或`chatgpt_base_url`的任何非null显式值均拒绝，包括官方地址、空字符串、错误类型、低优先级或已禁用层；仅省略或null保留内置默认入口。不能仅凭typed配置、Provider名字或登录类型推定官方路由，不开放UI/API覆盖、静默忽略或回退。进程环境继续使用Runtime安全白名单，不把未经固定版本源码证实的环境变量描述为官方endpoint覆盖能力。原生登录仍由CLI管理，不复制凭据。回归测试用合成layers验证这些入口在任何account/thread/turn请求前被拒绝。

不能假定`turn/start`的RPC响应总先于通知。官方0.145的[turn处理](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/app-server/src/request_processors/turn_processor.rs)先提交核心输入再构造响应，[事件处理](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/app-server/src/bespoke_event_handling.rs)独立发送`turn/started`；这是源码支持的顺序风险，不是本轮真实模型race复现。只在本次`turn/start`正在等待响应且thread已经固定时，允许该own-thread的首个`turn/started`绑定一次turn ID，后续RPC响应必须与它一致；响应先到时由响应正常绑定，后续通知仍须匹配。任何不同thread、turn重绑定、响应ID冲突、没有pending请求的未知turn或尚未绑定turn的item均安全拒绝。`thread/start`的实际源码先回复再发送`thread/started`，不因turn顺序兼容而放宽其他身份检查。

请求携带完整历史envelope，包括system/user/assistant/tool消息、先前函数选择与工具结果，不依赖续接的原生thread、旧prompt或项目指令。默认/快速/会话模型沿用现有`ModelSelection`/`ResolvedModel`快照；后续设置更改不能使运行中的Run漂移。Windows0.159.2独立exec探针接受的flag不构成0.145.0 app-server支持证据。

此原生契约目前只传输文本JSON历史，不把图片或其他多模态内容转换为原生输入，不宣称vision支持。模型目录中未知的视觉能力保持unknown，不因模型名字、目录存在或独立CLI探针映射为已支持。

启动前须关闭Web/search、apps、skills、MCP、hooks、notify、memory、goals、multi-agent、用户输入和文件/终端等工作区副作用能力，并确认固定profile支持这些限制。精确Luna元数据可要求`code_mode_only`：允许内部内存Plan，以及仅暴露Plan facade的已验证CodeMode exec/wait；该CodeMode是bare V8，不是Node/Deno，无imports或IO，不提供文件、网络或进程能力。这些内部计算不执行Ambient工具、不产生工作区副作用且不展示为用户tool call；通过条件是零工作区副作用，而非零原生tool item。未知原生工具请求、副作用审批、未允许执行项或无法确认限制的版本/config/model须终止并清理，不批准或猜测继续。

仅原生turn的outputSchema使用JSON`{text, tool_calls:[{name, arguments}]}`外层封装：text为字符串，arguments为包含JSON的字符串，外层及每个call的strict schema均为`additionalProperties=false`，name必须属于本次提供的已注册工具白名单。adapter将每个arguments字符串精确解析一次，结果必须为对象，并按对应`tool.function.parameters`的完整JSON Schema验证所有call之后，才能返回任何调用；required、类型、enum及其他约束不能略过。禁止解析外部/网络`$ref`或为验证访问网络，内部defs仅在本地验证后允许。未发布的原生预设不保留对象arguments wire兼容。

adapter再将已验证对象序列化为既有`LLMResult.tool_calls[*].function.arguments` JSON字符串。Ambient公开ToolGateway的逻辑参数仍为对象，通用API Provider wire不变。实际执行仍只经过Ambient注册工具循环、`ToolGateway`、Capability授权、Run、审批、幂等键和审计。未传入工具时tool_calls必须为空。无效外层或参数JSON、非对象参数、未知工具、参数schema不符、超限输出和缺失最终schema回复返回脱敏安全错误，不返回部分调用、不自动repair、再开turn、重放请求或执行正文中的命令。原生内部turn不等于单次底层模型请求，不能虚称整个Agent流程只有一次模型请求。

采用服务端固定、测试可注入的默认90秒deadline和有界stdio。成功、失败、超时与取消均等待关闭本次拥有的Linux app-server进程组并回收临时目录，不留孤儿进程；取消保留上层取消语义。父进程已退出不能跳过组清理，后代忽略SIGTERM时仍须有界强制终止；二次取消也不能使启动或清理失去拥有者。只操作本次创建的进程组，先停止进程再删除临时cwd，并以无模型的自建父子进程回归验证后代退出。Windows不以taskkill或父进程wait推定零孤儿保证，而是在transport入口拒绝。错误只含有界code/message，不返回stdout/stderr、auth、历史prompt、路径、设备码或回复正文。Usage仅在上游确有数据时标准化，不伪造缺失统计。

失败诊断只使用固定、白名单内的reason/category区分配置、版本、模型元数据、输入/输出预算、thread策略、身份、原生item、通知、callback、协议或最终输出等拒绝阶段。版本schema内已知但禁止的item/method可对应预定义安全枚举；未知method/type仅归入固定unsupported类别，绝不将原始名字、参数、上游错误、正文或路径拼入reason/message/audit。`LLMConfigError`在现有审计链路中保留该白名单reason，使配置拒绝与真实工具能力测试失败可以区分；不把通用`llm_capability_unsupported`直接解释为模型不支持工具。诊断不改变严格fail-closed、安全profile、允许的内部Plan/CodeMode或无自动重试契约，不重放失败模型请求。先用注入协议的Red/Green验证每类理由、未知method/type与私有内容的脱敏、审计保留固定reason、owned进程清理及零追加推理，再将诊断用于用户授权的独立运行尝试。

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
