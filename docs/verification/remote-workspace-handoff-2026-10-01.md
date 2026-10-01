# 云交接评审与 Ambient 接入码适配

## 评审结论与固定版本

云交接符合原意：Agent、App、文件、Provider 密钥与 Run 仍由本机持有和执行；云端只提供账户入口、授权元数据及有界中转。五分钟账户接入码补充匿名准入防护，不代替领取和本机明确确认，也不要求 Ambient submodule。失败、断线和回应丢失不能变成再次执行请求。

本次固定 [Deploy d6dd823](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/tree/d6dd823f1b379440616a2dc2e866ce9d9bac7729)、[Web fa55096](https://github.com/BillShiyaoZhang/agent-collaboration-web/tree/fa55096cc66f88f17f1b6191a5bc41d046cd08e8)，依据[交接文档](https://github.com/BillShiyaoZhang/agent-collaboration-deploy/blob/d6dd823f1b379440616a2dc2e866ce9d9bac7729/docs/developers/AMBIENT_WORKSPACE_HANDOFF_2026-10-01.md)核对源码。Gateway 独立重跑 48/48；Web 账户、接入码、Origin、撤销与删除相关回归 78/78。旧 paired 迁移保留；pending/claimed 作废；账户 tombstone 永久保留。未修改云代码、分支或原云项目检出。

## 客户端验收规范

先更新[架构](../architecture/remote-workspace.md)与[使用流程](../guide/remote-workspace.md)，再以失败测试驱动实现。配对必填短期码，只本次发送，不进磁盘、状态、浏览器存储、网址、日志或异常。422 验证错误不能回显输入；409/422/429/410 映射为有界安全消息，429 保留合法 Retry-After。UI 每次尝试和关闭均清码，冷却期间不立即重复提交，本机撤销仍立即生效。

待批准的成功查询保持两秒；连续失败指数退避并加抖动、遵守 Retry-After。无效设备、到期、撤销或账户删除终止许可、HTTP/WS 转发与云轮询；旧已批准身份和数据保留。pending 带账户 ID 不代表领取或批准。

可复用跨仓工具 `scripts/verify_remote_workspace_handoff.py --gateway-root <workspace-gateway目录>` 使用临时 SQLite、合成账户与真实 loopback Gateway 网络，Ambient Connector 的本机 upstream 为测试 fixture。验收 enrollment、错误账户不消耗码、领取、本机批准、真实 Tunnel、单次写、双向 WS/子协议、撤销/到期/删除、迁移与预算。它不代替真实 Ambient 界面、模型或公网验收。

## 实际本地验收

真实界面与网络验收使用临时 SQLite 工作区和合成账户，不调用真实模型，不操作原用户数据；监听进程只清理本次启动的所有者。Ambient 代码、云 Gateway 和门户均在这台 Windows 电脑运行，没有部署至公网。

| 检查 | 实际结果 |
| --- | --- |
| 后端针对性 Red/Green | 接入契约先确认失败；最终 68 项通过，含迟到响应和 Tunnel 容量恢复回归 |
| 后端完整回归 | 854 通过、21 个既有跳过；一个既有 Starlette/httpx 警告 |
| 前端针对性 Red/Green | 旧实现 14 失败、7 通过；最终 21 项通过 |
| 前端完整回归 | `npm --prefix frontend test -- --maxWorkers=2`：37 个文件、288 项通过；首轮高并发超时及后续空 DOM 失败未通过，不改变超时或断言后限制并发复跑 |
| 构建与静态检查 | Frontend build/lint、Python Ruff、UML 契约及 32 对中英文文档校验通过 |
| 跨仓网络验收 | `uv run python scripts/verify_remote_workspace_handoff.py --gateway-root <workspace-gateway目录>`：6 组 PASS；测试服务与临时数据库已清理 |
| 真实本机与门户 UI | 明确生成接入码、配对、同账户领取、仍等待本机确认、核对范围和一小时期限、批准后在线；管理权限默认关闭 |
| 后端重启 | 原节点、grant 和 origin 保持不变，新后端恢复 paired/online；本机 Widget 重取票据后 Count 0→1 |
| 真实本机撤销 UI | 本机显示 revoked，接入码为空；门户当前连接消失，历史页显示已撤销且打开按钮禁用；Gateway Tunnel 计数归零 |
| 真实节点页面 UI | 经用户明确授权，使用隔离临时 profile 的 headless Playwright：7 项检查、5 个阶段 PASS；同一节点远程 Widget 的 Count 0→1，聊天页显示已连接，合成会话仍为空且未发送消息或调用 LLM |
| 浏览器授权与 Frame 隔离 | 单次 launch 消费后 URL 不含 ticket；节点 cookie 为 host-only、HttpOnly、SameSite=Lax。Frame 来自同节点 `/_ambient/frame.html`，保留 `allow-scripts` 且没有 `allow-same-origin`；远程管理状态请求返回 403 |
| 真实远程 WS 与撤销 | 同时存在三条聊天 WS 与一条 Widget operator WS，Gateway 显示一个 Tunnel、四条 browser WS；本机撤销关闭全部既有连接，旧 cookie 的 HTTP 与页面重新加载均返回 401，Tunnel/WS 计数归零 |
| 本轮清理 | 浏览器/context 正常关闭，测试 Node 进程退出为 0；2026-10-01 09:54:43 UTC 核验本轮所有者的 12 个进程均已退出，3334/8794/8000/8001/5174 无监听，两份 runtime 状态均为 stopped |

本轮真实浏览器的[有界验收结果](remote-workspace-handoff/remote-browser-acceptance.json)保留检查、阶段、路径、计数及固定版本等元数据，不保留 ticket、cookie 值、请求头或 body。四条连接分别为 `/ws/chat`、`/ws/run-live`、`/ws/runs` 和 `/ws/widgets/remote-fixture/client-runtime`；纯 React Widget 也保持自己的 operator WS，Graph 订阅不是建立该连接的前提。浏览器未发出被阻断的外部 HTTP 请求；`/favicon.svg` 的非功能资源请求返回 403，不能据此声称所有资源都为 200。

较早的内置浏览器对 `<node>.localhost` 返回 `ERR_BLOCKED_BY_CLIENT`，Chrome 连接不可用；门户 launch 审计已产生，但节点导航受阻。这是本轮 Playwright 授权前的历史诊断。隔离 fixture 还曾因可信 Origin 配置遗漏返回 403，仅修正测试配置并重启后，以全新的 enrollment、配对和 launch 完成上述验收；未重放写请求或票据，未为此修改产品代码。

跨仓工具覆盖真实 Tunnel、双向文本/二进制 WS 和子协议、单次写及超时不重放、在途撤销、接入码过期/重放、错误账户不消耗码、配额不消费码、429 冷却、到期/删除/无效设备终止，以及旧 paired 身份与 origin 双端重启恢复。第六组在真实 Gateway Tunnel 容量为一时验证第二节点保持凭据，第一节点释放后第二节点按退避恢复原身份。

![本机批准后显示账户、范围与期限](remote-workspace-handoff/local-approved.jpg)

![撤销后接入码为空且可主动重新接入](remote-workspace-handoff/local-revoked.jpg)

![同一远程节点的 Widget Count 1 与已连接的空聊天页](remote-workspace-handoff/remote-widget-chat.jpg)

## 云侧容量与发布门禁

生产 overlay 默认预约预算 128 MiB 是实际兼容门槛：当前 Resources 实现允许一个 Tunnel 加两个 browser WS（109,331,432 字节），第三个 WS 返回 429。Ambient 聊天页有 command、run-live、durable Run 至少三个 WS，Widget 还增加连接。因此不能声称默认生产配置已通过完整 Ambient 验收。独立预约探针在 256 MiB 下允许一个 Tunnel、四个 WS 与两个 HTTP（209,746,904 字节）；本轮真实 UI 也显式采用 256 MiB（268,435,456 字节），没有修改 128 MiB 生产默认值。预约计数不等于实测进程内存，也不能作为安全生产容量。

本次已完成客户端契约适配与本地验收；云侧合并/上线前应按完整界面及实际 RSS 决定预算、容器上限与初期规模。真实 DNS、可信 wildcard TLS/续期、可注册域隔离、生产 nginx/反代、共享主机容量、在线备份恢复和回滚仍须发布前验证；本地联调不能冒充已上线。

另一个已复现的兼容问题是 Gateway 在 Tunnel 接受前将容量拒绝统一关闭为 1008，网络握手呈 HTTP 403。Ambient 已通过设备 state 查询区分暂时容量不足和真正失效，避免丢失有效身份。建议云侧后续明确返回 429/Retry-After 的握手拒绝；此次没有修改云源代码。

新一轮 Windows Gateway 采样按精确 UTC 时间将内存与无秘密 metrics 联表，每秒约一次。初始空闲 WorkingSet 为 53,420,032 字节。2026-10-01 09:52:22.872951–09:52:29.991531 UTC 的稳定聊天与 Widget 窗口有 8 个样本，每个均为一个 Tunnel、四条 browser WS 和一个采样 control 请求：WorkingSet 最小/中位/最大为 58,245,120 / 58,667,008 / 58,900,480 字节，PrivateMemory 最大为 45,215,744 字节。涵盖页面就绪、点击和聊天的完整四 WS 窗口共有 13 个样本（09:52:17.796253–09:52:29.991531 UTC），WorkingSet 最大 60,977,152，PrivateMemory 最大 46,710,784 字节；窗口观察到的进程生命周期 PeakWorkingSet 为 60,985,344，并非阶段重置后的峰值。

上述稳定窗口 HTTP 瞬时计数为 0 或 2，预约最小/最大为 184,351,704 / 209,779,672 字节；单独的页面就绪快照有四个 HTTP，预约为 235,207,640 字节，包含采样 control 的 32,768 字节。queued、capacity rejection 与 backpressure close 均为零。撤销后 128 个同步样本均为 Tunnel/WS/HTTP 零，剩余 32,768 字节预约来自 metrics 采样自身。原始 `gateway-memory.jsonl`、`gateway-metrics.jsonl` 及 `gateway-four-ws-summary.json` 留在本轮隔离缓存，`cleanup-result.json` 记录所有者核验与实际停止结果。

较早只包含账户控制、Connector 和受阻节点导航的历史采样没有 browser WS：空闲 WorkingSet 53,104,640 字节，采样最大 58,052,608，进程 PeakWorkingSet 58,056,704，PrivateMemory 最大 43,995,136；它不计入上述新一轮四 WS 窗口。两轮均为本机 Windows loopback 合成工作负载，未填满队列；WorkingSet/PrivateMemory 不是 Linux 容器 RSS，短时四 WS 通过不能替代多节点、共享主机或 384 MiB 生产容器的容量验收。
