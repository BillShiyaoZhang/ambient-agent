export type WindowMode = "maximized" | "floating" | "snapped";
export type SnapZone = "left" | "right" | "top-left" | "top-right" | "bottom-left" | "bottom-right";
export type LayoutPreset = "focus" | "side-by-side" | "grid";

export interface NormalizedBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface AppWindowState {
  mode: WindowMode;
  bounds: NormalizedBounds;
  restoreBounds?: NormalizedBounds;
  snapZone?: SnapZone;
}

export interface CanvasConfigV3 {
  version: 3;
  open_app_ids: string[];
  active_app_id: string | null;
  windows: Record<string, AppWindowState>;
}

export interface ViewportSize {
  width: number;
  height: number;
}

export const DEFAULT_BOUNDS: NormalizedBounds = { x: 0.16, y: 0.12, width: 0.68, height: 0.72 };
export const EMPTY_CANVAS: CanvasConfigV3 = { version: 3, open_app_ids: [], active_app_id: null, windows: {} };

const finite = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

export function clampBounds(bounds: Partial<NormalizedBounds>, viewport: ViewportSize): NormalizedBounds {
  const minWidth = Math.min(1, Math.max(0.3, 360 / Math.max(1, viewport.width)));
  const minHeight = Math.min(1, Math.max(0.3, 240 / Math.max(1, viewport.height)));
  const width = Math.min(1, Math.max(minWidth, finite(bounds.width, DEFAULT_BOUNDS.width)));
  const height = Math.min(1, Math.max(minHeight, finite(bounds.height, DEFAULT_BOUNDS.height)));
  const x = Math.min(1 - width, Math.max(0, finite(bounds.x, DEFAULT_BOUNDS.x)));
  const y = Math.min(1 - height, Math.max(0, finite(bounds.y, DEFAULT_BOUNDS.y)));
  return { x, y, width, height };
}

export function snapBounds(zone: SnapZone): NormalizedBounds {
  const map: Record<SnapZone, NormalizedBounds> = {
    left: { x: 0, y: 0, width: 0.5, height: 1 },
    right: { x: 0.5, y: 0, width: 0.5, height: 1 },
    "top-left": { x: 0, y: 0, width: 0.5, height: 0.5 },
    "top-right": { x: 0.5, y: 0, width: 0.5, height: 0.5 },
    "bottom-left": { x: 0, y: 0.5, width: 0.5, height: 0.5 },
    "bottom-right": { x: 0.5, y: 0.5, width: 0.5, height: 0.5 },
  };
  return map[zone];
}

export function detectSnapZone(
  point: { x: number; y: number },
  viewport: ViewportSize,
  threshold = 28,
): SnapZone | "maximize" | null {
  const left = point.x <= threshold;
  const right = point.x >= viewport.width - threshold;
  const top = point.y <= threshold;
  const bottom = point.y >= viewport.height - threshold;
  if (top && left) return "top-left";
  if (top && right) return "top-right";
  if (bottom && left) return "bottom-left";
  if (bottom && right) return "bottom-right";
  if (top) return "maximize";
  if (left) return "left";
  if (right) return "right";
  return null;
}

export function defaultFloatingBounds(index: number): NormalizedBounds {
  const offset = Math.min(index * 0.025, 0.16);
  return clampBounds(
    { ...DEFAULT_BOUNDS, x: DEFAULT_BOUNDS.x + offset, y: DEFAULT_BOUNDS.y + offset },
    { width: 1440, height: 900 },
  );
}

export function migrateCanvasConfig(raw: unknown): CanvasConfigV3 {
  if (!raw || typeof raw !== "object") return { ...EMPTY_CANVAS, windows: {} };
  const input = raw as Record<string, unknown>;
  const v3 = input.version === 3 || Array.isArray(input.open_app_ids);
  const idsInput = (v3 ? input.open_app_ids : input.pinned_ids) as unknown;
  const open_app_ids = Array.isArray(idsInput)
    ? idsInput.filter((id, index, values): id is string => typeof id === "string" && Boolean(id) && values.indexOf(id) === index)
    : [];
  const sourceWindows = input.windows && typeof input.windows === "object" ? input.windows as Record<string, any> : {};
  const spans = input.widget_spans && typeof input.widget_spans === "object" ? input.widget_spans as Record<string, any> : {};
  const windows: Record<string, AppWindowState> = {};

  open_app_ids.forEach((id, index) => {
    const candidate = sourceWindows[id];
    if (candidate && typeof candidate === "object") {
      const mode: WindowMode = ["maximized", "floating", "snapped"].includes(candidate.mode)
        ? candidate.mode
        : "maximized";
      windows[id] = {
        mode,
        bounds: clampBounds(candidate.bounds ?? {}, { width: 1440, height: 900 }),
        ...(candidate.restoreBounds ? { restoreBounds: clampBounds(candidate.restoreBounds, { width: 1440, height: 900 }) } : {}),
        ...(candidate.snapZone ? { snapZone: candidate.snapZone as SnapZone } : {}),
      };
      return;
    }
    const span = spans[id];
    const bounds = span
      ? clampBounds(
          { ...defaultFloatingBounds(index), width: Math.max(4, Math.min(12, span.cols ?? 8)) / 12, height: Math.max(4, Math.min(12, span.rows ?? 8)) / 12 },
          { width: 1440, height: 900 },
        )
      : defaultFloatingBounds(index);
    windows[id] = { mode: v3 ? "maximized" : "floating", bounds };
  });

  const requestedActive = typeof input.active_app_id === "string" ? input.active_app_id : null;
  return {
    version: 3,
    open_app_ids,
    active_app_id: requestedActive && open_app_ids.includes(requestedActive) ? requestedActive : open_app_ids.at(-1) ?? null,
    windows,
  };
}

export function tileWindows(ids: string[], preset: LayoutPreset, activeId?: string | null): Record<string, AppWindowState> {
  const result: Record<string, AppWindowState> = {};
  if (preset === "focus") {
    const id = activeId && ids.includes(activeId) ? activeId : ids.at(-1);
    if (id) result[id] = { mode: "maximized", bounds: DEFAULT_BOUNDS };
    return result;
  }
  const columns = preset === "side-by-side" ? Math.max(1, ids.length) : Math.ceil(Math.sqrt(ids.length));
  const rows = Math.max(1, Math.ceil(ids.length / columns));
  ids.forEach((id, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    result[id] = {
      mode: "snapped",
      bounds: { x: column / columns, y: row / rows, width: 1 / columns, height: 1 / rows },
    };
  });
  return result;
}

export function boundsToPixels(bounds: NormalizedBounds, viewport: ViewportSize) {
  return {
    x: Math.round(bounds.x * viewport.width),
    y: Math.round(bounds.y * viewport.height),
    width: Math.round(bounds.width * viewport.width),
    height: Math.round(bounds.height * viewport.height),
  };
}

export function pixelsToBounds(
  pixels: { x: number; y: number; width: number; height: number },
  viewport: ViewportSize,
): NormalizedBounds {
  return clampBounds(
    {
      x: pixels.x / viewport.width,
      y: pixels.y / viewport.height,
      width: pixels.width / viewport.width,
      height: pixels.height / viewport.height,
    },
    viewport,
  );
}
