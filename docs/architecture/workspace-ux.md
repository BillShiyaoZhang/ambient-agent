# App-first workspace UX

Ambient Agent uses an app-first desktop workspace. Chat is an auxiliary surface and must never reduce the usable app viewport.

## Workspace states

- **Home**: when no app is open, App Center is rendered as the main surface.
- **Focused**: launching an app opens it maximized inside the product viewport. Other open apps stay mounted behind it.
- **Floating**: restoring a maximized app creates a movable, resizable window. A newly restored window is centered and offset from existing windows.
- **Snapped**: dropping a window on the left or right edge uses half the viewport; dropping on a corner uses a quarter. The top edge maximizes it.
- **Mobile**: below 720 px, only the active app is visible and fills the viewport. Window dragging, resizing, snapping and layout controls are hidden.

Closing a window removes it from the workspace without uninstalling the app. Closing the last app returns to Home. Switching chat sessions never changes the global workspace.

## System chrome

The workspace reserves one persistent system-chrome row above the app stage. The row is 52 CSS pixels high on desktop, begins after an 8 px visual inset and any `safe-area-inset-top`, and uses 12 px horizontal insets. Canvas V3 bounds remain normalized, but are resolved against the app stage below this row rather than the full browser viewport.

The desktop row has three zones:

- The leading zone owns the active maximized app's close/restore controls and title. Floating and snapped windows keep their own 44 px title bars.
- The geometrically centered island owns App Center, Tasks/Runs and the open-app switcher. It does not move when the leading or trailing zones change width.
- The trailing zone owns layout, audit, language and theme actions.

A maximized app does not render a second local title bar. Its content fills the stage while its window identity and controls move into system chrome. System chrome remains visible; maximize is not an auto-hiding presentation mode.

Window controls use a neutral resting appearance. Their visual dots may be small, but every desktop control has a 40 x 40 px hit target. Close and maximize colors appear only on hover or keyboard focus, and inactive floating windows are visually quieter than the active window.

### Responsive chrome

- At 1024 px and wider, all three zones are visible. The app switcher may scroll horizontally inside a bounded center island.
- From 720 through 1023 px, the center keeps App Center, Tasks/Runs and the current app. Layout, audit, language and theme move into one More menu.
- Below 720 px, the existing single-app behavior remains. The chrome exposes the active title and close action, uses 44 x 44 px targets, and moves all other workspace actions into More.
- Chrome and overlays honor all `env(safe-area-inset-*)` values.

## Window interaction contract

- Floating windows have a minimum size of 360 x 240 CSS pixels and retain at least 48 px of title bar inside the viewport.
- The title bar moves a floating window. Eight edge/corner handles resize it. Double-clicking the title bar toggles maximize.
- Dragging a maximized window restores it under the pointer. Holding Alt/Option disables snapping for that drag.
- The snap preview is shown before commit. Manual resize of a snapped window first restores it to floating mode.
- Pointer movement is applied through pointer capture and animation-frame visual updates. Persistent state is written only when the gesture ends.
- The open-app order is the z-order. Focusing a window moves its id to the end of the order.
- Layout commands are keyboard-accessible and include focus, side-by-side and adaptive grid.

## Auxiliary surfaces

- Chat is opened from a bottom-right floating action button into a 380 x 560 px anchored panel. It overlays rather than reflows the workspace.
- The chat header owns new-session and session-history controls. History is a scrollable popover with active/running states and delete actions.
- On small screens chat becomes a bottom sheet/full-screen drawer.
- Audit, language and theme controls live in a compact floating workspace toolbar.

All product-owned surfaces use the same system primitives and semantic tokens:

- `SystemIconButton` provides the accessible name, visible tooltip, focus ring, selected/expanded semantics and 40/44 px hit target.
- `SystemPopover` is mutually exclusive with other chrome popovers, closes on Escape or outside press and returns focus to its trigger.
- `SystemDrawer` and dismissible `SystemDialog` close on Escape or scrim press and return focus. Approval dialogs are blocking: they trap focus and require an explicit domain action rather than treating Escape or the scrim as a decision.
- `SystemToast` uses status color only for the status mark and action, not for the whole surface.

The layer order is fixed: windows, chrome, popovers, drawers/chat, dialogs, then toasts. Components must use layer tokens rather than local arbitrary z-index values.

## Canvas V3

`GET/POST /api/canvas` uses the global Canvas V3 contract:

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

Bounds are normalized to the app stage below system chrome. V1/V2 `pinned_ids` and `widget_spans` are accepted and migrated to floating V3 windows; the persisted Canvas V3 shape does not change.

## Theme and accessibility

- Theme preference is `system`, `light` or `dark`; `system` is the default and follows live OS changes.
- Liquid Glass uses semantic tokens for surfaces, text, borders, shadows and accents. Blur has an opaque fallback.
- The host and standard `ambient.components` follow the effective theme. Custom hard-coded widget colors are not rewritten.
- All controls expose accessible names, visible keyboard focus and 40 px minimum touch targets. Reduced-motion disables spring and transform animations.
- Mobile controls use a 44 px minimum target. Icon-only controls also expose a visible tooltip on hover and keyboard focus.
- System chrome is neutral in both themes. Purple is reserved for the current app, primary actions and running/unread state; danger and success colors are contextual rather than persistent decoration.
- Normal text meets WCAG AA contrast, focus is never conveyed by color alone, and translucent surfaces have an opaque fallback when backdrop filtering is unavailable.

## TDD acceptance map

| Requirement | Test contract |
| --- | --- |
| V2 canvas migration | `migrates legacy canvas spans to Canvas V3` |
| Window geometry | `clamps, snaps and tiles normalized windows` |
| App lifecycle | `keeps multiple apps mounted and returns home after the last close` |
| Persistence timing | `persists only after pointer interaction ends` |
| Floating chat | `opens chat without changing workspace dimensions` |
| Session titles | `generates, sanitizes and broadcasts an LLM session title` |
| Theme | `follows system theme until a manual preference is stored` |
| Maximized chrome | `moves maximized window controls into one system chrome row` |
| Floating chrome | `keeps a local title bar for non-maximized windows` |
| Chrome menus | `keeps one workspace menu open and restores trigger focus on Escape` |
| Responsive chrome | `resolves desktop, compact and mobile action visibility` |
| Blocking dialogs | `requires an explicit approval response and traps keyboard focus` |

Every behavior change updates this contract and its failing test before implementation.
