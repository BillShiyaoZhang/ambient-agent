# App-first workspace UX

Ambient Agent uses an app-first desktop workspace. Chat is an auxiliary surface and must never reduce the usable app viewport.

## Workspace states

- **Home**: when no app is open, App Center is rendered as the main surface.
- **Focused**: launching an app opens it maximized inside the product viewport. Other open apps stay mounted behind it.
- **Floating**: restoring a maximized app creates a movable, resizable window. A newly restored window is centered and offset from existing windows.
- **Snapped**: dropping a window on the left or right edge uses half the viewport; dropping on a corner uses a quarter. The top edge maximizes it.
- **Mobile**: below 720 px, only the active app is visible and fills the viewport. Window dragging, resizing, snapping and layout controls are hidden.

Closing a window removes it from the workspace without uninstalling the app. Closing the last app returns to Home. Switching chat sessions never changes the global workspace.

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

Bounds are normalized to the workspace viewport. V1/V2 `pinned_ids` and `widget_spans` are accepted and migrated to floating V3 windows.

## Theme and accessibility

- Theme preference is `system`, `light` or `dark`; `system` is the default and follows live OS changes.
- Liquid Glass uses semantic tokens for surfaces, text, borders, shadows and accents. Blur has an opaque fallback.
- The host and standard `ambient.components` follow the effective theme. Custom hard-coded widget colors are not rewritten.
- All controls expose accessible names, visible keyboard focus and 40 px minimum touch targets. Reduced-motion disables spring and transform animations.

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

Every behavior change updates this contract and its failing test before implementation.
