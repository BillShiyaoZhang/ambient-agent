# 权限、执行边界与审计

Ambient Agent 使用可组合的多层 policy，而不是一个全局“已授权”布尔值。Widget grants、模型本地 tool、Capability action、MCP/remote Agent runtime 和 Coding Agent 各自回答不同问题，外层批准不能放宽内层约束。

## 1. Widget Capability Grants

Widget 权限在 schema 对齐 interaction 中与数据 schema 一起批准，并以 Manifest V2 精确 grants 固化。`CapabilityAuthorizer` 默认拒绝，按 App、类目、operation 与 resource 逐次检查 Graph、Network、File 和 installed-capability adapter。

Manifest revision 或 grants digest 改变后，旧 SDK snapshot 不能继续调用。隐藏前端方法不是授权；后端只信任当前持久 Manifest。完整模型见 [Widget 能力安全架构](/architecture/capability-security.md)。

## 2. 模型本地工具：Tool Gateway

模型请求的 Python tool 由 `ToolGateway` 执行。每个 `ToolSpec` 声明强类型 input/output schema、effect、required scopes、approval policy、timeout、输出上限、幂等要求和敏感字段。

Gateway 拒绝未注册 tool、未知参数、scope 不足、缺少审批和缺少幂等键，并脱敏 tool events。Converse 只获得从 [Agent 系统能力目录](/agent/system-capabilities.md) 投影的 read tools；有副作用的 workflow 使用持久 effect ledger，而不是进程内 result cache。

## 3. Installed Capability、MCP 与远端 Agent

Widget 只使用 `capability.invoke` grant 中精确的 `catalog_id + action_id`。Capability Manifest 再固定 invocation adapter、input/result schema 和 recovery policy。MCP runtime identity 仍包括 command、args、显式 env digest 与 manifest revision；变化需要持久 Run interaction 重新批准。

Widget grant 不等于 MCP spawn approval，也不等于任意 tool approval。调用必须依次通过：Widget grant → Capability action schema → adapter runtime identity permission → protocol capability/tool policy → Run effect/recovery policy。新版本不接受 Widget 直接提交 `mcp_call_tool`。

## 4. Coding Agent

Coding Agent 只在 per-Run staging App 中工作：

1. 路径必须是 Apps 根的安全直接子项，拒绝 escape 与 symlink；
2. 只允许 `controller.js`、`manifest.json` 和 `README.md`；
3. terminal 使用固定 argv 与 `create_subprocess_exec()`，不使用 shell；
4. cwd 固定在 staging，环境使用小型 allowlist；
5. stdout/stderr、wall time 和 process group 有上限；
6. Prompt 只获得批准 Runtime Contract；
7. verifier 确认 Manifest grants 完全相等、代码使用为子集，再允许原子 promote。

路径/argv/env/staging policy 降低风险，但不是完整 OS 网络/文件系统隔离。它不能替代 Widget runtime authorizer。

Windows 子进程保留运行 Node、系统 shell 和原生 CLI 所必需的非秘密 OS 变量：`SYSTEMROOT`、`SYSTEMDRIVE`、`COMSPEC`、`PATHEXT`、`USERPROFILE` 与 `WINDIR`。它们不扩大工具或文件授权，也不允许 Ambient Provider 凭据传给原生 Codex。

ACP 文件读写保持 UTF-8 文本原有的 LF/CRLF，不执行系统默认换行转换。文件变更审批把 `oldText` 的 UTF-8 SHA-256 与当前原始文件字节比较；换行变化也属于修改，旧 hash、错误路径与复用的变更证据仍被拒绝。

每轮 ACP prompt 附带三个允许产物的有界快照；新建 App 明确标记这些文件不存在。Coding Agent 应使用原生 `apply_patch` 或实际暴露的 ACP file tool 写入允许文件，不能通过 shell 读取、扫描目录或绕过文件权限。产物验证由宿主完成；过大的文件不截断为完整内容，模型必须报告缺少安全编辑上下文。

ACP 启动失败保留有界 stderr 诊断；启动及协议异常的消息在返回前使用同一脱敏边界。先移除控制序列并识别凭据字段、Bearer token、URL 凭据，再替换已知环境秘密，防止短 token 破坏字段名后导致其它凭据漏出。普通 OS 环境值不是秘密，不应整批隐藏。stderr 的保存与错误输出都有独立上限；不能把原始 stderr、含秘密的异常文本或完整环境写进日志、Run 或 UI。

MCP 停止和失败清理必须在所属 event loop 仍运行时等待子进程退出，并关闭 stdin、stdout、stderr 的全部 pipe transport。畸形或超大响应可能使 stdout 停读，因此不能只等待 PID 退出或取消 reader；这两种失败仍须完成相同清理，保持既有响应大小上限与所有 pending request 的失败语义。

Coding Agent 的版本与登录状态探针保留五秒期限。探针超时仍返回既有失败结果，调用取消仍传播取消；返回或传播前必须终止仍存活的探针子进程，并在当前 event loop 中等待退出、排空和关闭其 stdout/stderr 管道。正常探针仍返回原退出码与有界清理后的输出，不扩大 CLI 环境或执行权限。

## 5. 审计与敏感数据

前端恢复 Run 历史时，历史权限事件只用于展示执行记录。授权弹窗必须重新读取当前 Run，确认它仍为 `waiting_user`、属于当前会话，且同一 interaction 仍为 `pending`。刷新后真实待处理权限可以恢复；已处理、终态 Run 或过时的异步查询不能重新打开弹窗。断线保留有效请求，用户恢复连接后可继续响应。切换会话只清理前端显示，不批准、拒绝或修改原 Run。

预审批的 `mutation_preview`（`committed: false`）不能显示撤销已写入数据的操作；历史撤销提示须具有有效 ticket，且仍在原事件的 soft window 内。重放记录不会刷新这个时限。

- 已接入的 Run effect、Tool Gateway、adapter 与 LLM 路径按各自契约记录事件。`CapabilityAuthorizer`、App 文件操作与 Graph query 尚未统一记录每次 capability allow/deny；完整访问审计仍是未实施的覆盖项。新增 hook 应只记录 App、Manifest revision、类目、operation、resource 摘要和稳定 code，不记录文件内容、secret 或完整上游 body。
- Run events 使用版本化 envelope，并带 Run/session/step/attempt/trace 关联。
- Tool/adapter events 对敏感参数脱敏并限制大小；LLM audit 保存有界 preview、hash、usage 与 latency。
- 终态 Run events 与 LLM audit 按 retention policy 清理，但仍是敏感 workspace 数据。
- 用户批准不能代替最小 scope、schema 校验、幂等、fencing、补偿和 `needs_attention` reconciliation。
