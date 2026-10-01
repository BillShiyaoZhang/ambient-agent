# 公网工作区完整运行验收

## 当前结论

截至 **2026-10-01 17:27 UTC**，真实公网验收已证明核心链路可运行：真实账户配对与本机批准、公网 Widget 和聊天、本机原生 `gpt-5.6-luna` 主模型与 Codex 代码生成、App 验证与发布、活动 Run 在云连接中断时继续完成、同一授权恢复，以及门户和本机两种撤销。新生成 App 在公网实际完成 `READY → DONE → READY` 交互。第四轮模型/App 阶段及随后第五轮安全补证授权均已主动撤销，持久凭据已清空。第五轮还直接证明 launch/Cookie 属性、管理 403、真实浏览器门户边界和旧 Cookie 401；撤销后连续 132.73 秒的 runner/轮询核验、两阶段隔离服务清理与自建探针资产清理均通过。

该结论覆盖下文直接观察的核心行为。第五轮既有原登录内置浏览器的 DOM/账户标签证据，又有新隔离浏览器的 CDP 状态与实际 Node Cookie 证据，两者不混用：隔离浏览器没有导入原门户登录 Cookie。`/ws/runs` 的五次自动重试和一次手动恢复已直接计数；撤销后直接观察 runner/tunnel 不存在与轮询产品方法调用计数保持，不把这些计数当作网络包数。原登录门户 Cookie 的攻击请求头、Native 内部请求计数仍未捕获，Frame 全部资源/安全头没有逐项独立采证，不能把整张矩阵标为全部通过。当前配置也没有通过第二张完整活动页面或两个 App Frame 并行容量验收。

本轮原生修复已发布到 GitHub `main` 的 [8f4bfdb](https://github.com/BillShiyaoZhang/ambient-agent/commit/8f4bfdb235f7c0cc882535220d54a3f609bdca06)，随后仅格式化修复发布为 [c2561a6](https://github.com/BillShiyaoZhang/ambient-agent/commit/c2561a6c863058b4725387331c49a248e95d4012)，远端仅保留 `main`。后者的远端后端 CI 全部通过（**1002 通过、15 跳过**，真实 Neo4j 专项 **31 通过**）；本机格式修复后为 **995 通过、22 跳过**，前端 **292 通过**。c2561a6 的 CI 唯一失败为两个 Chromium 渲染测试超时，本机不改源码的并行与串行比较均 36/36 通过，尚未定位原因；单次 job 重跑因连接器权限 403 未启动。本次发布的整轮状态以 [main 提交关联 checks](https://github.com/BillShiyaoZhang/ambient-agent/actions/workflows/ci.yml?query=branch%3Amain) 为准，历史与本机通过不代替后续远端结果。Ambient 不加入 submodule；本轮没有改写云项目或部署生产云服务。

## 已测拓扑与隔离

实际门户为 `https://agent-communication.online`，控制入口为 `https://gateway.workspace.agent-communication.online`，节点为 `https://<24hex>.workspace.agent-communication.online`。只读运行态采集确认部署显式选择 `WORKSPACE_GATEWAY_ORIGIN_MODE=same-site-subdomains`，应用预约预算 `WORKSPACE_GATEWAY_BUFFER_BYTES=268435456`（256 MiB），Gateway 容器上限 384 MiB、0.5 CPU。默认 `separate-site` 和完整 PSL 校验保留，没有为验收放宽 TLS、hostname 或门户保护。

云侧[临时子域名发布记录](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/blob/main/docs/releases/WORKSPACE_TEMPORARY_SUBDOMAIN_2026-10-01.md)与本轮实际采集一致：Web revision 为 [4abb3f34331f2d26be411cf0af60bd3827b26314](https://github.com/BillShiyaoZhang/agent-collaboration-web/tree/4abb3f34331f2d26be411cf0af60bd3827b26314)，镜像为 `sha256:c974855860cf9ef173bca1cc3df765dc4fbeaa9507f192652c426e1705b9b2dd`；Gateway revision 为 [152dea974f09e5dbca4d0da16867df1be9d3395e](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/152dea974f09e5dbca4d0da16867df1be9d3395e)，镜像为 `sha256:bb265785e5d1c446f599712d3a06f9e028bae9d22b4d9e7659a653b688b1de8f`。Ambient 本轮开始的已发布基线为 [793da83ac263aebe26dc989d5e025d8b7c7c01de](https://github.com/BillShiyaoZhang/ambient-agent/tree/793da83ac263aebe26dc989d5e025d8b7c7c01de)，随后在本机实现并验证原生主 Provider 与兼容修复。

本机使用独立合成工作区 `.cache/ambient-public-e2e-20261001/workspace` 和独立 Neo4j，实际适配器为 `Neo4jGraphDatabase`，`RETURN 1` 成功，Neo4j 仅监听 loopback 9747/9768。完整 Runtime 使用受支持的 Linux Docker 拓扑、固定 Docker upstream 和 Unix socket，smoke=1。Runtime socket 为 0660，未授权 uid 65534 的实际访问返回 EACCES；初始控制 App 的真实首帧 smoke 通过，没有模型调用或 App promotion。

本轮仅操作身份已核对的测试节点与自建服务。用户授权为 control-only、不含模型/Coding Agent/技能管理范围，每次最多一小时；在本机网页配置模型使用另一个本机管理上下文。已有 Hermes、原工作区与业务数据不作为测试目标。Linux 隔离 Codex 使用自建状态目录中的登录副本，副本已清理，原本机登录文件保留且内容未被检查或输出。截图和安全元数据留在 ignored observation 目录，不提交账号、邮箱、接入码、凭据、Cookie、launch ticket、模型密钥、原始请求或业务回复正文。

第五轮沿用同账户、`workspace.control`、最多一小时的范围，期限为 17:29:04。补证阶段不复制原登录文件、不调用模型；只有自建 Connector 和浏览器测试连接可被暂停。此前 [2026-10-01 交接验收](remote-workspace-handoff-2026-10-01.md)的 Windows、loopback、合成账户、SQLite 测试适配器和无模型调用结果仍是历史证据，不能代替本轮真实 HTTPS、Neo4j、模型和持久 Run 结果。下文时间均为 **UTC，2026-10-01**；北京时间加八小时。

## 验收矩阵与证明范围

| 编号 | 场景及契约 | 本轮直接结果 | 未直接证明的细项 |
| --- | --- | --- | --- |
| 1 | 真实账户发码、本机配对、同账户领取、核对范围与期限后批准；未批准不能打开 | 第二至第五轮正常授权链路逐次提交，Local Connected、Portal Online；首轮待批准到期保留为预期历史。第四轮模型/App 与第五轮安全补证授权均已撤销 | 没有在生产中额外构造错账户或重放接入码；这些边界由独立本地回归覆盖 |
| 2 | HTTPS launch 消费票据后清理 URL；节点会话具有规定的 Cookie 属性 | 第五轮实际 HTTPS 303→clean root 200；Set-Cookie 的 Secure/HttpOnly/Lax/Path=/、无 Domain、有界 Max-Age 六项直接通过，浏览器实际接受一个未过期、domain/path 匹配的 Cookie。launch 重复 Cache-Control 均为 no-store，重复 Referrer-Policy 均为 no-referrer | 新隔离浏览器的真实 Cookie 证明不等于读取原内置浏览器 Cookie；不以重复策略值误称未设置安全头 |
| 3 | 临时同站模式拒绝节点向门户的跨 origin fetch/form/iframe，账户不被替换 | 原登录内置浏览器的 fetch/form/iframe 拒绝、普通链接 403 与新门户 tab 账户标签保持已观察；另在新隔离浏览器中，四类 GET 的 CDP 实际状态均 403，metadata 自然为 same-site | 隔离浏览器没有原 Portal Cookie，原登录 profile 的攻击 Cookie 请求头未捕获；DOM/标签与独立 CDP 状态分别支持结论，不宣称已捕获原账户携 Cookie 攻击或所有账户替换攻击 |
| 4 | 隔离 Frame、一次性 Runtime 握手与真实 Widget 交互 | 原控制 App Count 0→1，超过 75 秒空闲后仍交互；新生成 App 的公网 Frame 实际显示 READY，Toggle 后 DONE，再次 Toggle 后 READY | 完整模块/vendor/CSP/CORS/握手头没有逐项独立采证。新 App smoke 为 workflow gate 所蕴含通过，直接 smoke 结果未单独捕获；公网渲染与交互是另外的直接证据 |
| 5 | chat/runs/run-live/client-runtime 四条 WSS 同时活动，空闲后可用 | Cloud 快照实际 WS=4、Tunnel=1；空闲超过 nginx 75 秒读超时后控制 Widget 仍响应；真实模型与新 App 单页后来也达到四条 WS | 单次快照和所测交互不代表所有负载或任意长空闲已覆盖 |
| 6 | 本机真实模型、持久 Run、最终消息与 App 代码生成/验证/发布 | Native Model 和 Test tools 通过；公网短 Chat 一次提交取得预期 Receipt 与 succeeded Run。新的受控 App Run 成功，模型快照精确为 Luna，生成、验证和 promotion 均完成 | 第一次 App 在 AlignData 失败保留，具体原因未证实。Native 内部模型请求计数未捕获；合法的一次 workflow autoRepair 增加模型预算，不宣称零额外模型请求 |
| 7 | 只暂停自建 Connector，本机活动 Run 继续；同身份/grant 恢复且无请求重放 | 活动 Run 的 49 秒断线与前缀/数量不重复已通过。第五轮实际生产 WebSocketService `/ws/runs` 另观察 initial=1+auto=5 后 unavailable；恢复 Connector 后 manual=1、constructor=7、connected，send=0 | 新补证计数限定该 `/ws/runs` 服务，不是四条 WS 的每包计数；容器级进程快照不是进程→Run 的独立绑定证据 |
| 8 | 门户撤销与本机撤销使授权失效、关闭转发并清凭据；云通知不可达也能本机撤销 | 两条撤销路径和第五轮本机 Revoke 实际完成，Cloud WS/Tunnel/HTTP=0；独立 API/持久许可均 revoked、凭据/approval 清空、期限仍未来。同一未过期 Cookie 保留并实际发送时，API/sessions/浏览器 document 均 401；连续 132.73 秒直接确认 runner/tunnel 不存在、request/WS=0、轮询产品方法计数不变 | 原内置浏览器重载仍保留旧 DOM，不据此证明导航状态；直接 401 来自另一隔离浏览器。产品方法调用计数不是网络包计数 |
| 9 | control-only 允许聊天/Widget，拒绝管理；公开控制 Host 不暴露账户和 metrics | 既有 Provider 变更 403、管理入口缺席、控制 Host 账户/metrics 404 保留；第五轮携有效 Node Cookie 的本机连接管理 API 实际 403，而 sessions API 为 200 | 没有把每条管理 API 或所有 Host 误判都列为公网直接通过；隔离浏览器不含原 Portal 登录 Cookie，相应源码/本地回归独立保留 |
| 10 | 资源预算拒绝有界，关闭测试连接后回收，原节点仍可用 | 单页四 WS、新增第二页 429、关闭第二页后原控制 App 仍交互；第四及第五轮撤销均资源归零，采样 OOMKilled=false/restart_count=0；两阶段自建服务及五个自建探针资产清理通过 | 第二张完整页面及两个 App Frame 并行容量没有通过；多用户、多节点、满队列、持续模型压力未验收 |

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

模型/App 阶段的第四轮也由门户 Revoke/Confirm 各一次撤销，门户显示已撤销、公网新 App 显示 Authorization inactive、本机显示 Revoked。**15:00:57** Cloud 实际 WS/Tunnel/HTTP=0，采集请求自身 control=1，预约 32,768 字节、queued=0。**15:04:02** 独立只读核验确认 API 与持久状态均 revoked、online=false、credentials cleared、approved=false，原身份及 15:16:48 期限保持且当时未到期，证明主动撤销而非到期失效。该核验没有采内存 runner 或 poll 请求计数。

**15:08:27** 该运行阶段清理核验确认：自建 supervisor 已停止、5 个自建容器已删除、自建网络和 socket volume 不存在、测试 loopback 端口关闭、登录副本不存在；原 Hermes 的容器身份和运行状态保持，原宿主登录文件仍存在，工作区 Graph 数据与 observation 文件保留。清理没有模型调用、没有读取原登录文件内容，没有删除撤销记录、业务数据或其他用户服务。该结果不代替随后第五轮安全补证阶段的清理。

### 第五轮浏览器安全补证

`iab-portal-boundary-verified.json` 于 16:40:44 记录原登录内置浏览器的实际结果：正常 Portal launch 一次，节点 URL 无 ticket；从节点发起 credentials=include fetch 时浏览器拒绝，GET form 目标 Frame 与 Portal iframe 显示 refused to connect，普通顶层链接显示 403 Forbidden。随后正常新 Portal tab 可访问，同 profile 的账户标签前后相同；没有读取密码或 Cookie 值，没有伪造请求头，没有模型调用。实际网络 Cookie 头与 form/iframe 状态码未捕获，不能以 DOM 结果代替网络层证明。

`public-five-retries-verified.json` 于 17:05:59 记录实际前端 `WebSocketService`/`socketReconnect` 的 `/ws/runs` 探针：初始构造 1 次，自动重试 5 次，总构造 6 次后 unavailable；分次观察超过 283 秒仍保持该数量，手动次数为 0。恢复自建 Connector 后手动 Retry 一次，总构造为 7 且 connected；sent_messages=0，没有事件正文或每包计数、没有模型调用，探针随后 Stop。该实验补充真实自动重试与手动恢复，不改变此前活动 App Run 的数量或重放结论。

正常 Cookie 采证 helper 的首次 launch POST 被 Cloud 接受，但本机以 local-now+61 秒检查 60 秒票据时误拒；Cloud 时钟领先本机约 9.755–10.096 秒。17:01:41 的只读 `issuer-clock-and-expiry-diagnostic.json` 核对该测试节点成功 launch 累计 2 次、最新为 16:52:32，session audit 累计 1 次、最新为 16:30:17，最新 launch 后 session=0；没有 retained unused launch row，配置 TTL=60 秒且最新 TTL 已经过。这不等于直接观察到一条 unconsumed 记录，空集合的 all_expired=false 也不是“仍有未过期票据”。诊断没有 HTTP/POST，不重放原请求。helper 的时钟 guard 随后使用 HTTP Date 范围；第二次仍在 real_launch 阶段失败，未逐项捕获响应 flags，后续实际重复 Cache-Control 与固定源码仅支持“单值精确断言可能误拒”的推断。第三次实际 303 直接证实重复 no-store，却在重复 Referrer-Policy 的精确断言失败。第三次未到达浏览器 Cookie 属性检查，不是浏览器拒收 Cookie。严格“所有重复项均为唯一规定策略”的 parser 经纯本地 Red–Green 后应用于 ignored helper，没有修改 Cloud 或产品，也没有放宽为允许其他策略。

最终一次新的明确受控测试记录于 `isolated-browser-security-live-final.jsonl`：**17:23:10** 实际 HTTPS launch 返回 303、Location 为 `/`，clean root 导航 200；Cache-Control 所有项仅为 no-store、Referrer-Policy 所有项仅为 no-referrer，二者实际都有重复，Referrer-Policy 单值精确相等为 false。六项 Set-Cookie 属性直接通过，浏览器实际接受一个 domain/path 匹配、Secure/HttpOnly/Lax 且未过期的 Cookie。**17:23:11** 有效 Node Cookie 实际随本轮资产请求发送，连接管理 API=403、sessions API=200；四种真实浏览器 GET 向门户的 CDP 状态均 403，fetch 为 cors/empty，GET form 与 iframe 为 navigate/iframe，普通链接为 navigate/document，site 均自然为 same-site，未伪造 headers。这个 fresh context 未使用原内置 profile、没有导入门户登录 Cookie，CDP 直接确认这些门户请求没有该 Cookie。

随后本机 Revoke 一次并显示 Revoked。**17:23:38** 同一隔离 context 的 Cookie 保留、值不变且实际发送，Cookie 和授权期限仍未过期；context API、sessions API、浏览器 document 都实际 401，浏览器随后关闭、探针退出 0。原登录内置浏览器另一次重载仍保留旧 probe DOM，没有网络状态证据，不记其视觉 401。**17:24:18** 独立一次本机只读 GET 加持久许可核验全部 15 项通过：revoked、online=false、credentials/approved 清空，预期第五轮身份、期限、control-only scopes、node origin、gateway 保留且匹配，17:29:04.601845 仍未来，原生配置和登录副本均不存在。**17:24:58** Cloud 版本与镜像仍匹配，WS/Tunnel/HTTP=0、collector control=1、预约 32,768 字节、queued=0，RSS 47,816,704、容器 stats 57,640,222，累计 capacity rejection 18、OOMKilled=false、restart_count=0。

**17:26:31** 的只读 reducer 与独立审计重算确认 `final-security-connector-stop-verified.json` 通过：从 **17:24:18.242727 到 17:26:30.973423**，132 个连续样本覆盖 **132.730696 秒**，相邻间隔均不超过三秒。全过程 runner/tunnel 不存在、request/WS=0、revoked/offline/unapproved/no credential，期限仍未来且写错误为零；四个 GET-state 轮询产品方法计数始终为 calls/completed/failed/cancelled=22/22/0/0。这是方法调用计数，不宣称网络包数。首末原有三个 Run 的身份与状态保持，公开计数仍为两个 succeeded、一个 failed；Native 内部模型请求计数仍为 null。reducer 没有新增 GET、Connector 控制或模型调用。

**17:26:58** 的第五轮清理核验确认 supervisor 已停止，五个自建容器、网络、socket volume 均不存在，测试 loopback 端口关闭、登录副本不存在；原 Hermes 身份与运行状态保持，原宿主登录文件存在但内容未读取，工作区 Graph 与观察记录保留。**17:27:30** 又确认五个自建前端探针文件先归档到 ignored cache，再按确切文件删除，fixture source 保留、原 dist 资产未改；只关闭本轮创建的三个内置浏览器 tab，隔离浏览器已在 finally 中关闭并退出 0。整个安全补证与清理阶段没有模型调用。

## 修复与保留的历史失败

本轮发现网页只有 Coding Agent 原生模型选项，尚不能用本机 Codex 登录承担 Ambient default/fast 主模型。先在[Provider 契约](../integrations/llm-providers.md)与[UML](../architecture/uml.md)记录行为，再按 Red–Green 实现 `codex_native` Provider、发现、主模型传输、网页提示和 OpenCode 不支持原生共享 binding 的明确拒绝。主模型传输使用固定 Linux 0.145 app-server，验证托管配置、原始 config layers 的 endpoint 关闭、空 MCP、只读/无外部环境的 thread policy、严格输出与注册工具 JSON Schema，保留 deadline、消息/字节上限和自建进程组清理，不转为 API fallback 或请求重放。

首次原生目录安全预检因固定 0.145 typed 配置投影差异被安全拒绝，account/thread/turn/模型调用均未发生。按官方固定版本 schema 修正后实际发现成功，返回四个模型并包含精确 Luna；此后主 Provider 的真实网页调用另行通过，没有把目录成功当成模型执行。

**第一次 App 仍是失败结果。**一次 Approve plan 后 AlignData 失败，UI 把通用 `llm_capability_unsupported` 映射为“模型不兼容工具调用”。安全只读诊断记录两条消息、17,575 字节 prompt、17,592 字节空 tools envelope、latency 26,370 ms，输入低于 512 KiB，托管模型元数据 guard 通过；源码没有 API-only 或 tool-capability-false 专用门禁，AlignData 未显式传 tools。这排除了已检查的几项解释，没有定位当时具体的原生 item/notification/protocol 拒绝原因。

随后先补固定 reason/category 的安全诊断与泛化网页提示，再修复固定 0.145 普通 `error` 通知兼容：只接受绑定本次 thread/turn 的严格结构，已知 `willRetry=true` 在原 deadline/预算内等待同一 turn，不创建新 RPC；终止错误净化，未知/外来/异常结构仍拒绝。聚焦回归和新受控 App 实际成功证明新行为可用，**不能反推首次 AlignData 失败就是这项静态兼容缺口导致**。失败请求没有自动 Replay。

原生 Windows viewer 的 Count 交互不支持完整 Unix Runtime smoke，因此本轮完整执行切换至 Linux Docker，未关闭 smoke。首次 Docker Backend 在 90 秒内未 HTTP-ready，原因未证实；该阶段自建容器/网络/socket 已清理，之后一次带安全诊断的重试就绪。保留这次历史超时，不把重试成功写成已定位或修复产品故障。

## 最终回归与证据清单

### 两轮 CI 与最小格式修复

[8f4bfdb 的 CI](https://github.com/BillShiyaoZhang/ambient-agent/actions/runs/36893823922) 在后端 Ruff Format Check 失败，后续后端步骤未执行；同轮前端、Widget Runtime、Compose 和 Pages 通过。先在配对文档记录格式门契约，再本机 Red 得到 13 files would be reformatted。锁定 Ruff 0.15.21 仅格式化这 13 个本轮 Python 文件，Green 为完整 `ruff format --check .` 的 161 files already formatted 与 `ruff check .` 通过。全部 13 文件与 8f4bfdb 的 AST 严格等价，独立审查再次确认；Git 内容 diff 为 9 文件，另 4 文件仅本机换行规范化，没有 Native 行为、测试断言或 CI 门变更。

格式修复发布为 c2561a6。[新 CI](https://github.com/BillShiyaoZhang/ambient-agent/actions/runs/36895402825) 的后端全部通过：Linux Ruff format 为 152 files already formatted，完整 Pytest 1002/15/1，60.29 秒；真实 Neo4j compensation/concurrency 专项 31/1，16.10 秒；UML、事件类型和 33 对文档均通过，前端、Compose、Pages 也通过。唯一失败为 Widget Runtime：`frame_browser.test.mjs` 的 controller/CSP 测试 30 秒整体 timeout，以及 `runtime_lifecycle.test.mjs` 的 first frame 等待 30 秒 timeout，合计 34 pass/1 fail/1 cancelled。

widget-runtime 与 CI workflow 在这两提交间没有差异。本机 cached Linux image 在 2 CPU/2 GiB、无网络/模型/auth、源码只读且保留 sandbox/原超时/断言下，原并行 suite 36/36 通过（13.09 秒），串行比较也 36/36 通过（7.94 秒），均无 skip；两个自建容器自然清理。并行较慢是关联，没有复现 CI 失败或证明原因，不据此更改运行逻辑、CI 并行度、超时或断言。已授权的单次 job 重跑被 GitHub 集成权限 403 拒绝，未启动。本记录保留这两次历史 CI 的结果；本次发布的整轮状态以 [main 提交关联 checks](https://github.com/BillShiyaoZhang/ambient-agent/actions/workflows/ci.yml?query=branch%3Amain) 为准，不把本机通过当成远端整轮通过。

| 最终检查 | 结果 |
| --- | --- |
| 完整本机后端 | 格式修复后 995 passed、22 skipped、1 项既有 Starlette warning，164.33 秒；22 项为该本机环境 21 项既有跳过及 1 项 Linux 进程组回归限制；此前 174.79 秒结果保留为原实现冻结历史 |
| 远端后端 CI | c2561a6 的 job 全部通过，Linux 完整 Pytest 1002 passed/15 skipped/1 warning；真实 Neo4j 专项 31 passed/1 warning，不与本机计数混用 |
| 完整前端 | 38 files、292 tests passed |
| 前端 build/lint | 通过 |
| Ruff lint/format、中英文 UML、Run 事件类型 | 格式修复后本机及远端后端 CI 均通过 |
| 中英文文档配对与链接 | 整理后的 `scripts/verify_docs.py` 通过，33 对页面 |
| 差异格式 | 整理后的 `git diff --check` 通过 |
| GitHub 发布与整轮 CI | main 已发布 8f4bfdb 与格式修复 c2561a6；c2561a6 的 Widget 超时未复现，其他 jobs 通过。后续发布状态见对应 main 提交的 checks，不覆盖历史结果 |

本轮回归包含 Native 安全预检、配置/endpoint 限制、registered-tool schema、turn 通知先于 RPC 回应的协议顺序、普通 error 通知、净化诊断、OpenCode binding 拒绝、网页模型提示与本地化。测试使用隔离工作区和注入边界，不依赖真实网络或模型；真实运行证据独立采集。历史 941/953 后端和 290 前端结果不替代最终源码冻结结果。

安全证据保留在 ignored 目录，文件名可定位观察，不含可复用授权秘密：

| 证据组 | 文件与作用 |
| --- | --- |
| 本机观察 | `.cache/ambient-public-e2e-20261001/observations/`：`native-provider-luna-verified.jpg`、`public-native-model-receipt.jpg`、`public-app-align-failed.jpg`、`public-run-connector-offline.jpg`、`public-generated-app-done.jpg`、`public-generated-app-ready-restored.jpg`、`public-final-grant-revoked.jpg`；分别保留模型、成功、失败、离线和实际 Frame/撤销 UI |
| 持久 Run | `owned-run-before-20261001T145220548351Z.json`、`owned-run-offline-20261001T145256613868Z.json`、`owned-run-recovered-20261001T145345041166Z.json`、`owned-run-terminal-20261001T145802214311Z.json`：同 Run 的模型、数量和元数据前缀；不保存正文 |
| 最终撤销与清理 | `final-grant-revocation-verified.json`、`final-owned-cleanup-verified.json`；另保留 `local-revoke-unreachable-verified.json` 的两次不可达撤销核验 |
| Cloud 观察 | `.cache/ambient-public-e2e-20261001-observation/`：`cloud-active-widget-chat-v3.json`、`cloud-second-page-capacity-v3.json`、`cloud-after-native-app-recovery-v3.json`、`cloud-single-generated-app-active-v3.json`、`cloud-final-grant-revoked-v3.json`，记录匹配版本、实际配置、Uvicorn RSS、容器内存及资源计数 |
| 源码与格式回归 | `observations/backend-full-native-protocol-final.log` 为原实现 995/22/1；`backend-full-format-final.log` 为格式修复后 995/22/1；`python-format-ast-equivalence.json` 为 13 文件 AST；`widget-runtime-ci-parallel-repro.log` 与 `widget-runtime-ci-serial-repro.log` 为保持契约的两次 36/36 |
| 第五轮安全补证 | `observations/iab-portal-boundary-verified.json`、`public-five-retries-verified.json` 为原登录浏览器 DOM/标签与 retry；`isolated-browser-security-live-final.jsonl` 为独立浏览器实际 Cookie/CDP/401；`final-security-grant-revocation-verified.json` 为 15 项许可核验；`final-security-connector-stop-verified.json` 为 132.73 秒 runner/poll 与首末 Run 核验；`final-owned-security-cleanup-verified.json`、`final-owned-probe-assets-cleaned.json` 为第五轮服务与探针资产清理；`cloud-final-security-revoked-v3.json` 为该轮资源归零。只读 Clock 历史在 `.cache/ambient-public-e2e-20261002-security/issuer-clock-and-expiry-diagnostic.json` |

独立浏览器已直接证明 Node Cookie 属性/发送/撤销后 401 及门户 CDP 状态；原登录门户 Cookie 的攻击请求头仍未捕获。撤销后 runner/poll 直接采样只证明所测 132.73 秒与产品方法调用计数，Native 内部请求仍为 null；不把两个浏览器的证据、源码/UI、合成 metadata 或 null 计数字段混合扩大证明范围。

## 容量与运营限制

| 阶段（UTC） | WS/Tunnel | 应用预约字节 | Gateway Uvicorn RSS 字节 | 容器 stats 字节 | 累计 capacity rejection |
| --- | --- | --- | --- | --- | --- |
| 12:53:54 原控制 App+聊天单页 | 4/1 | 184,351,704 | 43,180,032 | 42,173,726 | 0 |
| 12:56:51 增开本轮第二页，实际 429 | 6/1 | 259,339,208 | 42,561,536 | 47,783,608 | 12 |
| 14:55:13 新 App 恢复、双 Frame 曾超时 | 3/1 | 146,857,952 | 51,576,832 | 55,218,012 | 18 |
| 14:58:14 仅保留新 App 的活动页 | 4/1 | 184,351,704 | 47,984,640 | 52,974,059 | 18 |
| 15:00:57 最终撤销 | 0/0 | 32,768 | 48,087,040 | 55,469,670 | 18 |
| 17:24:58 第五轮本机撤销 | 0/0 | 32,768 | 47,816,704 | 57,640,222 | 18 |

各采样 HTTP=0、queued=0、control=1 为采集自身，OOMKilled=false、restart_count=0；Uvicorn 为实际 PID7，PID1 init 的 4,096 字节不是 Gateway RSS。应用预约、RSS、容器 stats 和生命周期 HWM 是不同量，不据此推断阶段峰值或多用户安全容量。累计 rejection 从 12 增至 18，不能写恢复阶段零拒绝。

当前部署实测仅支持本轮**一个完整页面、一个活动 App Frame**的功能通过。第二完整页面受到预算拒绝；双 Frame 并行超时后，关闭原窗口与单 Frame 重试恢复相关，但没有证明容量是唯一原因。原控制页在关闭第二页后仍可 Count 2→3，新 App 单 Frame 可 Toggle；这些局部恢复不能升级为双 App、多节点、多用户或持续模型负载通过。运营平台前应另做容量规划、资源预算及并发/背压负载验收，本轮没有调整生产云限额。

发布记录的 wildcard TLS 使用手动 DNS-01，**2026-12-30 到期，尚无自动续期**。本轮可信 TLS/hostname 访问不证明续期、备份恢复、数据库迁移或完整回滚通过。临时同站模式仍有父域普通 Cookie 耗尽的可用性边界，独立可注册域名仍是长期部署方案；不要为补安全负例或恢复演练覆盖真实数据库或修改其他用户授权。
