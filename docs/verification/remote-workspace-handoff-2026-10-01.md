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
| 真实节点页面 UI | 当前内置浏览器拒绝 `<node>.localhost`，返回 `ERR_BLOCKED_BY_CLIENT`；Chrome 连接不可用。门户打开操作已产生一次 launch 审计，但导航与手动新 tab 均受阻；不能声称远程 Widget/聊天页已通过 |

跨仓工具覆盖真实 Tunnel、双向文本/二进制 WS 和子协议、单次写及超时不重放、在途撤销、接入码过期/重放、错误账户不消耗码、配额不消费码、429 冷却、到期/删除/无效设备终止，以及旧 paired 身份与 origin 双端重启恢复。第六组在真实 Gateway Tunnel 容量为一时验证第二节点保持凭据，第一节点释放后第二节点按退避恢复原身份。

![本机批准后显示账户、范围与期限](remote-workspace-handoff/local-approved.jpg)

![撤销后接入码为空且可主动重新接入](remote-workspace-handoff/local-revoked.jpg)

## 云侧容量与发布门禁

生产 overlay 默认预约预算 128 MiB 是实际兼容门槛：当前 Resources 实现允许一个 Tunnel 加两个 browser WS（109,331,432 字节），第三个 WS 返回 429。Ambient 聊天页有 command、run-live、durable Run 至少三个 WS，Widget 还增加连接。因此不能声称默认生产配置已通过完整 Ambient 验收。256 MiB 预约预算允许一个 Tunnel、四个 WS 与两个 HTTP（209,746,904 字节）；这只是资源预约探针，不是实测 RSS 或安全生产容量。

本次继续完成客户端契约适配；云侧合并/上线前应按完整界面及实际 RSS 决定预算、容器上限与初期规模。真实 DNS、可信 wildcard TLS/续期、可注册域隔离、生产 nginx/反代、共享主机容量、在线备份恢复和回滚仍须发布前验证；本地联调不能冒充已上线。

另一个已复现的兼容问题是 Gateway 在 Tunnel 接受前将容量拒绝统一关闭为 1008，网络握手呈 HTTP 403。Ambient 已通过设备 state 查询区分暂时容量不足和真正失效，避免丢失有效身份。建议云侧后续明确返回 429/Retry-After 的握手拒绝；此次没有修改云源代码。

本机 Windows Gateway 进程连续采样的空闲 WorkingSet 为 53,104,640 字节，采样最大值 58,052,608，进程 PeakWorkingSet 58,056,704，PrivateMemory 最大值 43,995,136。这个阶段仅有账户控制、Connector 和受阻的节点导航，未出现浏览器 WS；撤销后 Tunnel 为零。上述实测不能替代远程完整界面、多节点或 384 MiB 生产容器的容量验收。
