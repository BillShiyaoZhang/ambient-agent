# 连接云平台并远程打开工作区

## 使用流程

先登录云门户，在“本地工作区”主动生成接入码。码绑定登录账户、有效期五分钟且只能消费一次。然后在本机运行 Ambient Agent，包括前台、后端和 Widget Frame 服务。在应用中心的系统设置中打开“连接云平台”，填入运营方提供的平台网址、连接地址和接入码，选择电脑名称、期限和管理权限。公网地址必须使用 HTTPS；原生本地联调可用 localhost HTTP。

点击“生成连接链接”，到平台使用生成接入码的同一账户领取。回到本机检查显示的账户、权限和期限，点击“确认允许此账户访问”。接入码与云端领取都不会自动允许远程访问；未领取的账户 ID 也不代表已经批准。随后在平台的“本地工作区”页打开在线节点。电脑必须保持开机并联网；云端提供入口和中转，Agent、密钥、工作区和 Run 仍保存在本机，前台和默认 Widget Controller 在访问者浏览器执行。

默认允许工作区操作和任务执行。勾选管理权限才可修改模型、Coding Agent、技能和能力配置。远程页面不能生成新配对或修改连接授权。云端领取不等于本机允许；连接码和打开链接均不可重复使用。

接入码只用于当前提交，每次尝试结束或关闭对话框后清空，不保存到连接记录、浏览器存储、网址或日志。码失效或已使用时，到门户主动生成新码。暂时繁忙时按提示等待后手动重试；如果配对响应丢失，不会自动再创建节点。现有已批准连接升级后继续使用原身份和授权；旧未批准连接需重新接入。

## 撤销与恢复

时间选项也可明确选择“直到撤销”。这会持续允许所选账户在原权限范围内访问，必须由本机或平台主动撤销；本机账户确认页会再次显示这个期限。仅支持该模式的云网关可接入，客户端会先检查能力，旧网关不支持时不会提交配对或消耗接入码，可改选有限期限。已有连接继续使用原到期时间，不能通过升级自动变为长期授权。浏览器会话仍最多一小时，到期后可从门户重新打开已授权节点。

在本机点击“撤销远程访问”可立即停止本机转发，即使云端暂时不可用。在平台撤销节点也会使现有浏览器会话和连接失效。账户删除先在 Gateway 永久撤销该账户节点，失败时保留账户。授权到期必须重新连接；临时断网会显示离线，恢复后 Connector 自动重连，传输层不自动重放写操作。

浏览器入口会话最多一小时且不超过本机授权期限。门户退出登录与已打开节点的短期会话分别管理；需立即停止远程访问时，使用撤销按钮。中转服务可以读取转发中的正文，当前实现不把正文持久化。原聊天 RPC 的政策签名不覆盖这个独立 HTTP/WebSocket 通道。

## 本地开发与建议包

Ambient 无需任何 Agent Collaboration submodule。默认原生 Connector 使用固定 loopback 服务端口 5173、8000、8001；现有 Compose 配置选择固定 docker 服务地址。连接仅由显式配对启用，未配置时不联网。

当前客户端采用云端 [2026-10-01 交接契约](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/blob/d6dd823f1b379440616a2dc2e866ce9d9bac7729/docs/developers/AMBIENT_WORKSPACE_HANDOFF_2026-10-01.md)。使用 `git clone --branch codex/ambient-workspace-review-20261001 --recurse-submodules https://github.com/BillShiyaoZhang/agent-collaboration-deploy.git cloud-workspace-review` 获取对应云分支；本次固定 Deploy `d6dd823f1b379440616a2dc2e866ce9d9bac7729`、Web `fa55096cc66f88f17f1b6191a5bc41d046cd08e8`。原有云项目工作目录不受影响，Ambient 无需 submodule。仓库根 `proposals/agent-collaboration-deploy` 保留旧首版建议的 patch/bundle 历史快照，不能代替当前需要接入码的云端版本。

在独立云分支检出的 `workspace-gateway` 目录按其 README 安装隔离环境依赖。以下是一组完整的原生开发配置：门户使用 `http://localhost:3000`，Gateway 使用 `http://localhost:8090`，Ambient 仍使用原生固定端口。Gateway launcher 固定监听 `0.0.0.0:8090`；本地访问使用 localhost，设备只通过公开控制地址接入。

| 变量 | Gateway | Web |
| --- | --- | --- |
| `WORKSPACE_GATEWAY_SECRET` | 独立随机服务密钥，至少 32 字符 | 同一服务密钥，只供 BFF |
| `WORKSPACE_GATEWAY_DATABASE` | 新的隔离 SQLite 绝对路径 | 不使用此变量；保留独立门户数据库配置 |
| `WORKSPACE_GATEWAY_DOMAIN` | `localhost:8090` | `localhost:8090` |
| `WORKSPACE_GATEWAY_SCHEME` | `http` | 不使用此变量 |
| `WORKSPACE_GATEWAY_PUBLIC_URL` | `http://localhost:8090` | `http://localhost:8090`，返回给用户的公开控制地址 |
| `WORKSPACE_GATEWAY_CONTROL_HOST` | `localhost:8090` | 不使用此变量 |
| `WORKSPACE_GATEWAY_SERVICE_HOST` | `127.0.0.1:8090`，允许此内部 BFF Host 的账户/指标接口 | 不使用此变量 |
| `WORKSPACE_GATEWAY_PORTAL_ORIGIN` | `http://localhost:3000` | 不使用此变量 |
| `WORKSPACE_GATEWAY_URL` | 不使用此变量 | `http://127.0.0.1:8090`，仅 BFF 内部请求 |
| `NEXTAUTH_URL` / `NEXTAUTH_SECRET` | 不使用；门户 origin 用上行变量 | `http://localhost:3000` / 独立私有密钥 |

从 Gateway 目录使用隔离环境运行其 `python -m workspace_gateway.launcher`，采用与应用预算匹配的传输队列和并发限制。使用该配置时，Ambient 的平台网址填 `http://localhost:3000`，连接地址填 `http://localhost:8090`；不能把内部 `127.0.0.1` URL 当作公开 Connector 地址。账户接口只允许配置的控制或服务 Host 且要求服务 Bearer，设备与健康接口只允许公开控制 Host。Compose 内部 BFF 使用 `workspace-gateway:8090`，须采用 overlay 的对应 SERVICE_HOST。数据库、设备凭据、服务密钥和联调账号均不属于建议包。

生产接入使用 HTTPS 的精确 CONTROL_HOST / PUBLIC_URL、独立节点 wildcard 域名、TLS 和内部账户 API 隔离；门户与工作区必须属于不同可注册域名，同一域的不同子域不满足隔离。例如门户 `portal.example.com`、控制 `connect.example-workspace.com`、节点 `*.nodes.example-workspace.com`；须替换示例域名并通过云分支的 PSL、证书和入口验证。另需脱敏入口日志、服务政策、全局注册配额、状态库维护和真实负载 RSS 验收。当前支持单 Gateway 实例；公网 TLS/DNS、入口拒绝伪造 Host/代理头、账户 API 不公开及多 WS 实际负载均须在独立环境实测。

详细契约见 [架构设计](../architecture/remote-workspace.md)，本次交接实测见 [2026-10-01 交接验收](../verification/remote-workspace-handoff-2026-10-01.md)，原首版证据保留为 [历史本地验收记录](../verification/remote-workspace.md)。本次工作未部署至服务器。
