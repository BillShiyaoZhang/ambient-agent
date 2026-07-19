import { describe, expect, it } from "vitest";
import {
  clampBounds,
  detectSnapZone,
  migrateCanvasConfig,
  snapBounds,
  tileWindows,
} from "../../frontend/src/lib/windowManager";

describe("App workspace window manager", () => {
  it("migrates legacy canvas spans to Canvas V3", () => {
    const migrated = migrateCanvasConfig({
      pinned_ids: ["weather", "tasks"],
      widget_spans: { weather: { cols: 6, rows: 5 } },
    });

    expect(migrated.version).toBe(3);
    expect(migrated.open_app_ids).toEqual(["weather", "tasks"]);
    expect(migrated.active_app_id).toBe("tasks");
    expect(migrated.windows.weather.mode).toBe("floating");
    expect(migrated.windows.tasks.bounds.width).toBeGreaterThan(0);
  });

  it("clamps, snaps and tiles normalized windows", () => {
    expect(detectSnapZone({ x: 3, y: 400 }, { width: 1200, height: 800 })).toBe("left");
    expect(detectSnapZone({ x: 1197, y: 3 }, { width: 1200, height: 800 })).toBe("top-right");
    expect(snapBounds("bottom-left")).toEqual({ x: 0, y: 0.5, width: 0.5, height: 0.5 });

    expect(clampBounds({ x: -2, y: 2, width: 0.1, height: 0.1 }, { width: 1200, height: 800 })).toEqual({
      x: 0,
      y: 0.7,
      width: 0.3,
      height: 0.3,
    });

    const tiled = tileWindows(["a", "b", "c"], "grid");
    expect(Object.keys(tiled)).toHaveLength(3);
    expect(tiled.a.mode).toBe("snapped");
    expect(tiled.c.bounds.y).toBe(0.5);
  });
});
