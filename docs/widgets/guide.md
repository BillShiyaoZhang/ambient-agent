# Widget XML 协议说明

当大语言模型需要为用户界面输出交互小程序卡片（Widget）时，它会在返回的文本流中嵌入特定格式的 XML 代码块。后端 `AgentParser` 服务会自动捕获、解析并从最终聊天气泡的文本中剔除这些 XML，然后以卡片形式分发给前端渲染。

---

## 1. 协议结构定义

系统支持两种模式的 Widget 卡片定义：

### A. HTML / CSS / JS 混合渲染模式（默认）
这种模式允许直接定义卡片内部的 HTML 标记、局部样式表以及交互脚本：

```xml
<ambient-widget id="WIDGET_ID" title="WIDGET_TITLE">
<html-content>
  <!-- 符合 HTML5 规范的结构，支持 Tailwind 类 -->
  <div class="p-4 rounded-lg bg-slate-900/50">
    <button id="action-btn" class="bg-purple-600 px-3 py-1 text-xs text-white rounded">
      触发操作
    </button>
  </div>
</html-content>
<css-styles>
  /* 可选：特定的局部样式扩展 */
  #action-btn { transition: all 0.2s ease-in-out; }
</css-styles>
<js-script>
  // 可选：JavaScript 交互逻辑。
  // 执行时会自动传入参数 root (指向 Widget 的 DOM 根元素) 和 ambient (SDK)。
  const btn = root.querySelector("#action-btn");
  btn.addEventListener("click", () => {
    ambient.sendMessage("用户点击了 Widget 操作按钮！");
  });
</js-script>
</ambient-widget>
```

### B. A2UI JSON 布局渲染模式
当大模型生成复杂的结构化控制面板时，可以使用 JSON 声明 UI 组件：

```xml
<ambient-widget id="WIDGET_ID" title="WIDGET_TITLE">
<layout-json>
  {
    "type": "container",
    "children": [
      {
        "type": "button",
        "id": "my-btn",
        "label": "确认提交",
        "color": "primary"
      }
    ]
  }
</layout-json>
<js-script>
  // 此时可以绑定状态和 UI 事件句柄
  ambient.ui.on("click", "my-btn", () => {
    ambient.sendMessage("A2UI 声明式按钮被点击");
  });
</js-script>
</ambient-widget>
```

---

## 2. 字段规范说明

*   `id`: 卡片的唯一标识符。英文字母、数字和横杠组合（如 `weather-card`）。如果重名，后台服务会检测冲突，若为旧应用会进入更新生命周期。
*   `title`: 卡片的标题。展示在 Canvas 卡片拖拽标题栏中。
*   `<html-content>`: HTML 实体标记。可直接编写符合现代浏览器标准的 DOM 树。支持行内 Tailwind 工具类（Tailwind CSS v4 在沙箱外自动编译生效）。
*   `<css-styles>`: 局部样式表。在沙箱挂载时会自动转换为 scoped 标签插入卡片容器中，绝不污染系统主界面样式。
*   `<js-script>`: 交互脚本。在隔离的 `new Function` 环境下运行，接收 `(root, ambient, fetch)` 三个注入参数。
