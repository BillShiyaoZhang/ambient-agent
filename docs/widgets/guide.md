# Widget XML 协议说明

当大语言模型需要为用户界面输出交互小程序卡片（Widget）时，它会在返回的文本流中嵌入特定格式的 XML 代码块。后端 `AgentParser` 服务会自动捕获、解析并从最终聊天气泡的文本中剔除这些 XML，然后以卡片形式分发给前端渲染。

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

### C. React JSX 动态编译渲染模式

当大模型生成需要使用 React 交互的现代组件时，可以直接输出 JSX 源码与对应的控制器代码：

```xml
<ambient-widget id="WIDGET_ID" title="WIDGET_TITLE">
<react-jsx>
  // index.jsx: 符合 React 规范的组件代码。必须默认 (default) 导出或命名导出一个组件。
  // 可以导入 "react" 和 "./controller.js" 模块。
  import React, { useState } from "react";
  import { useController } from "./controller.js";

  export default function MyWidget({ ambient }) {
    const { items, addItem } = useController(ambient);
    const [text, setText] = useState("");

    return (
      <div className="p-4 bg-slate-900/50 rounded-xl text-white">
        <h3 className="text-sm font-semibold mb-2">React 动态卡片</h3>
        <ul className="space-y-1 text-xs mb-3">
          {items.map((item, idx) => <li key={idx}>- {item.title}</li>)}
        </ul>
        <div className="flex gap-2">
          <input 
            type="text" 
            value={text} 
            onChange={(e) => setText(e.target.value)} 
            className="bg-black/20 border border-white/10 px-2 py-1 rounded text-xs w-full"
          />
          <button 
            onClick={() => { addItem(text); setText(""); }} 
            className="bg-blue-600 px-3 py-1 rounded text-xs text-white"
          >
            添加
          </button>
        </div>
      </div>
    );
  }
</react-jsx>
<js-script>
  // controller.js: 核心控制器 Hook 逻辑，必须导出 useController 自定义 Hook。
  import { useState, useEffect } from "react";

  export function useController(ambient) {
    const [items, setItems] = useState([]);

    useEffect(() => {
      // 实时订阅 Task 实体数据
      const unsub = ambient.graph.subscribe({ type: "Task" }, (tasks) => {
        setItems(tasks);
      });
      return unsub;
    }, [ambient]);

    const addItem = async (title) => {
      await ambient.graph.mutate([
        {
          action: "create_node",
          type: "Task",
          properties: { title, status: "pending" }
        }
      ]);
    };

    return { items, addItem };
  }
</js-script>
</ambient-widget>
```

## 2. 字段规范说明

- `id`: 卡片的唯一标识符。英文字母、数字和横杠组合（如 `weather-card`）。如果重名，后台服务会检测冲突，若为旧应用会进入更新生命周期。
- `title`: 卡片的标题。展示在 Canvas 卡片拖拽标题栏中。
- `<html-content>`: HTML 实体标记（仅限 HTML 模式）。可直接编写符合现代浏览器标准的 DOM 树。支持行内 Tailwind 工具类（Tailwind CSS v4 在沙箱外自动编译生效）。
- `<css-styles>`: 局部样式表（仅限 HTML 模式）。在沙箱挂载时会自动转换为 scoped 标签插入卡片容器中，绝不污染系统主界面样式。
- `<layout-json>`: UI 组件声明 JSON 结构（仅限 A2UI 模式）。
- `<react-jsx>`: React 组件 JSX 源码（仅限 React 模式）。前端使用 `@babel/standalone` 动态编译并在 `SandboxWidget` 内部隔离渲染。
- `<js-script>`: 交互脚本（所有模式均可用）。在 HTML 模式和 A2UI 模式下，作为闭包执行；在 React 模式下，作为 `controller.js` 控制器逻辑。它接收注入参数并在隔离环境下执行。
