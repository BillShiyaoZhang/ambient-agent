# 工作区交互

Ambient Agent 采用 App-first 桌面工作区。聊天是浮层辅助界面，不会压缩应用可用区域。

## 工作区状态

- **首页**：没有打开应用时，应用中心占据主界面。
- **最大化**：启动应用后，它默认在产品视口内最大化；其他已打开应用仍保留为工作区窗口，但其 Runtime 按下述生命周期管理。
- **浮动**：恢复最大化应用会得到可移动、可缩放的窗口。
- **贴靠**：拖到左右边缘占半屏，拖到角落占四分之一，拖到顶边最大化。
- **移动端**：宽度小于 720 px 时只显示活动应用，隐藏拖动、缩放、贴靠和布局控制。

关闭窗口只会把应用移出工作区，不会卸载应用。关闭最后一个应用返回首页。切换聊天会话不会改变全局工作区。

## 系统栏与响应式布局

桌面工作区在应用舞台上方保留一行 52 px 系统栏。最大化应用的关闭/恢复和标题进入系统栏；浮动或贴靠窗口保留自己的标题栏，因此不会出现重复标题栏。

- 宽度至少 1024 px：显示活动应用信息、居中的应用/任务切换区，以及布局、图谱探索、审计、模型、语言、主题操作。
- 720–1023 px：保留核心切换操作，其余动作进入“更多”菜单。
- 小于 720 px：活动应用填满舞台，系统栏只保留活动标题和关闭操作，其他动作进入“更多”。
- 系统栏和浮层遵循 `safe-area-inset-*`。

## 窗口交互契约

- 浮动窗口最小为 360 × 240 CSS px，并始终保留至少 48 px 标题栏在视口内。
- 标题栏移动窗口，八个边/角 handle 缩放窗口，双击标题栏切换最大化。
- 拖动最大化窗口会在指针下恢复；按住 Alt/Option 可临时关闭贴靠。
- 松开指针前显示贴靠预览。手动缩放贴靠窗口时先恢复为浮动模式。
- 指针移动通过 pointer capture 与 animation frame 更新；持久化只发生在手势结束时。
- `open_app_ids` 同时是窗口 z-order；聚焦窗口会把 id 移到末尾。
- 布局预设包括聚焦、左右排列和自动网格。

## Widget Runtime 生命周期

同时打开的应用数量不等于同时常驻的 Widget Runtime 数量。工作区从 `active_app_id` 和 `open_app_ids` 的最近使用顺序派生四种瞬时状态，不把它们写入 Canvas：

- **Active**：当前聚焦的应用。它的 iframe、WebSocket 或 Pixel Runtime context 保持挂载并可交互。
- **Warm**：最近使用的一个非活动应用。最多保留一个 Warm Runtime，以便快速切回；其他非活动应用不会继续占用 Runtime。Warm Pixel Widget 必须发送 `visibility=false` 并停止 screencast，即使浏览器标签页仍可见。
- **Suspending**：原 Warm 超出常驻预算后，最多保留一个正在退出的 Runtime。固定 Frame 收到认证 MessagePort 上的 `before_suspend` 后调用 Controller 注册的 `ambient.lifecycle.onBeforeSuspend` handler；Widget 可在其中等待最后一次 `ambient.storage` 写入。宿主收到确认或等待 1 秒后卸载，工作区另有 1.25 秒兜底。因而正常稳态最多常驻两个 Runtime，刷盘窗口内最多三个；快速连续切换会用最新退出者替换旧的挂起请求，不能无界累积 Runtime。旧 Pixel 回滚链路不提供该存储协议，会停止可见性并立即释放。
- **Suspended**：其余已打开应用。工作区保留窗口位置、模式和 z-order，但卸载 Widget 内容，从而销毁 iframe、WebSocket 和 Pixel Runtime context。窗口内容区显示可点击的恢复占位；点击占位或应用切换器会将该应用恢复为 Active，原 Active 变为 Warm，原 Warm 在超过预算时变为 Suspended。

Warm iframe 内部的输入事件不会跨 browsing context 冒泡，因此固定 Frame 必须通过已认证的
MessagePort 把可信的 `pointerdown`、`keydown` 和 `wheel` 用户激活通知宿主。只有浏览器标记为
`isTrusted` 的事件可以触发该内部通知，Controller 合成事件或公开 `hostEvent` API 都不能伪造它。
宿主收到后立即将该窗口提升为 Active、更新 MRU 并持久化 Canvas。这样用户在 Warm 应用里的
第一次鼠标、键盘或滚轮交互不会被误判为仍在后台，也不会在随后切换第三个窗口时错误挂起刚
使用的应用。

Pixel Runtime 的 `pointerdown`、`wheel` 和 `keydown` 起始输入在转发给 Runtime 前也必须先走
同一个宿主激活入口；单纯的 `pointermove` 不得造成 focus-follows-mouse 或 Canvas 写入。已经是
规范 Active/MRU 的应用不会产生重复 Canvas 写入；仅 `onActivate` 回调引用变化不得重建 Pixel
WebSocket，Warm → Active 也必须复用原连接。

如果持久化的 `active_app_id` 对应应用暂时不可用或缺少窗口状态，工作区会瞬时回退到
`open_app_ids` 中最近的可渲染窗口作为 Active，并据此选择 Warm；仅渲染该容错状态不会自行
改写持久化 Canvas。用户第一次真实地与这个派生 Active 交互时，宿主会把它移到 MRU 末尾、写入
`active_app_id` 并持久化修正后的 Canvas。

Canvas 初始加载会并发获取已打开应用的快照。快照必须按应用 `id` 和 `manifest_revision` 以
函数式状态更新合入；如果等待中的快照返回前 WebSocket 已经送达同一应用的更新，则当前事件版本
优先，迟到的启动快照不得把它覆盖或制造重复应用。

挂起不是关闭或卸载：应用仍在 `open_app_ids` 中，窗口几何信息不会丢失。关闭窗口仍只会把应用移出工作区；重新聚焦 Suspended 应用时，Runtime 会按正常启动流程重建。

Suspended 是“释放 Runtime 后可恢复重载”，不是浏览器级无损冻结：任意 React hook
内存态都会随卸载销毁。生成的 App 必须把用户草稿、表单值、编辑内容等需要跨挂起保留的
工作状态从 `ambient.storage` hydrate，并在每次有意义的修改后写穿；Graph/File 中的
规范数据仍使用其已批准能力。使用 debounce 时必须注册 `ambient.lifecycle.onBeforeSuspend`
异步 handler，并等待最新的持久化写入；该握手是有界的安全网，不是无限期保存承诺。仅
hover、临时菜单等纯展示状态允许重置。旧 App 如果只把
用户输入留在内存中，恢复时可能重置，必须先升级其持久化逻辑，不能把 Active/Warm 预算
描述成通用状态快照机制。

## 辅助界面

- 聊天从右下角按钮打开为 380 × 560 px 浮层，小屏下变为 bottom sheet/全屏 drawer。
- 任务抽屉展示活动、需关注、历史 Run 和 runtime；用户确认通过 blocking dialog 完成。
- 应用中心在首页为主界面，在有窗口时作为 overlay 打开。
- 图谱探索工作台复用同一交互式图组件浏览 Ontology、KG、Agent 编排与隐私数据地图；它是可信 Host 的辅助界面，不作为生成 App 安装。
- 审计日志、模型设置和系统菜单使用共享的 `SystemDialog`、`SystemDrawer`、`SystemPopover` 与 `SystemIconButton`。

Popovers 支持 Escape、外部点击和焦点返回。审批对话框是 blocking 的：Escape 或点击遮罩不会被解释为批准或拒绝。

## Canvas V3

`GET /api/canvas` 与 `POST /api/canvas` 使用全局 Canvas V3：

```json
{
  "version": 3,
  "open_app_ids": ["weather", "tasks"],
  "active_app_id": "tasks",
  "windows": {
    "tasks": {
      "mode": "maximized",
      "bounds": { "x": 0.16, "y": 0.12, "width": 0.68, "height": 0.72 }
    }
  }
}
```

`bounds` 是相对于系统栏下方应用舞台的归一化坐标。旧 V1/V2 的 `pinned_ids` 和 `widget_spans` 会迁移为 V3 浮动窗口；当前持久化只写 V3。

## 主题与可访问性

- 主题偏好为 `system`、`light` 或 `dark`；默认 `system` 并跟随操作系统实时变化。
- 宿主和标准 `ambient.components` 使用有效主题；自定义 Widget 的硬编码颜色不会自动改写。
- Widget 通过同一 Runtime session 接收 `{ theme, locale, reducedMotion }` 展示上下文；主题或语言变化不重建 Chromium BrowserContext。
- 控件具有 accessible name、可见焦点和至少 40 px 点击区域；移动端至少 44 px。
- reduced-motion 会关闭 spring/transform 动画；透明材质在不支持 backdrop filter 时有不透明 fallback。
- 普通文本以 WCAG AA 对比度为目标，焦点状态不只依赖颜色表达。

核心状态迁移与几何算法位于 `frontend/src/lib/windowManager.ts`，交互实现位于 `AppWorkspace.tsx`，覆盖测试位于 `tests/frontend/window_manager.test.ts` 与 `workspace_ui.test.tsx`。
