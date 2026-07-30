# Workspace UX

Ambient Agent uses an app-first desktop workspace. Chat is an auxiliary overlay and does not shrink the usable app area.

## Workspace states

- **Home**: when no app is open, App Center occupies the main surface.
- **Maximized**: launching an app maximizes it inside the product viewport by default; other open apps remain workspace windows, while their runtimes follow the lifecycle below.
- **Floating**: restoring a maximized app creates a movable, resizable window.
- **Snapped**: dropping on the left or right edge uses half the stage, a corner uses one quarter, and the top edge maximizes.
- **Mobile**: below 720 px, only the active app is visible; drag, resize, snap, and layout controls are hidden.

Closing a window removes it from the workspace without uninstalling it. Closing the last app returns Home. Switching chat sessions does not alter the global workspace.

## System chrome and responsive layout

The desktop workspace reserves a 52 px system-chrome row above the app stage. A maximized app moves its close/restore controls and title into system chrome. Floating and snapped windows retain local title bars, so there is no duplicate title bar.

- At least 1024 px: show active-app information, the centered app/task switcher, and layout, Graph Explorer, audit, model, language, and theme actions.
- 720–1023 px: keep core switcher actions and move the rest into More.
- Below 720 px: the active app fills the stage, system chrome keeps only its title and close action, and all other actions move into More.
- Chrome and overlays honor `safe-area-inset-*`.

## Window interaction contract

- Floating windows have a minimum size of 360 × 240 CSS px and keep at least 48 px of title bar within the viewport.
- The title bar moves a window, eight edge/corner handles resize it, and double-click toggles maximize.
- Dragging a maximized window restores it under the pointer. Holding Alt/Option temporarily disables snapping.
- A snap preview appears before pointer release. Manually resizing a snapped window first restores floating mode.
- Pointer movement uses pointer capture and animation-frame updates. Persistence happens only when the gesture ends.
- `open_app_ids` is also the window z-order. Focusing a window moves its id to the end.
- Layout presets include focus, side by side, and adaptive grid.

## Widget runtime lifecycle

The number of open apps is independent from the number of resident Widget runtimes. The workspace derives four ephemeral states from `active_app_id` and the most-recently-used order in `open_app_ids`; these states are not persisted in Canvas:

- **Active**: the focused app. Its iframe, WebSocket, or Pixel Runtime context stays mounted and interactive.
- **Warm**: the one most recently used inactive app. At most one warm runtime is retained for a fast switch; no other inactive app keeps a runtime. A warm Pixel Widget must send `visibility=false` and stop its screencast even while the browser tab remains visible.
- **Suspending**: after the former Warm app exceeds the resident budget, at most one outgoing Runtime remains mounted. The fixed Frame receives `before_suspend` over its authenticated MessagePort and invokes the Controller's `ambient.lifecycle.onBeforeSuspend` handler, where a Widget can await its last `ambient.storage` write. The host unmounts after the acknowledgement or a one-second timeout, with a 1.25-second workspace fallback. Steady state therefore has at most two resident Runtimes and the bounded flush window at most three. Rapid successive switches replace the older outgoing request with the latest one rather than accumulating Runtimes without bound. The legacy Pixel rollback path has no storage protocol; it stops visibility and releases immediately.
- **Suspended**: every other open app. The workspace retains window position, mode, and z-order, but unmounts Widget content so its iframe, WebSocket, and Pixel Runtime context are destroyed. The content area shows a clickable resume placeholder. Selecting it or the app switcher restores that app as Active, moves the former Active to Warm, and suspends the former Warm when the budget is exceeded.

Input events inside a warm iframe do not bubble across its browsing context, so
the fixed Frame must report trusted `pointerdown`, `keydown`, and `wheel` user
activations to the host over the authenticated MessagePort. Only events marked
`isTrusted` by the browser may produce this internal notification; neither
Controller-synthesized events nor the public `hostEvent` API can forge it. The
host immediately promotes that window to Active, updates MRU, and persists the
Canvas. A user's first mouse, keyboard, or wheel interaction with a warm app
therefore cannot remain misclassified as background work or cause the just-used
app to be suspended when a third window is selected.

Pixel Runtime initiating `pointerdown`, `wheel`, and `keydown` input must also
pass through the same host activation entry point before it is forwarded to
the Runtime. A plain `pointermove` must not create focus-follows-mouse behavior
or a Canvas write. An app that is already the canonical Active/MRU entry causes
no redundant Canvas write; changing only the `onActivate` callback reference
must not reconnect its Pixel WebSocket, and Warm-to-Active promotion must reuse
that connection.

If the persisted `active_app_id` is temporarily unavailable or lacks window
state, the workspace transiently falls back to the most recent renderable window
in `open_app_ids` as Active and selects Warm from the remaining order. Rendering
this resilience state alone does not rewrite the persisted Canvas. On the
user's first genuine interaction with that derived Active app, the host moves
it to the MRU tail, writes `active_app_id`, and persists the repaired Canvas.

Canvas bootstrap fetches snapshots for open apps concurrently. Those snapshots
must merge through a functional state update keyed by app `id` and
`manifest_revision`. If a WebSocket update for the same app arrives while a
snapshot is pending, the current event version wins: the late bootstrap response
must neither overwrite it nor create a duplicate app.

Suspension is neither closing nor uninstalling: the app remains in `open_app_ids`, and its window geometry is preserved. Closing still only removes the app from the workspace; refocusing a suspended app rebuilds its runtime through the normal startup path.

Suspended means a recoverable reload after releasing the Runtime, not a
lossless browser freeze: unmounting destroys arbitrary React hook memory.
Generated Apps must hydrate user drafts, form values, editor content, and any
other work that must survive suspension from `ambient.storage`, then write
through after every meaningful change. Canonical Graph/File data continues to
use its approved capability. A debounced writer must register an async
`ambient.lifecycle.onBeforeSuspend` handler and await the latest persistence
write; this bounded handshake is a safety net, not an indefinite durability
promise. Only transient presentation state such as hover
or an open menu may reset. A legacy App that keeps user input only in memory
can reset on resume and must upgrade its persistence logic; the Active/Warm
budget is not a generic state-snapshot mechanism.

## Auxiliary surfaces

- Chat opens from a bottom-right button as a 380 × 560 px overlay and becomes a bottom sheet/full-screen drawer on small screens.
- The Task Drawer shows active, attention, historical Runs, and runtimes. User confirmation happens in a blocking dialog.
- App Center is the Home surface with no windows and opens as an overlay when windows exist.
- The Graph Explorer workbench reuses one interactive graph component for Ontology, KG, Agent orchestration, and the privacy data map. It is a trusted-Host auxiliary surface rather than an installed generated App.
- Audit logs, model settings, and system menus use shared `SystemDialog`, `SystemDrawer`, `SystemPopover`, and `SystemIconButton` primitives.

Popovers support Escape, outside press, and focus return. Approval dialogs are blocking: Escape or a scrim press is not interpreted as approval or rejection.

## Canvas V3

`GET /api/canvas` and `POST /api/canvas` use the global Canvas V3 contract:

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

`bounds` are normalized against the app stage below system chrome. Legacy V1/V2 `pinned_ids` and `widget_spans` migrate to V3 floating windows; current persistence writes only V3.

## Theme and accessibility

- Theme preference is `system`, `light`, or `dark`. `system` is the default and follows live OS changes.
- The host and standard `ambient.components` follow the effective theme. Hard-coded custom Widget colors are not rewritten.
- Widgets receive `{ theme, locale, reducedMotion }` presentation context over the same Runtime session; theme or language changes do not recreate the default iframe. Pixel rollback likewise reuses its existing Chromium BrowserContext.
- Controls have accessible names, visible focus, and at least 40 px hit targets; mobile uses at least 44 px.
- Host tooltips use a light surface with dark text in the light theme; they must not render as a theme-breaking black box.
- Chat-history rows use a compact 28 px visual delete button that is shown only on row hover, focus-within, or the active row, preserving the truncatable title column; its accessible name remains complete.
- Reduced motion disables spring/transform animations. Translucent materials have an opaque fallback without backdrop-filter support.
- Normal text targets WCAG AA contrast, and focus is never represented by color alone.

Core state migration and geometry algorithms live in `frontend/src/lib/windowManager.ts`; interactions live in `AppWorkspace.tsx`; coverage lives in `tests/frontend/window_manager.test.ts` and `workspace_ui.test.tsx`.
