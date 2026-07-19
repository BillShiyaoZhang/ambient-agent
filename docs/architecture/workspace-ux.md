# 工作区交互

Ambient Agent 采用 App-first 桌面工作区。聊天是浮层辅助界面，不会压缩应用可用区域。

## 工作区状态

- **首页**：没有打开应用时，应用中心占据主界面。
- **最大化**：启动应用后，它默认在产品视口内最大化；其他已打开应用继续挂载在后方。
- **浮动**：恢复最大化应用会得到可移动、可缩放的窗口。
- **贴靠**：拖到左右边缘占半屏，拖到角落占四分之一，拖到顶边最大化。
- **移动端**：宽度小于 720 px 时只显示活动应用，隐藏拖动、缩放、贴靠和布局控制。

关闭窗口只会把应用移出工作区，不会卸载应用。关闭最后一个应用返回首页。切换聊天会话不会改变全局工作区。

## 系统栏与响应式布局

桌面工作区在应用舞台上方保留一行 52 px 系统栏。最大化应用的关闭/恢复和标题进入系统栏；浮动或贴靠窗口保留自己的标题栏，因此不会出现重复标题栏。

- 宽度至少 1024 px：显示活动应用信息、居中的应用/任务切换区，以及布局、审计、模型、语言、主题操作。
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

## 辅助界面

- 聊天从右下角按钮打开为 380 × 560 px 浮层，小屏下变为 bottom sheet/全屏 drawer。
- 任务抽屉展示活动、需关注、历史 Run 和 runtime；用户确认通过 blocking dialog 完成。
- 应用中心在首页为主界面，在有窗口时作为 overlay 打开。
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
- 控件具有 accessible name、可见焦点和至少 40 px 点击区域；移动端至少 44 px。
- reduced-motion 会关闭 spring/transform 动画；透明材质在不支持 backdrop filter 时有不透明 fallback。
- 普通文本以 WCAG AA 对比度为目标，焦点状态不只依赖颜色表达。

核心状态迁移与几何算法位于 `frontend/src/lib/windowManager.ts`，交互实现位于 `AppWorkspace.tsx`，覆盖测试位于 `tests/frontend/window_manager.test.ts` 与 `workspace_ui.test.tsx`。
