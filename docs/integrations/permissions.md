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

## 5. 审计与敏感数据

- 每次 capability allow/deny 记录 App、Manifest revision、类目、operation、resource 摘要和稳定 code，不记录文件内容、secret 或完整上游 body。
- Run events 使用版本化 envelope，并带 Run/session/step/attempt/trace 关联。
- Tool/adapter events 对敏感参数脱敏并限制大小；LLM audit 保存有界 preview、hash、usage 与 latency。
- 终态 Run events 与 LLM audit 按 retention policy 清理，但仍是敏感 workspace 数据。
- 用户批准不能代替最小 scope、schema 校验、幂等、fencing、补偿和 `needs_attention` reconciliation。
