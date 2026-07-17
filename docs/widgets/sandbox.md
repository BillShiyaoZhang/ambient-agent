# 沙箱隔离与动态编译机制

由于 Ambient Agent 的 Widget 是由大模型动态生成的，前端在 `frontend/src/components/SandboxWidget.tsx` 中实现了一套严格的**运行沙箱与动态编译隔离**，以保障系统稳定性、样式自治和逻辑安全。

## 1. 脚本与运行沙箱 (JS Sandbox)

Widget 并不以传统 script 标签或 iframe 形式加载，而是在浏览器环境中通过构造**安全闭包**运行，主要依托 `new Function` 隔离其顶层作用域：

```javascript
const runScript = new Function("exports", "React", "ambient", transpileScript);
runScript(exportsObj, React, ambientProps);
```

### 注入参数与接口访问限制：

1. **`exports`**: 提供 CommonJS 规范的模块导出容器。动态脚本在其中注册并导出自身的 default 组件。
2. **`React`**: 显式注入的 React 全局变量，包含 `useState`, `useEffect`, `useMemo` 等常用 Hook 的访问。
3. **`ambient`**: 系统专门为该 Widget 构造的隔离 SDK 实例。包含：
   - `ambient.react`: 安全访问受控的 React Hooks。
   - `ambient.components`: 注入的 Tailwind-styled 基础 UI 组件库（Card, Button, TextField 等）。
   - `ambient.html`: 提供 HTM（Hyperscript Tagged Markup）动态渲染模板能力。
   - `ambient.graph`: 仅限与图数据库进行安全的数据订阅 and Mutation 交互。
   - `ambient.mcp`: 只能调用受控的 MCP Tool。

这种基于闭包形参的命名空间拦截，阻断了 Widget 代码对宿主全局变量或非授权对象的直接侵入。

## 2. 动态 React + HTM 编译 (On-the-Fly Transpilation)

前端卡片并不存在构建打包期，所有的渲染逻辑都是在挂载时动态转译的：
1. **转译引擎**：借助前端运行的 `@babel/standalone` 对 Widget 脚本执行实时编译，解析 JSX、组件以及 ES 模块导出语法。
2. **错误屏障 (Error Boundary)**：转译后的脚本执行以及组件渲染均被包裹在 React 的 `ErrorBoundary` 之中。一旦发生任何语法转译报错、逻辑运行时 crash 或网络请求异常，均只会在卡片局部展示错误报告，绝不会影响主画布（Canvas）和侧边栏的渲染。

## 3. 全屏与布局生命周期安全 (Zero Remount)

当卡片在网格布局中切换全屏时，系统**不会卸载并重新挂载（Zero Remount）**现有的 React DOM。
这是通过 CSS Transforms 和画布视口（Canvas Container）平移技术实现的，能够瞬间实现无感全屏切换。这不仅带来了绝佳的微动画交互体验，更 100% 完整保留了 Widget 内存中的 React State 状态、WebSocket 数据订阅监听器以及当前输入框的聚焦状态。
