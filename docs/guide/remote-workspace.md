# 连接云平台并远程打开工作区

## 使用流程

本机运行 Ambient Agent，包括前台、后端和 Widget Frame 服务。在应用中心的系统设置中打开“连接云平台”，填入运营方提供的平台网址和连接地址，选择电脑名称、期限和管理权限。公网地址必须使用 HTTPS；原生本地联调可用 localhost HTTP。

点击“生成连接链接”，到平台登录并领取。回到本机检查显示的账户、权限和期限，点击“确认允许此账户访问”。随后在平台的“本地工作区”页打开在线节点。电脑必须保持开机并联网；云端提供入口和中转，Agent、密钥、工作区和 Run 仍保存在本机，前台和默认 Widget Controller 在访问者浏览器执行。

默认允许工作区操作和任务执行。勾选管理权限才可修改模型、Coding Agent、技能和能力配置。远程页面不能生成新配对或修改连接授权。云端领取不等于本机允许；连接码和打开链接均不可重复使用。

## 撤销与恢复

在本机点击“撤销远程访问”可立即停止本机转发，即使云端暂时不可用。在平台撤销节点也会使现有浏览器会话和连接失效。账户删除先在 Gateway 永久撤销该账户节点，失败时保留账户。授权到期必须重新连接；临时断网会显示离线，恢复后 Connector 自动重连，传输层不自动重放写操作。

浏览器入口会话最多一小时且不超过本机授权期限。门户退出登录与已打开节点的短期会话分别管理；需立即停止远程访问时，使用撤销按钮。中转服务可以读取转发中的正文，当前实现不把正文持久化。原聊天 RPC 的政策签名不覆盖这个独立 HTTP/WebSocket 通道。

## 本地开发与建议包

Ambient 无需任何 Agent Collaboration submodule。默认原生 Connector 使用固定 loopback 服务端口 5173、8000、8001；现有 Compose 配置选择固定 docker 服务地址。连接仅由显式配对启用，未配置时不联网。

云端建议实现位于独立副本 `.cache/agent-collaboration-proposal`，分支 `codex/ambient-workspace-proposal`；原有 `agent-collaboration-deploy` 工作目录不受影响。建议分支已发布到 GitHub：[Deploy](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/codex/ambient-workspace-proposal)、[Web](https://github.com/BillShiyaoZhang/agent-collaboration-web/tree/codex/ambient-workspace-proposal)，尚未合入云项目 `main`。可用 `git clone --branch codex/ambient-workspace-proposal --recurse-submodules https://github.com/BillShiyaoZhang/agent-collaboration-deploy.git cloud-proposal-review` 获取。可审阅的 patch 与 Git bundle 保存在仓库根 `proposals/agent-collaboration-deploy`，其中 README 也保留快照恢复流程。Web 建议复用原账户，Gateway 建议新增独立服务，Platform 和 SDK 不变。

在独立建议检出的 `workspace-gateway` 目录按其 README 安装依赖。本地运行 Gateway 设置 `WORKSPACE_GATEWAY_SECRET`（独立服务密钥）、`WORKSPACE_GATEWAY_DATABASE`（隔离 SQLite 路径）、`WORKSPACE_GATEWAY_DOMAIN=localhost:8788`、`WORKSPACE_GATEWAY_SCHEME=http`，以 `uvicorn workspace_gateway.app:create_app --factory --host 127.0.0.1 --port 8788 --no-proxy-headers --no-access-log` 启动。Web 设置相同服务密钥、`WORKSPACE_GATEWAY_URL=http://127.0.0.1:8788`、独立数据库和 NextAuth URL/secret。数据库、设备凭据、服务密钥和联调账号均不属于建议包。

生产接入需要独立 wildcard 域名、TLS、内部账户 API 隔离、脱敏入口日志、服务政策、全局注册配额和状态库维护。当前支持单 Gateway 实例，详细契约见 [架构设计](../architecture/remote-workspace.md)，实际验证见 [本地验收记录](../verification/remote-workspace.md)。此建议未部署至服务器。
