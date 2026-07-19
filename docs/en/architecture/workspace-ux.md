# Workspace UX

Ambient Agent uses an app-first desktop workspace. Chat is an auxiliary overlay and does not shrink the usable app area.

## Workspace states

- **Home**: when no app is open, App Center occupies the main surface.
- **Maximized**: launching an app maximizes it inside the product viewport by default; other open apps remain mounted behind it.
- **Floating**: restoring a maximized app creates a movable, resizable window.
- **Snapped**: dropping on the left or right edge uses half the stage, a corner uses one quarter, and the top edge maximizes.
- **Mobile**: below 720 px, only the active app is visible; drag, resize, snap, and layout controls are hidden.

Closing a window removes it from the workspace without uninstalling it. Closing the last app returns Home. Switching chat sessions does not alter the global workspace.

## System chrome and responsive layout

The desktop workspace reserves a 52 px system-chrome row above the app stage. A maximized app moves its close/restore controls and title into system chrome. Floating and snapped windows retain local title bars, so there is no duplicate title bar.

- At least 1024 px: show active-app information, the centered app/task switcher, and layout, audit, model, language, and theme actions.
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

## Auxiliary surfaces

- Chat opens from a bottom-right button as a 380 × 560 px overlay and becomes a bottom sheet/full-screen drawer on small screens.
- The Task Drawer shows active, attention, historical Runs, and runtimes. User confirmation happens in a blocking dialog.
- App Center is the Home surface with no windows and opens as an overlay when windows exist.
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
- Controls have accessible names, visible focus, and at least 40 px hit targets; mobile uses at least 44 px.
- Reduced motion disables spring/transform animations. Translucent materials have an opaque fallback without backdrop-filter support.
- Normal text targets WCAG AA contrast, and focus is never represented by color alone.

Core state migration and geometry algorithms live in `frontend/src/lib/windowManager.ts`; interactions live in `AppWorkspace.tsx`; coverage lives in `tests/frontend/window_manager.test.ts` and `workspace_ui.test.tsx`.
