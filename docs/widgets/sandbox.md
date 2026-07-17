# 沙箱隔离机制

由于 Ambient Agent 的 Widget 是由大模型动态生成的，为了防止恶意的或编写错误的卡片破坏主系统的布局、窃取全局变量或造成全局样式污染，前端在 `frontend/src/components/SandboxWidget.tsx` 中实现了一套极其严格的**双重沙箱隔离**。

## 1. 样式隔离：CSS Scoping

在主页面中，每个 Widget 卡片都会生成一个唯一的 Scope ID（例如 `scope-1a2b3c`），并给最外层容器打上特定的自定义属性 `data-widget-scope="[ScopeId]"`。

当前端收到卡片的 CSS 样式流时，并不会直接将其插入到全局 `<style>` 标签中，而是通过 `scopeCss` 编译器对所有选择器执行**前缀绑定**：

```typescript
const scopeCss = (css: string, scopeId: string): string => {
  const prefix = `[data-widget-scope="${scopeId}"]`;
  // 利用正则表达式匹配选择器，并将 selector 转换为 prefix + selector 的嵌套格式
  // 例如：.btn-action { color: red; } 转换为：
  // [data-widget-scope="scope-1a2b3c"] .btn-action { color: red; }
};
```

### 特殊选择器转换：

- `.my-class` $\rightarrow$ `[data-widget-scope="scope-xxx"] .my-class` （嵌套绑定）
- `:root`, `html`, `body` $\rightarrow$ `[data-widget-scope="scope-xxx"]` （作用域替换）
- `@keyframes`（动画帧如 `from`, `to`, `%`）$\rightarrow$ 保持原样不添加前缀，保证 CSS 动画正常运行。

## 2. 脚本隔离：JS Scope

Widget 的脚本在浏览器环境中通过构造**安全闭包**的形式运行，主要依托 `new Function` 的动态解释器：

```javascript
const runScript = new Function("root", "ambient", "fetch", widget.js);
runScript(contentEl, ambient, customFetch);
```

### 注入参数与访问限制：

1.  **`root`**: 仅指向当前 Widget 的局部根 DOM 节点（非 React 模式下使用）。
    - _规范要求_：JS 内部进行 DOM 选择时，禁止使用全局 `document.querySelector`，而必须使用 `root.querySelector`。
    - _安全性_：这样使得该 Widget 无法查询和操作 Canvas 上其他卡片或系统 UI 的节点。
2.  **`ambient`**: 系统专门为该卡片构造的轻量 SDK 上下文。在 React 模式下，作为 props 传给 React 组件，提供 graph 读写、对话、全屏切换和 mcp 调用能力。
3.  **`fetch`**: 拦截型的代理请求。
    - 它重写了原生的 `window.fetch` 方法，默认开启 **5 分钟的本地内存缓存 (Fetch Cache)**。
    - 只有对外部第三方域名的 `GET` 请求会走该缓存。这避免了在 Widget 销毁后重建或在轮询中造成的重复 API 请求开销。

### React 模式下的编译与隔离：

对于 React JSX 模式，隔离机制通过模拟 CommonJS 环境来执行：
1. **ES 模块模拟**：前端构造了一个 Mock 模块加载器 `customRequire`，仅允许导入 `"react"` 和 `"./controller.js"`。
2. **独立编译域**：使用 `@babel/standalone` 动态转换 JSX 语法。控制器 `controller.js` 的 `useController` Hook 会在独立的闭包上下文中运行，然后与视图组件 `index.jsx` 结合，将潜在的脚本异常隔离在 `ErrorBoundary` 之中，以保障主页面可用性。

## 3. 全屏与布局生命周期安全

当 Widget 切换到全屏状态时，Ambient Agent **不会卸载 (Zero Remount)** 现有的 React DOM。它是通过 CSS 转换及 Canvas 布局节点平移实现瞬间无感全屏。这保证了 Widget JS 的内存闭包上下文、挂载的 EventListeners 以及所有输入状态 100% 完整保留。
