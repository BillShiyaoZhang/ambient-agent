# 云入口与本地工作区

## 首版边界

Ambient 仍是用户独占的本地工作区，负责 Agent、App、Graph、Provider 密钥与持久 Run。Ambient 不依赖 Agent Collaboration 子模块。云侧改动在独立建议分支与隔离检出中开发，供原项目选择是否合并；其 Web 复用现有账户登录，新增工作区连接入口，部署仓库新增独立 Workspace Gateway。平台不复制整个工作区，也不重新调度 Run。网页和默认 Widget Controller 在访问者浏览器运行。

Connector 只在用户显式连接后出站联网。用户在云端领取一次性码后，还须在 Ambient 本机确认账户、范围和到期时间。普通聊天配对不包含工作区权限。首版一个节点只有一个账户；账户可连接多个节点。`workspace.control` 允许本人的工作区读写与执行，`workspace.manage` 另允许 Provider、Coding Agent、Skill 和 capability 管理。本机连接管理接口始终禁止远程调用。

## 身份与接口

Gateway 使用独立 SQLite 状态库，保存节点、一次性码摘要、设备凭据摘要、账户绑定、授权期限、短期浏览器会话与审计元数据，不保存请求或响应正文。设备凭据保存在本机私有工作区，不返回浏览器。Web 后端使用独立 `WORKSPACE_GATEWAY_SECRET` 访问账户接口，账户 ID 从现有登录会话取得；浏览器不可指定他人账户。

| Gateway 接口 | 调用者与约束 |
| --- | --- |
| `POST /v1/connector/pairings` | 本机注册；必填 `enrollment_token`，以及 `name`、`scopes`、`expires_in`；返回 `node_id`、`connector_token`、`pairing_code`、`pairing_expires_at`、`expires_at`、`workspace_origin` |
| `GET /v1/connector/state` | `Authorization: Bearer <connector_token>`；返回状态、领取账户、范围、授权期限和 `grant_id` |
| `POST /v1/connector/approve` | 同上；`account_id`、`grant_id` 必须匹配当前领取，只有被云账户领取后可由本机确认，领取后账户不可替换 |
| `POST /v1/connector/revoke` | 同上；撤销并关闭设备和浏览器连接 |
| `WS /v1/connector/tunnel` | 已确认、未过期设备；一个节点一个有效连接 |
| `POST /v1/accounts/{account_id}/enrollments` | Web 服务凭据；`label` 从登录会话派生；用户主动请求生成账户绑定、五分钟有效的一次性 `enrollment_token`，返回码与 `expires_at` |
| `POST /v1/accounts/{account_id}/pairings/claim` | Web 服务凭据；`code`、`label`；仅绑定账户可原子领取，错账户请求不消耗码 |
| `GET /v1/accounts/{account_id}/nodes` | Web 服务凭据；仅此账户节点；返回 `{nodes,next_cursor}`，支持 `limit`、`view=active\|history` 与账户/视图绑定游标；区分 pending、claimed、paired、revoked 与 online |
| `POST /v1/accounts/{account_id}/nodes/{node_id}/launch` | Web 服务凭据且已授权、在线；返回一次性 `url` |
| `DELETE /v1/accounts/{account_id}/nodes/{node_id}` | Web 服务凭据；撤销此账户节点 |
| `DELETE /v1/accounts/{account_id}` | Web 服务凭据；删除账户前永久标记并撤销全部节点，拒绝并发领取/确认/launch |

本机接口位于 `/api/remote-workspace`：`GET /status`，`POST /pair`（必填 `enrollment_token`，以及 `gateway_url`、`portal_url`、`name`、`scopes`、`expires_in`），`POST /approve`（`account_id`、`grant_id`），`POST /revoke`。接入码来自已登录门户的主动发码操作；Ambient 不访问账户服务接口或持有其服务密钥。pending 节点已绑定可信账户 ID，但仅 `status=claimed` 才显示领取身份并允许本机确认。只接受可信本机 peer；浏览器变更还必须满足现有 Origin 白名单。配置不进入 Graph；状态响应不含长期凭据。状态的到期字段统一为 `expires_at`。HTTPS 是公网默认，HTTP 仅允许显式 localhost/loopback 开发地址。凭据文件损坏时报告 invalid 且不联网，本机界面允许显式重新配对；网络失败保留已授权本地状态；本机撤销先断开并清除本地许可，再通知云端。

## 通道契约

Connector 的握手收到 `{type:"hello",node_id,grant_id,account_id,scopes,workspace_origin}`，必须与本机已确认授权完全匹配。Gateway 为每个请求附带同一身份上下文；Connector 逐次复核有效期、身份、范围、目标与路径。客户端不能通过代理头声明身份。设备密钥、Cookie、Authorization、Host 和 hop-by-hop 头不透传到 Ambient；Connector 设置固定可信本机 Origin，不扩大 Backend Origin 或 peer 白名单。

消息是有界 JSON：HTTP 正文和二进制 WS 帧使用 base64。`http.request` 包含 `id`、身份上下文、`service`（frontend/backend/frame）、`method`、`path`（含 query）、`headers`（二元数组）、`body`。`http.response` 包含 `id`、`status`、`headers`、`body`。`ws.open` 包含相同上下文及 `subprotocols`；响应 `ws.accept` 包含选定 `subprotocol`。双向 `ws.data` 包含 `id`、`kind`（text/bytes）和 `data`；`ws.close` 包含 `id`、`code`、`reason`。Connector 定时 `ping`，Gateway `pong`；撤销发送 `revoked` 并关闭所有在途连接。

固定、公开且不包含工作区正文的 Frame 静态资源以 gzip 原样传输，仅这些响应允许 `Content-Encoding: gzip`；2 MiB 限额计压缩后 wire 大小，普通 API/前台仍按解压后的正文计限额。

Connector 默认选择固定 `127.0.0.1:5173/8000/8001` 服务。`AMBIENT_REMOTE_UPSTREAM_MODE=docker` 在进程启动时选择固定 Compose 内部 `frontend:5173`、`127.0.0.1:8000`、`widget-frame:8001`；无其他模式，浏览器/云消息不能传入主机或 URL。Compose 明确设置 docker 模式，原生启动默认 loopback。

HTTP 上限 2 MiB，单 WS 帧上限 256 KiB；节点 HTTP 并发上限 16，浏览器 WS 上限 16，请求超时 30 秒，浏览器会话最多 1 小时且不超过本机授权期限。异常断连清理在途任务并返回离线错误，不自动重放写请求。本地 Run 继续按原状态机运行；重连由 Ambient 原有持久事件和快照恢复。

## 网页与 Widget

每节点独立 origin，开发地址为 `<node_id>.localhost:<gateway-port>`，生产配置 wildcard 域名和 TLS。生产门户与工作区/Connector 控制入口必须使用不同的可注册域名（校验包括离线 PSL 的 PRIVATE 规则），工作区与控制入口使用同一可注册域名；仅使用不同子域不足以隔离门户与任意工作区 JavaScript。一次性 launch ticket 原子消费后换为 host-only、HttpOnly、SameSite=Lax、`Path=/` Cookie：HTTPS 使用 Secure 的 `__Host-ambient_workspace`，本地 HTTP 使用 `ambient_workspace`；均不设置 Domain，随后立即跳转无 ticket URL。请求必须匹配节点 Host；账户路由不在工作区 Host 下暴露。Cookie 不与云入口共享，Connector 不读取或透传节点 Cookie。

`/api/*`、`/ws/*` 路由 Backend，其他已允许的静态资源路由 Frontend。Gateway 仅在 HTML 中注入非秘密 `window.__AMBIENT_REMOTE__={apiBaseUrl:"/",nodeId}`，前端采用同源 API。Connector 将 client-runtime-ticket 响应的 `frame_url` 改为当前节点 origin 的 `/_ambient/frame.html`。该地址与 Frame 固定根资源 `/frame_shell.css`、`/frame_shell.mjs`、`/controller_facade.mjs`、`/presentation_context.mjs`、`/vendor/*` 单独路由 Frame，不包含工作区内容，可无凭据加载以保持 opaque-origin module CORS。保留 sandbox、CSP 与 backend ticket 校验，不扩大 Widget 权限。

## 本地验收

先写失败测试再实现。验收包括：云登录与跨账户隔离；一次性码/票据重放拒绝；云领取后本机确认；完整 Ambient 页面、真实 HTTP 与 WS 双向收发；Widget 固定壳/模块/一次性票据握手；管理范围拒绝；未知目标、路径穿越、代理头和超限拒绝；到期与两端撤销使已有连接失效；离线不重派 Run；重启保留身份与授权。浏览器验收使用独立测试账户和临时工作区，不调用真实模型或修改用户原有数据。记录实际环境，不能把本地验收声称为生产可用。

首版 Gateway 是可测试的有界出站通道实现；部署配置、配额与失效机制明确后，可替换数据通道实现。跨账户共享工作区、跨节点数据合并、全量云副本、P2P 与生产多副本路由均不在首版范围。

## 2026-10-01 云端交接适配

固定 Deploy `d6dd823`、Web `fa55096` 的交接保留上述本机执行与明确批准边界。用户先在登录门户主动生成绑定当前账户、有效五分钟的一次性 `enrollment_token`，再在 Ambient 本机配对中提交；公开配对请求必须包含接入码。码只在这次请求中发送，不落 `node.json`、公开状态、URL、日志、异常或浏览器持久存储。配对响应仅保存既有设备字段白名单；云端误带的接入码和未知字段不保存。缺码或非法本机输入返回 422，验证错误不返回原输入。

本机 API 使用静态安全错误描述映射 Gateway 的 409（码失效、已用或状态冲突）、422（输入非法）、429（额度或暂时繁忙）、410（账户或资源终止），不转发 Gateway 正文。429 的合法 `Retry-After`（秒数或未来 HTTP 日期）转为 1 至 86400 秒的有界整数响应头，同时在公开状态返回 `retry_after` 秒数；非法或超过一天的值忽略。限流窗口内设备控制操作不提前发送，用户重新提交配对也必须等待本机冷却结束。本机撤销仍立即生效；冷却未结束时不发送云通知。

pending/claimed 成功状态刷新间隔仍为两秒；连续网络、服务或限流失败指数退避（基准三秒、最多六十秒）并加入正向随机抖动，合法 Retry-After 可延长等待。成功状态刷新重置退避；HTTP 或 Tunnel 断线不重放写操作、Run 创建或配对 POST。配对响应丢失时不自动创建第二个节点，用户须等待旧 pending 过期后主动获取新接入码。

设备 401/403、账户 410、云端 revoked/expired、本机授权期限以及 pending 领取码到期都会清除本机凭据和许可、关闭在途 HTTP/WS 与 Tunnel 并停止轮询。终止显示 revoked 或 expired，允许用户显式重新配对；临时网络或 429 保留已确认身份且不扩大权限。旧 paired 状态无需接入码即可按原身份、凭据、范围、期限和 origin 恢复；云迁移作废的未批准节点需要重新接入。

上述 401/403 终止语义针对设备 HTTP 控制接口。Tunnel 在接受连接前因容量不足关闭时也可能表现为 HTTP 403，因此 Connector 先查询设备 state：仍是相同 paired 授权则保留身份并指数退避；查询暂时失败也不撤销。只有 state 确认 revoked/expired，设备 HTTP 返回 401/403/410，或本机到期才终止；Tunnel 401/410 可直接终止。state 查询成功不能重置连续 Tunnel 握手失败的退避，仅有效 hello 或正常未批准刷新重置。验证与重连只发 GET，不重放任何 POST。

并发状态查询以请求开始时的本机状态版本为快照。用户批准、撤销或重新配对替换状态后，迟到的 claimed/paired 响应不得覆盖新状态；旧请求的 401/403/410 也不得终止替换后的许可。查询不能持锁阻止本机撤销；版本复核在响应应用或终止前完成。
