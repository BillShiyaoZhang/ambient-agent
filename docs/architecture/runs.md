# 后台 Run 与 Runtime

Ambient Agent 将已安装能力、执行环境和一次具体工作拆分为三个对象：

- **App / Capability** 声明可用 action；它可以绑定 Canvas UI，也可以只有后台 action。
- **Runtime** 是 action 的执行环境。平台托管 MCP 进程，观察远端 HTTP Agent，并提供内置 Agent Runtime。
- **Run** 是一次持久化执行。关闭窗口、切换聊天或断开浏览器都不会终止它。

## 状态与恢复

Run 的主状态流为 `queued → running → succeeded | failed`。需要用户输入时进入 `waiting_user`，取消中的执行进入 `cancel_requested`，无法安全重放的中断外部调用进入 `needs_attention`。

Run、事件、交互和 step checkpoint 存在 `workspace/.ambient/runs.db`。Worker 使用 lease 和 heartbeat 领取队列任务。服务恢复时：

- queued 和 waiting_user 保留；
- `restart_safe` action 回到队列并使用相同 checkpoint；
- manual action 的未知外部调用进入 needs_attention；
- 已完成 step 不会再次执行。

默认全局并发为 4，每个 owner 并发为 1，可通过 `RUNNER_MAX_CONCURRENCY` 和 `RUNNER_MAX_PER_APP` 调整。

## Capability V2

V2 manifest 使用 `actions` 声明输入、结果、调用 adapter 和恢复策略。V1 的单一 `input_schema + invocation` 会被映射为 `run` action。

没有 UI 的 capability 在 App Center 以 action launcher 打开；输入通过 JSON Schema 校验后创建 Run。生成 UI 是可选能力，不再是调用后台能力的前置条件。

## API

- `POST /api/runs`、`GET /api/runs`、`GET /api/runs/{id}`
- `POST /api/runs/{id}/cancel`、`POST /api/runs/{id}/retry`
- `POST /api/run-interactions/{id}/resolve`
- `GET /api/runtimes`、`POST /api/runtimes/{id}/stop`
- `/ws/runs?after_sequence=N` 提供可重放的 workspace 事件流。

Widget 可以使用 `ambient.runs.start/get/cancel/subscribe`。`ambient.capabilities.invoke` 也是 Run 的兼容封装，会在 terminal 状态返回结构化结果。

任务中心统一展示 Active、待处理、历史和 Runtime；聊天来源的 Run 可以跳回对应 session。App 卸载或 Runtime 停止在存在活动 Run 时返回 `409`。
