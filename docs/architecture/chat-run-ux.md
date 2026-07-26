# Chat 与 Run 信息体验设计

> 状态：Phase A–B 已实现；Phase C–D 待后续迭代。本文记录信息架构、事件契约、当前实现与后续顺序。

## 1. 目标与原则

当前聊天把阶段提示、Coding Agent 文本、工具调用、校验报告、审批和最终结果都投影成相同的消息气泡。用户很难分辨“系统正在做什么”“现在是否需要我操作”和“最终交付是什么”。

新的体验遵循四条原则：

1. **结果优先**：普通聊天只突出用户输入、当前任务卡和最终回答。
2. **过程可查**：工具、校验、自动修复和耗时进入默认折叠的 Run 时间线，不丢失可审计性。
3. **交互显式**：等待审批、权限或补充信息时显示独立 action card，不混进自然语言日志。
4. **流式与耐久分离**：实时 delta 提供响应感，持久 Run event 负责重放、恢复和最终一致性。

Codex 把长任务组织为独立 thread，并把变更审阅留在 thread 内；Codex cloud 同时强调实时进度、终端/测试证据和任务完成后的 review。这说明“任务状态、过程证据、最终结果”应当是三个层级，而不是一串同权消息。[Codex app](https://openai.com/index/introducing-the-codex-app/) · [Codex cloud](https://openai.com/index/introducing-codex/)

GitHub Copilot Agent 也把 session overview、live session log、工具/验证证据和最终 review 分开；其 SDK 明确区分实时但不持久化的 ephemeral delta 与完整、可重放的 persisted event。这一分层与 Ambient 已有的 durable RunStore 很契合。[Agent sessions](https://docs.github.com/en/enterprise-cloud%40latest/copilot/how-tos/copilot-on-github/use-copilot-agents/manage-and-track-agents) · [Streaming events](https://docs.github.com/en/copilot/how-tos/copilot-sdk/use-copilot-sdk/streaming-events)

## 2. 当前链路的问题

当前实现有四个具体限制：

- `Message` 只有 `sender/content/timestamp`，不能表达阶段、activity、artifact、approval 或 error。
- ACP 的 `agent_message_chunk` 和 tool update 被拼成累计字符串，再以 `id=-1` 的普通 `reply` 投影；前端只能替换一条 pending 消息。
- durable reducer 的 `_emit()` 先写 step event buffer，直到 step commit 后才投影。Coding Agent 即使逐 chunk 回调，用户也会在阶段结束时一次性收到一大段内容，并非真正流式。
- `/ws/runs` 已有 sequence、epoch、replay 和去重能力，但聊天只消费少数业务 payload，没有把 `step_started`、`progress` 和 `step_committed` 组合成用户可读的任务状态。

因此不能只改气泡 CSS。需要先建立 chat projection model，再接入真正的 live delta。

## 3. 推荐的信息层级

聊天主区域只保留三类一级内容：

| 一级内容 | 默认展示 | 说明 |
| --- | --- | --- |
| 用户消息 | 完整 | 用户意图和后续 steering |
| Run 卡片 | 运行中展开摘要，结束后折叠 | 当前阶段、耗时、是否等待用户、过程入口 |
| 最终回答/交付 | 完整 | 结果、验证证据、App/Artifact 操作 |

Run 卡片内的二级 activity 按语义分组：

- 理解与规划；
- Schema / capability 对齐；
- 生成或修改文件；
- 工具执行；
- 独立验证；
- 自动修复；
- 发布与交付。

连续的低价值事件要聚合。例如 18 次文件读取显示为“检查了 12 个文件 · 18 次操作”，默认不逐条占据聊天高度。失败工具、修改文件、测试结果和权限动作仍单独保留。

不展示模型的原始思维链。Activity 只展示可验证的行动摘要、工具、输入输出边界和结果。

## 4. 交互草图

运行中：

```text
你
生成一个全量天气 App

Ambient · 正在构建 weather-app                         02:14
✓ 需求与方案   ✓ Schema   ↻ 编码   · 验证   · 发布

  正在修复校验问题（第 4 次）
  graph query type 必须使用字符串字面量

  [查看过程 12]                                      [停止]
```

成功后：

```text
Ambient · 已完成
天气 App 已生成并通过静态校验、Schema 校验和运行时 smoke test。

[打开 App] [查看验证] [查看过程]
```

重复错误熔断：

```text
Ambient · 自动修复已停止
同一校验错误连续出现，继续尝试不会产生新结果。草稿未发布且已保留。

错误：Unexpected token (1100:3)
[查看错误详情] [带具体说明继续修复]
```

## 5. 流式事件模型

建议保留两条事件通道。

### 5.1 Durable lane

Durable event 必须先写 SQLite，支持 epoch/sequence replay，用于恢复事实状态：

- `run_phase_started` / `run_phase_completed`；
- `activity_completed`；
- `assistant_message_completed`；
- `interaction_requested` / `interaction_resolved`；
- `artifact_ready`；
- `run_succeeded` / `run_failed`。

现有 `step_started`、`step_committed` 和业务 payload 可以先通过 projection adapter 映射到这些 UI 语义，不需要立即迁移底层数据库 schema。

### 5.2 Live lane

Live event 不进入聊天历史，允许丢弃，用于正在进行的视觉反馈：

- `assistant_message_delta`；
- `activity_delta`；
- `tool_progress`；
- `run_heartbeat`。

当前 v1 envelope（由 `/ws/run-live?session_id=...` 的 `run_live_event.event` 承载）：

```json
{
  "schema_version": 1,
  "run_id": "run-id",
  "session_id": "session-id",
  "step_id": "stage_code",
  "attempt": 1,
  "stream_id": "run-id:stage_code:1:activity",
  "chunk_sequence": 17,
  "kind": "activity_delta",
  "delta": "正在检查 controller.js",
  "replace": false,
  "created_at": "2026-07-26T06:00:00Z"
}
```

规则：

1. `stream_id + chunk_sequence` 幂等追加，重复 chunk 丢弃。
2. live delta 只修改 `liveStreams`，不能直接追加持久 `messages`。
3. 收到对应 completed event 后，用 durable 内容原子替换 live buffer。
4. 断线时清空未完成 delta，并从 `/ws/runs` 重放 durable event；不能把半条流式文本写成最终回答。
5. 后端进程崩溃导致 live delta 丢失是可接受的，Run checkpoint 与 completed event 才是正确性来源。
6. 前端按 animation frame 或 40–60 ms 合并 chunk，避免每 token React render。

## 6. 前端 projection state

建议把当前 `Message[]` 替换为 projection store：

```ts
interface ConversationProjection {
  items: ConversationItem[];
  runs: Record<string, RunCardState>;
  liveStreams: Record<string, LiveStreamState>;
  liveStepWatermarks: Record<string, number>;
  interactions: Record<string, InteractionCardState>;
}

type ConversationItem =
  | UserMessageItem
  | RunCardItem
  | FinalAnswerItem
  | ArtifactItem;
```

同一 `run_id` 在一级列表中最多只有一张 Run 卡。`step_id + activity_id` 负责二级 activity 去重；`event_id` 和 run stream cursor 继续负责 durable replay 去重。

当前 `mergeIncomingMessage()` 可保留为兼容层，但新事件不得继续依赖 `id=-1` 表达所有 pending 状态。

## 7. 流式渲染与滚动

- 用户在距底部 80 px 内时自动跟随；一旦主动上滚就停止抢焦点，并显示“有新进展”按钮。
- delta 可显示轻量 caret，但 activity 不使用逐 token 打字动画。
- Markdown 以 chunk buffer 渲染；未闭合 code fence 保持临时 code block，completed 后再做最终语法高亮。
- `aria-live` 只播报阶段变化、等待用户和完成/失败，不逐 token 播报。
- reduced-motion 下关闭 caret、spinner 旋转之外的位移动画。
- Run 卡折叠后保留最终状态、耗时、repair 次数和验证摘要。

## 8. Approval、错误与自动修复

Approval 应是结构化卡片：

- 普通 plan/schema 选择可直接内联；
- 涉及权限、不可逆操作或大段 diff 时，卡片展示摘要，点击后打开现有 blocking dialog；
- interaction resolved 后卡片变成只读记录，不从历史消失。

错误分为两层：

1. 一级提供可行动的自然语言摘要；
2. “详情”内展示 stable code、stage、bounded diagnostic、attempt、artifact revision。

自动修复只更新一条 activity：

```text
独立验证 → 未通过 → 自动修复 1 → 再验证 → 自动修复 2
```

默认展示最新 finding；历史 finding 放在展开时间线中。完全相同 finding 熔断后，主卡必须明确“已停止、未发布、草稿已保留”，不再用普通失败消息诱导用户无条件发送 `/repair`。

## 9. 实施顺序

### Phase A：projection model

- 新增 `ConversationProjection` 与 `RunCard`，先消费现有 durable events。
- 将阶段提示和 `id=-1` 累计日志收进 activity timeline。
- 将桌面聊天浮层默认尺寸调整为约 432 × 600 px，并增加受视口约束的缩放与尺寸持久化。
- 保持现有 reply/widget/approval API 兼容。

### Phase B：真实 live delta

- 为 ACP callback 增加独立 live WebSocket 投影，不经过 reducer commit buffer。
- 增加 `stream_id/chunk_sequence`、限流、合并和 completed replacement。
- 覆盖断线、重放、重复、乱序和 session 切换测试。

已实现说明：

- 后端使用 session-scoped、内存有界队列投影 `assistant_message_delta`、`activity_delta` 和 `tool_progress`；慢客户端只丢最旧的临时事件，不会阻塞 workflow。
- ACP 累计 snapshot 在 workflow 边界转成 delta；完全重置的 snapshot 使用 `replace=true`，不会重复拼接。
- 前端每 50 ms 批量归并事件，以 `stream_id + chunk_sequence` 去重并忽略乱序；发现序号缺口时提示最终以 durable event 校准。
- durable `step_committed`、最终 reply、等待/终态会清理对应 live buffer；断线和 session 切换会丢弃未完成 buffer，再由 `/ws/runs` 恢复事实状态。

### Phase C：结构化 activity 与 interaction

- 把 tool call、verification、repair、artifact 和 approval 改为类型化事件。
- 将高风险 approval 继续交给 blocking dialog，普通选择内联。
- 增加 debug 模式，按需显示原始 bounded event JSON。

### Phase D：收敛旧协议

- 停止用普通 `reply` 承载阶段进度。
- 删除 `id=-1` 单 pending 消息约定。
- 将聊天历史持久化限定为用户消息、最终回答和必要的 interaction 摘要。

## 10. 已确认的产品选择与验收标准

本轮已确认：

1. Run 完成后默认折叠过程，只展开最终回答。
2. 工具默认显示人类可读动作，原始命令只在详情/debug 中显示。
3. 桌面聊天使用约 420–440 px 的默认宽度，并允许用户自行缩放；移动端保持全屏 drawer。
4. 低风险 plan/schema 选择内联，高风险权限继续使用 blocking dialog。

桌面缩放契约：

- 默认约 432 × 600 CSS px；建议最小 360 × 420 px，最大宽度为 `min(720px, viewport - 32px)`，最大高度为 `viewport - 96px`。
- 浮层继续锚定右下角；从上边、左边和左上角拖动时改变尺寸，不移动聊天入口按钮。
- 用户尺寸写入独立的 workspace UI preference；重开聊天和刷新后恢复，切换会话不改变尺寸。
- 视口缩小时自动 clamp 到可见区域，但不覆盖已保存的用户偏好；视口恢复后可恢复原尺寸。
- 小于 720 px 时忽略桌面尺寸，使用现有全屏 drawer，隐藏 resize handle。
- 菜单提供“紧凑 / 默认 / 宽屏 / 恢复默认”预设；resize handle 支持键盘方向键，保证不依赖精确指针操作。
- 缩放过程使用 pointer capture 与 animation frame；只在手势结束时持久化，避免高频写 storage。
- 缩放不能改变消息滚动锚点，也不能让 composer、停止按钮或 interaction action 离开可视区域。

验收标准：

- 一个 10 分钟、上百次 tool update 的 Run 在主聊天中仍只占一张 Run 卡。
- 首个 live delta 在后端收到 ACP chunk 后 200 ms 内可见。
- 断线重连后无重复 activity、无半条最终回答、审批状态不倒退。
- 连续 repair 只更新现有 activity，不产生多条“请手动 repair”消息。
- 用户上滚阅读时流式输出不抢滚动位置。
- 桌面缩放后刷新可恢复尺寸；窄视口下无溢出，回到桌面后仍保留用户偏好。
- 只使用键盘和 screen reader 可以完成展开、审批、停止与打开 artifact。
