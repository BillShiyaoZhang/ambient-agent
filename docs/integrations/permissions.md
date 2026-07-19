# 权限、执行边界与审计

Ambient Agent 使用多层 enforcement，而不是一个全局“已授权”布尔值。模型本地工具、MCP spawn、远端 Agent、OpenCode ACP 和浏览器 Widget 的安全属性不同。

## 1. 模型本地工具：Tool Gateway

模型请求的 Python tool 由 `ToolGateway` 执行。每个 `ToolSpec` 声明：

- 强类型 input/output schema；
- `read`、`write`、`delete`、`execute` 或 `network` effect；
- 所需 scopes 与 `never/always` 审批策略；
- timeout、最大输出字节数、幂等键要求及敏感参数字段。

Gateway fail closed：拒绝未注册 tool、未知/非法参数、scope 不足、缺少审批和缺少幂等键；tool event 会对声明的敏感参数脱敏。常规 Converse 目前只暴露 read tools。

当前幂等 result cache 是进程内实现；它避免同一进程中的重复调用，但不是跨重启 exactly-once 账本。带副作用的 durable workflow 仍应使用持久 idempotency key 和底层事务/补偿。

## 2. MCP 与远端 Agent 权限

`workspace/backend_permissions.json` 按 App 保存批准身份。MCP 的新身份包括精确 `command`、`args`、显式 env 的 SHA-256 digest 和 manifest revision；任何一项改变都需要重新批准。远端 Agent 当前按完整 endpoint URL 批准。

Capability、MCP tool/resource 和 Agent Run 的未批准调用会产生持久 Run interaction；resolve 以 `run_version` 原子记录响应并重新入队。权限状态不依赖全局 Future，可跨后端重启恢复。

Spawn approval 只代表允许启动特定 runtime identity，不自动代表任意 MCP tool 都已授权。Capability action 通过 manifest 固定 tool name；客户端提交的 MCP tool Run 会持久具体 tool name，但尚未根据 `tools/list` 生成动态 allowlist。

## 3. OpenCode ACP

OpenCode 只在 per-Run sibling staging App 中工作：

1. 先校验 `app_id`，live/staging 都必须是 Apps 目录的直接子项；
2. 拒绝 `..`、目录外路径、symlink/junction 和已有 App 内的不安全 link；
3. 使用 `create_subprocess_exec(argv)`，不使用 shell；command/args 中的 shell 控制字符被拒绝；
4. terminal cwd 必须位于 staging，环境只继承小型白名单并限制请求注入变量；
5. command policy 对 argv 做 exact/prefix token 比较，并保留 blocklist；未知 ACP tool kind 默认拒绝；
6. stdout/stderr 有总字节上限，子进程在独立 process group 中运行，终止采用 TERM→KILL；
7. 只有 artifact 与 Schema verification 通过或用户明确 override 后，`promote` 才原子替换 live App；失败/返工会删除 staging。

`opencode_permissions.json` 的生产结构为 strict 精确 argv 白名单：

```json
{
  "policy_mode": "strict",
  "files": {
    "allowed_extensions": [".js", ".json", ".md"],
    "allowed_filenames": ["controller.js", "manifest.json", "README.md"]
  },
  "commands": {
    "allowed_commands": ["npm test", "npm run build"],
    "allowed_prefixes": [],
    "blocklist": ["rm -rf", "curl", "wget", "sudo"]
  }
}
```

command 会被解析为固定 executable + argv token，通过 `create_subprocess_exec()` 执行。生产 policy 不允许 prefix 或任意 terminal/network；policy 外 ACP 请求直接 fail closed，不挂起 durable worker 等待进程内审批。

## 4. 审计与敏感数据

- Run events 使用版本化 envelope，并带 Run/session/step/attempt/trace 关联字段。
- Tool Gateway event 记录脱敏参数、effect、状态和输出大小。
- `LLMAuditLog` 保存有界、按 tool schema 脱敏的 prompt/response preview，以及 hash、usage、latency 和 trace 关联。

Run event 会递归脱敏常见 secret 键并限制 payload 大小，`redacted` 标记表示内容曾被隐去/截断。终态 Run events 与 LLM audit 默认保留 30 天，分别由 `RUN_EVENT_RETENTION_DAYS` 和 `AGENT_AUDIT_RETENTION_DAYS` 调整。它们仍是 workspace 内的敏感数据，不应向不受信任用户暴露。

## 5. 不应误解为强 sandbox 的部分

- OpenCode 的路径、argv、env、进程与 staging 控制降低风险，但没有 OS 级 filesystem/network isolation。
- MCP 子进程同样没有默认 network deny 或独立用户/容器隔离。
- 浏览器 Widget 的 `new Function` 是模块加载方式，不是 hostile-code security boundary。
- 用户审批不能代替强制授权、最小 scope、幂等和副作用审计。
