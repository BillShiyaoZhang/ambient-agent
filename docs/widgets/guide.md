# Widget XML 协议说明

当大语言模型需要为用户界面输出交互小程序卡片（Widget）时，它会在返回的文本流中嵌入特定格式的 XML 代码块。后端 `AgentParser` 服务会自动捕获、解析并从最终聊天气泡的文本中剔除这些 XML，然后以卡片形式分发给前端渲染。

为了同时保障开发的高效性、首屏的高性能渲染、视觉风格的一致性以及自定义界面的自由度，系统采用 **React + HTM** 统一声明式渲染模式。

---

## 1. 统一声明式渲染协议（推荐首选）

大模型只需输出单个 `<js-script>` 块，通过 `ambient.html`（基于 `htm`）编写声明式的 React 组件。该模式无需加载繁重的编译器，支持响应式 Hooks 以及预置的高清组件。

### A. 协议结构示例

```xml
<ambient-widget id="todo-manager" title="待办任务管理">
<js-script>
  // 1. 解构导入 React Hooks
  const { useState, useEffect } = ambient.react;

  // 2. 解构导入系统预置组件（自带样式规范，离线可用）
  const { Card, Button, TextField, List } = ambient.components;

  export default function TodoWidget() {
    const [tasks, setTasks] = useState([]);
    const [input, setInput] = useState("");

    useEffect(() => {
      // 订阅后端实时图数据
      const unsub = ambient.graph.subscribe({ type: "Task" }, (data) => {
        setTasks(data.nodes || []);
      });
      return unsub;
    }, []);

    const handleAdd = async () => {
      if (!input.trim()) return;
      await ambient.graph.mutate([
        {
          action: "create_node",
          type: "Task",
          properties: { content: input, completed: false }
        }
      ]);
      setInput("");
    };

    // 使用 ambient.html 声明界面（使用 \$ 避免模板字符串变量插值冲突）
    return ambient.html`
      <\${Card} title="我的待办列表">
        <div class="flex gap-2 mb-3">
          <\${TextField} 
            placeholder="输入新任务..." 
            value=\${input} 
            onChange=\${e => setInput(e.target.value)} 
            onEnter=\${handleAdd}
          />
          <\${Button} label="添加" onClick=\${handleAdd} />
        </div>
        
        <!-- 混合使用预置组件与自定义 HTML 节点，并可直接写 Tailwind 工具类 -->
        <div class="border-t border-white/5 pt-3">
          <\${List} 
            items=\${tasks.map(t => t.properties.content)} 
            itemStyle=\${{ backgroundColor: 'rgba(255,255,255,0.01)' }}
          />
        </div>
      <//>
    `;
  }
</js-script>
</ambient-widget>
```

### B. 预置组件库（`ambient.components`）说明

预置组件遵循系统整体的设计系统（Design System），可以自动适应暗黑/透明毛玻璃主题，并且**完全支持离线渲染**。

1. **容器类组件**：
   - `<\${Card} title="标题" onClick=\${...}>子节点<//>`：圆角卡片面板。
   - `<\${Column} gap="8px" padding="10px">子节点<//>`：垂直弹性盒布局。
   - `<\${Row} gap="8px" align="center">子节点<//>`：水平弹性盒布局。
2. **基础交互组件**：
   - `<\${Text} text="内容" style=\${...} />`：文本标签。
   - `<\${Button} label="按钮文本" variant="primary|secondary|danger" onClick=\${...} />`：扁平交互按钮。
   - `<\${TextField} label="标签" placeholder="..." value=\${value} onChange=\${...} onEnter=\${...} />`：文本输入框。
   - `<\${Checkbox} label="标签" checked=\${checked} onChange=\${...} />`：复选框。
3. **数据展示组件**：
   - `<\${List} items=\${itemsArray} onItemClick=\${...} itemStyle=\${...} />`：标准间距列表。
   - `<\${Table} columns=\${columnsArray} rows=\${rowsArray} onRowClick=\${...} />`：自适应表格。

---

## 2. 字段规范说明

- `id`: 卡片的唯一标识符。英文字母、数字和横杠组合（如 `weather-card`）。
- `title`: 卡片的标题。展示在 Canvas 拖拽标题栏中。
- `<js-script>`: 核心交互逻辑与视图渲染脚本。在统一模式下，它需要 `export default` 导出一个标准的 React 组件，并利用宿主传入的 `ambient` API 提供的 `react`（Hooks）、`components`（预置组件）以及 `html`（模板渲染）构建卡片。

---
