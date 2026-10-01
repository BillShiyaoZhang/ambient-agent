# 公网工作区完整运行验收

## 当前结论

2026-10-01 的真实公网验收已证明核心链路可运行：真实账户配对与本机批准、公网 Widget 和聊天、本机原生 `gpt-5.6-luna` 主模型与 Codex 代码生成、App 验证与发布、活动 Run 在云连接中断时继续完成、同一授权恢复，以及门户和本机两种撤销。新生成 App 在公网实际完成 `READY → DONE → READY` 交互。最终授权已主动撤销，持久凭据已清空，本轮隔离服务和登录副本已清理。

该结论覆盖下文直接观察的核心行为。十项验收场景包含的 Cookie 属性、旧 Cookie 的 401、真实浏览器安全负例、内存 runner/轮询计数、五次自动重试次数和 Native 内部请求计数没有全部直接采证，不能把整张矩阵标为全部通过。当前配置也没有通过第二张完整活动页面或两个 App Frame 并行容量验收。

本轮 Ambient 改动的最终源码回归为后端 **995 通过、22 跳过**，前端 **292 通过**；build、lint、Ruff 和中英文 UML 检查通过。本文整理时 GitHub `main` 发布仍待执行，不能以本机测试结果宣称已经推送。Ambient 保持 `main`，不加入 submodule；本轮没有改写云项目或部署生产云服务。

## 已测拓扑与隔离

实际门户为 `https://agent-communication.online`，控制入口为 `https://gateway.workspace.agent-communication.online`，节点为 `https://<24hex>.workspace.agent-communication.online`。只读运行态采集确认部署显式选择 `WORKSPACE_GATEWAY_ORIGIN_MODE=same-site-subdomains`，应用预约预算 `WORKSPACE_GATEWAY_BUFFER_BYTES=268435456`（256 MiB），Gateway 容器上限 384 MiB、0.5 CPU。默认 `separate-site` 和完整 PSL 校验保留，没有为验收放宽 TLS、hostname 或门户保护。

云侧[临时子域名发布记录](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/blob/main/docs/releases/WORKSPACE_TEMPORARY_SUBDOMAIN_2026-10-01.md)与本轮实际采集一致：Web revision 为 [4abb3f34331f2d26be411cf0af60bd3827b26314](https://github.com/BillShiyaoZhang/agent-collaboration-web/tree/4abb3f34331f2d26be411cf0af60bd3827b26314)，镜像为 `sha256:c974855860cf9ef173bca1cc3df765dc4fbeaa9507f192652c426e1705b9b2dd`；Gateway revision 为 [152dea974f09e5dbca4d0da16867df1be9d3395e](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/152dea974f09e5dbca4d0da16867df1be9d3395e)，镜像为 `sha256:bb265785e5d1c446f599712d3a06f9e028bae9d22b4d9e7659a653b688b1de8f`。Ambient 本轮开始的已发布基线为 [793da83ac263aebe26dc989d5e025d8b7c7c01de](https://github.com/BillShiyaoZhang/ambient-agent/tree/793da83ac263aebe26dc989d5e025d8b7c7c01de)，随后在本机实现并验证原生主 Provider 与兼容修复。

本机使用独立合成工作区 `.cache/ambient-public-e2e-20261001/workspace` 和独立 Neo4j，实际适配器为 `Neo4jGraphDatabase`，`RETURN 1` 成功，Neo4j 仅监听 loopback 9747/9768。完整 Runtime 使用受支持的 Linux Docker 拓扑、固定 Docker upstream 和 Unix socket，smoke=1。Runtime socket 为 0660，未授权 uid 65534 的实际访问返回 EACCES；初始控制 App 的真实首帧 smoke 通过，没有模型调用或 App promotion。

本轮仅操作身份已核对的测试节点与自建服务。用户授权为 control-only、不含模型/Coding Agent/技能管理范围，每次最多一小时；在本机网页配置模型使用另一个本机管理上下文。已有 Hermes、原工作区与业务数据不作为测试目标。Linux 隔离 Codex 使用自建状态目录中的登录副本，副本已清理，原本机登录文件保留且内容未被检查或输出。截图和安全元数据留在 ignored observation 目录，不提交账号、邮箱、接入码、凭据、Cookie、launch ticket、模型密钥、原始请求或业务回复正文。

此前 [2026-10-01 交接验收](remote-workspace-handoff-2026-10-01.md)的 Windows、loopback、合成账户、SQLite 测试适配器和无模型调用结果仍是历史证据，不能代替本轮真实 HTTPS、Neo4j、模型和持久 Run 结果。下文时间均为 **UTC，2026-10-01**；北京时间加八小时。

## 验收矩阵与证明范围

| 编号 | 场景及契约 | 本轮直接结果 | 未直接证明的细项 |
| --- | --- | --- | --- |
| 1 | 真实账户发码、本机配对、同账户领取、核对范围与期限后批准；未批准不能打开 | 第二至第四轮正常授权链路逐次提交，Local Connected、Portal Online；首轮待批准到期保留为预期历史。第四轮用于真实模型、App 和恢复，最终撤销 | 没有在生产中额外构造错账户或重放接入码；这些边界由独立本地回归覆盖 |
| 2 | HTTPS launch 消费票据后清理 URL；节点会话具有规定的 Cookie 属性 | 门户实际打开节点且 URL 无 ticket；有效授权下 clean URL 重载无需再次 launch 即成功 | `__Host-ambient_workspace` 的 Secure、HttpOnly、Lax、Path=/、无 Domain 由固定云源码及本地回归支持；内置浏览器没有提供独立 Set-Cookie 元数据，属性未在本轮逐项直接捕获 |
| 3 | 临时同站模式拒绝节点向门户的跨 origin fetch/form/iframe，账户不被替换 | 11 个无 Cookie/Auth 的实际 HTTPS GET 探针中，八项合成节点→门户 Fetch Metadata 请求均 403；门户登录、领取与地址栏返回正常 | 合成 metadata 是服务器响应证据，不能代替带真实 Cookie 的浏览器安全负例、普通链接导航或完整账户不替换证明 |
| 4 | 隔离 Frame、一次性 Runtime 握手与真实 Widget 交互 | 原控制 App Count 0→1，超过 75 秒空闲后仍交互；新生成 App 的公网 Frame 实际显示 READY，Toggle 后 DONE，再次 Toggle 后 READY | 完整模块/vendor/CSP/CORS/握手头没有逐项独立采证。新 App smoke 为 workflow gate 所蕴含通过，直接 smoke 结果未单独捕获；公网渲染与交互是另外的直接证据 |
| 5 | chat/runs/run-live/client-runtime 四条 WSS 同时活动，空闲后可用 | Cloud 快照实际 WS=4、Tunnel=1；空闲超过 nginx 75 秒读超时后控制 Widget 仍响应；真实模型与新 App 单页后来也达到四条 WS | 单次快照和所测交互不代表所有负载或任意长空闲已覆盖 |
| 6 | 本机真实模型、持久 Run、最终消息与 App 代码生成/验证/发布 | Native Model 和 Test tools 通过；公网短 Chat 一次提交取得预期 Receipt 与 succeeded Run。新的受控 App Run 成功，模型快照精确为 Luna，生成、验证和 promotion 均完成 | 第一次 App 在 AlignData 失败保留，具体原因未证实。Native 内部模型请求计数未捕获；合法的一次 workflow autoRepair 增加模型预算，不宣称零额外模型请求 |
| 7 | 只暂停自建 Connector，本机活动 Run 继续；同身份/grant 恢复且无请求重放 | Connector 约离线 49 秒，同一 Run 在恢复前已 succeeded；恢复后 event/message/step 前缀、模型快照和数量保持，没有新增 Run/route/用户或最终消息；显式连接与 Widget Retry 后新 App 可交互 | 五次自动重试的实际次数未独立计数；容器级进程快照不是进程→Run 的独立绑定证据 |
| 8 | 门户撤销与本机撤销使授权失效、关闭转发并清凭据；云通知不可达也能本机撤销 | 两条撤销路径实际完成，Cloud WS/Tunnel/HTTP 归零，API 与持久状态均 revoked、凭据清空、approved=false。最终第四轮在期限未到时主动撤销 | 撤销后重载保留旧 DOM，未独立捕获旧 Cookie HTTP/主导航 401；内存 runner 和云 poll 请求计数未捕获。源码取消路径加资源归零支持停止转发，不能替代这些直接计数 |
| 9 | control-only 允许聊天/Widget，拒绝管理；公开控制 Host 不暴露账户和 metrics | 公网 Provider 变更实际 403 且原 Provider 数量保持 0；远程连接管理入口缺席；无凭证控制 Host 账户 API 与 metrics 实际 404，节点匿名访问 401 | 没有把每条管理 API、所有 Host 误判和真实浏览器攻击负例都列为公网直接通过；相应源码及本地回归范围独立保留 |
| 10 | 资源预算拒绝有界，关闭测试连接后回收，原节点仍可用 | 单页四 WS、新增第二页 429、关闭第二页后原控制 App 仍交互；最终撤销资源归零，所有采样 OOMKilled=false/restart_count=0 | 第二张完整页面及两个 App Frame 并行容量没有通过；多用户、多节点、满队列、持续模型压力未验收 |

授权到期、Cookie 会话上限、票据/接入码重放、错账户不消费码、在途撤销和单次写不重放的本地测试保留。`scripts/verify_remote_workspace_handoff.py` 使用真实 loopback Tunnel 与回声 fixture，不能替代本轮 TLS、nginx、Widget、模型和持久 Run 观察。

## 真实执行、恢复与撤销

### 网页模型配置与真实执行

按用户授权，在本机网页添加 `Codex Native · local`，精确选择 `gpt-5.6-luna` 为 default、fast 以及 Codex Coding Agent 原生模型，没有填写 API key 或 endpoint。主 Provider 的实际 Model 测试和 Test tools 均成功；公网短 Chat 只提交一次，返回预期 `AMBIENT_REMOTE_MODEL_OK` 并持久化为一个 succeeded Run。其模型快照再次确认 primary/fast/coding native 均为精确 Luna。

Ambient 原生主 Provider 使用固定 Linux Codex 0.145；Windows 宿主 0.159.2 的独立探针、login-status、模型目录和 Coding Agent 网页配置都不是该主 Provider 的运行证明。新受控 App 请求通过计划和数据阶段后，实际进入 Codex 代码生成、验证和发布，最终生成独立的 `Remote Model Receipt 2`，没有改写原控制 App。

### 活动 Run 的断线与恢复

| UTC 时间 | 直接观察 |
| --- | --- |
| 14:52:20 | 新 Run 等待用户，32 events/latest sequence 73；该 Run 1 条用户消息，工作区累计 3 Runs/3 audits，模型预算 3，route attempt=1；Luna 模型快照已保存 |
| 14:52:47 | 同 Run 的 stage_code 为 RUNNING；自建容器内观察到 1 个 Codex app-server、1 个 ACP bridge、0 个 Native Primary 进程。扫描范围为容器，未单独建立进程与 Run 的绑定 |
| 14:52:52 | 自建 IPC 仅停止 Connector，保留节点身份、grant、凭据、approval 和到期时间；没有停止本机 Run 或 Coding Agent |
| 14:52:56 | 同 Run 仍 RUNNING，42 events/latest sequence 83；已保存 event/message/step 前缀、模型快照、Run 数量和 route 次数均保持 |
| 14:53:36 | 尚未恢复 Connector 时，同 Run 已 SUCCEEDED，Codex/ACP 进程数为 0，直接证明活动本机 Run 可在云连接离线时完成 |
| 14:53:41 | 启动原 Connector，约 49 秒离线；相同身份、grant、凭据与许可保持。start ACK 自身 online=false，不能把 ACK 当成已重连；后续公网恢复另行观察 |
| 14:53:45 | 恢复快照为 64 events/latest sequence 105；该 Run 仍 1 user+1 final agent message，累计仍 3 Runs/3 audits，route attempt 仍 1，generation/promote/verify=true，全部持久前缀和模型快照保持 |
| 14:58:02 | 终态复核仍为同一 succeeded Run，64 events、3 Runs/3 audits、1 user+1 final agent message，前缀与数量没有重复；公网新 App Toggle 已实际完成 READY→DONE→READY |

模型预算由 3 增至 4，对应一次合法 workflow autoRepair，修正生成 manifest 的 `app_spec`；这不等于连接恢复重新执行 Run。公网显示 Completed/Publish 和一次 repair。Native 底层模型请求计数仍为 null，不能从预算或 audit 数量推断底层调用总数。事件 sequence 严格递增，但属于工作区序列，不以逐项连续加一作为单 Run 恢复要求。

浏览器显式 Retry connection 与 Retry Widget 后恢复；两个 App Frame 并行加载期间曾超时。只关闭原控制 App 窗口、保留新 App 并再次 Retry Widget Runtime 后，实际 Frame 显示 READY，Toggle 两次依次变 DONE、READY。关闭窗口没有删除 App，这段结果证明单个新 App 恢复可用，不证明双 Frame 并行容量通过。

### 撤销与清理

第二轮授权已由门户 Revoke/Confirm 各一次撤销，Cloud WS/Tunnel/HTTP 归零，本机 API 和持久状态均 revoked、online=false、credentials cleared、approved=false。第三轮在自建后端仅对控制入口注入 DNS 不可达：13:55:01 开始，其他 hosts 保持；本机 Revoke 一次后显示 Revoked/cloud notification unavailable，公网页面离线。13:55:37 恢复原 hosts，13:56:09 Cloud WS/Tunnel/HTTP=0，13:58:07 和 13:58:43 两次许可复核均保持凭据清空，授权期限 14:06:26 仍在未来；随后门户撤销残留 Cloud grant。

最终第四轮也由门户 Revoke/Confirm 各一次撤销，门户显示已撤销、公网新 App 显示 Authorization inactive、本机显示 Revoked。**15:00:57** Cloud 实际 WS/Tunnel/HTTP=0，采集请求自身 control=1，预约 32,768 字节、queued=0。**15:04:02** 独立只读核验确认 API 与持久状态均 revoked、online=false、credentials cleared、approved=false，原身份及 15:16:48 期限保持且当时未到期，证明主动撤销而非到期失效。该核验没有采内存 runner 或 poll 请求计数。

**15:08:27** 最终清理核验确认：自建 supervisor 已停止、5 个自建容器已删除、自建网络和 socket volume 不存在、测试 loopback 端口关闭、登录副本不存在；原 Hermes 的容器身份和运行状态保持，原宿主登录文件仍存在，工作区 Graph 数据与 observation 文件保留。清理没有模型调用、没有读取原登录文件内容，没有删除撤销记录、业务数据或其他用户服务。

## 修复与保留的历史失败

本轮发现网页只有 Coding Agent 原生模型选项，尚不能用本机 Codex 登录承担 Ambient default/fast 主模型。先在[Provider 契约](../integrations/llm-providers.md)与[UML](../architecture/uml.md)记录行为，再按 Red–Green 实现 `codex_native` Provider、发现、主模型传输、网页提示和 OpenCode 不支持原生共享 binding 的明确拒绝。主模型传输使用固定 Linux 0.145 app-server，验证托管配置、原始 config layers 的 endpoint 关闭、空 MCP、只读/无外部环境的 thread policy、严格输出与注册工具 JSON Schema，保留 deadline、消息/字节上限和自建进程组清理，不转为 API fallback 或请求重放。

首次原生目录安全预检因固定 0.145 typed 配置投影差异被安全拒绝，account/thread/turn/模型调用均未发生。按官方固定版本 schema 修正后实际发现成功，返回四个模型并包含精确 Luna；此后主 Provider 的真实网页调用另行通过，没有把目录成功当成模型执行。

**第一次 App 仍是失败结果。**一次 Approve plan 后 AlignData 失败，UI 把通用 `llm_capability_unsupported` 映射为“模型不兼容工具调用”。安全只读诊断记录两条消息、17,575 字节 prompt、17,592 字节空 tools envelope、latency 26,370 ms，输入低于 512 KiB，托管模型元数据 guard 通过；源码没有 API-only 或 tool-capability-false 专用门禁，AlignData 未显式传 tools。这排除了已检查的几项解释，没有定位当时具体的原生 item/notification/protocol 拒绝原因。

随后先补固定 reason/category 的安全诊断与泛化网页提示，再修复固定 0.145 普通 `error` 通知兼容：只接受绑定本次 thread/turn 的严格结构，已知 `willRetry=true` 在原 deadline/预算内等待同一 turn，不创建新 RPC；终止错误净化，未知/外来/异常结构仍拒绝。聚焦回归和新受控 App 实际成功证明新行为可用，**不能反推首次 AlignData 失败就是这项静态兼容缺口导致**。失败请求没有自动 Replay。

原生 Windows viewer 的 Count 交互不支持完整 Unix Runtime smoke，因此本轮完整执行切换至 Linux Docker，未关闭 smoke。首次 Docker Backend 在 90 秒内未 HTTP-ready，原因未证实；该阶段自建容器/网络/socket 已清理，之后一次带安全诊断的重试就绪。保留这次历史超时，不把重试成功写成已定位或修复产品故障。

## 最终回归与证据清单

| 最终检查 | 结果 |
| --- | --- |
| 完整后端 | 995 passed、22 skipped、1 项既有 Starlette warning，174.79 秒；22 项为 21 项既有跳过及 Windows 上 1 项 Linux 进程组回归限制；该自建 Linux 父子进程零孤儿回归另在真实 Linux 通过，无模型调用 |
| 完整前端 | 38 files、292 tests passed |
| 前端 build/lint | 通过 |
| Ruff、中英文 UML | 通过 |
| 中英文文档配对与链接 | 整理后的 `scripts/verify_docs.py` 通过，33 对页面 |
| 差异格式 | 整理后的 `git diff --check` 通过 |
| GitHub 发布 | 本文整理时尚待将本轮 Ambient 改动实际推送至 `main`；不提前标为完成 |

本轮回归包含 Native 安全预检、配置/endpoint 限制、registered-tool schema、turn 通知先于 RPC 回应的协议顺序、普通 error 通知、净化诊断、OpenCode binding 拒绝、网页模型提示与本地化。测试使用隔离工作区和注入边界，不依赖真实网络或模型；真实运行证据独立采集。历史 941/953 后端和 290 前端结果不替代最终源码冻结结果。

安全证据保留在 ignored 目录，文件名可定位观察，不含可复用授权秘密：

| 证据组 | 文件与作用 |
| --- | --- |
| 本机观察 | `.cache/ambient-public-e2e-20261001/observations/`：`native-provider-luna-verified.jpg`、`public-native-model-receipt.jpg`、`public-app-align-failed.jpg`、`public-run-connector-offline.jpg`、`public-generated-app-done.jpg`、`public-generated-app-ready-restored.jpg`、`public-final-grant-revoked.jpg`；分别保留模型、成功、失败、离线和实际 Frame/撤销 UI |
| 持久 Run | `owned-run-before-20261001T145220548351Z.json`、`owned-run-offline-20261001T145256613868Z.json`、`owned-run-recovered-20261001T145345041166Z.json`、`owned-run-terminal-20261001T145802214311Z.json`：同 Run 的模型、数量和元数据前缀；不保存正文 |
| 最终撤销与清理 | `final-grant-revocation-verified.json`、`final-owned-cleanup-verified.json`；另保留 `local-revoke-unreachable-verified.json` 的两次不可达撤销核验 |
| Cloud 观察 | `.cache/ambient-public-e2e-20261001-observation/`：`cloud-active-widget-chat-v3.json`、`cloud-second-page-capacity-v3.json`、`cloud-after-native-app-recovery-v3.json`、`cloud-single-generated-app-active-v3.json`、`cloud-final-grant-revoked-v3.json`，记录匹配版本、实际配置、Uvicorn RSS、容器内存及资源计数 |
| 最终后端 | `observations/backend-full-native-protocol-final.log`，记录 995/22/1 及完整运行结束结果 |

Cookie/旧会话、真实浏览器负例、runner/poll、自动重试次数和 Native 内部请求次数的未采证边界是明确结果，不以源码、UI、合成 metadata 或 null 计数字段冒充直接观察。

## 容量与运营限制

| 阶段（UTC） | WS/Tunnel | 应用预约字节 | Gateway Uvicorn RSS 字节 | 容器 stats 字节 | 累计 capacity rejection |
| --- | --- | --- | --- | --- | --- |
| 12:53:54 原控制 App+聊天单页 | 4/1 | 184,351,704 | 43,180,032 | 42,173,726 | 0 |
| 12:56:51 增开本轮第二页，实际 429 | 6/1 | 259,339,208 | 42,561,536 | 47,783,608 | 12 |
| 14:55:13 新 App 恢复、双 Frame 曾超时 | 3/1 | 146,857,952 | 51,576,832 | 55,218,012 | 18 |
| 14:58:14 仅保留新 App 的活动页 | 4/1 | 184,351,704 | 47,984,640 | 52,974,059 | 18 |
| 15:00:57 最终撤销 | 0/0 | 32,768 | 48,087,040 | 55,469,670 | 18 |

各采样 HTTP=0、queued=0、control=1 为采集自身，OOMKilled=false、restart_count=0；Uvicorn 为实际 PID7，PID1 init 的 4,096 字节不是 Gateway RSS。应用预约、RSS、容器 stats 和生命周期 HWM 是不同量，不据此推断阶段峰值或多用户安全容量。累计 rejection 从 12 增至 18，不能写恢复阶段零拒绝。

当前部署实测仅支持本轮**一个完整页面、一个活动 App Frame**的功能通过。第二完整页面受到预算拒绝；双 Frame 并行超时后，关闭原窗口与单 Frame 重试恢复相关，但没有证明容量是唯一原因。原控制页在关闭第二页后仍可 Count 2→3，新 App 单 Frame 可 Toggle；这些局部恢复不能升级为双 App、多节点、多用户或持续模型负载通过。运营平台前应另做容量规划、资源预算及并发/背压负载验收，本轮没有调整生产云限额。

发布记录的 wildcard TLS 使用手动 DNS-01，**2026-12-30 到期，尚无自动续期**。本轮可信 TLS/hostname 访问不证明续期、备份恢复、数据库迁移或完整回滚通过。临时同站模式仍有父域普通 Cookie 耗尽的可用性边界，独立可注册域名仍是长期部署方案；不要为补安全负例或恢复演练覆盖真实数据库或修改其他用户授权。
