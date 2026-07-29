# Ambient Agent Frontend

The frontend is a React 19 + TypeScript + Vite app. It implements the App-first workspace, App Center, floating chat, task/runtime surfaces, LLM settings, and the React/HTM Widget host.

## Commands

```bash
npm install
npm run dev
npm run lint
npm run test
npm run build
```

The development server uses port 5173 and talks to the backend on port 8000 at the same hostname.

## Source map

- `src/App.tsx`: top-level session, Canvas, Widget, and dialog coordination.
- `src/components/AppWorkspace.tsx`: system chrome and window interactions.
- `src/components/AppCenter.tsx`: unified app/skill/MCP catalog.
- `src/components/SandboxWidget.tsx`: controller transpilation and `ambient` API injection.
- `src/components/TaskDrawer.tsx`: Run, interaction, and runtime UI.
- `src/services/`: Run, WebSocket, LLM, theme, and localization clients.
- `src/lib/windowManager.ts`: Canvas V3 migration and geometry.

See [the frontend architecture documentation](../docs/en/architecture/project-structure.md) and [Widget runtime boundary](../docs/en/widgets/sandbox.md).
