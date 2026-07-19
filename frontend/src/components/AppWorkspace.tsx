import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  AppWindow,
  ChevronDown,
  Grid2X2,
  Languages,
  Maximize2,
  Minimize2,
  Moon,
  PanelLeft,
  Rows3,
  ShieldCheck,
  Store,
  Sun,
  X,
} from "lucide-react";
import type { ThemePreference, ThemeSnapshot } from "../services/theme";
import {
  boundsToPixels,
  defaultFloatingBounds,
  detectSnapZone,
  pixelsToBounds,
  snapBounds,
  tileWindows,
  type AppWindowState,
  type CanvasConfigV3,
  type SnapZone,
} from "../lib/windowManager";
import type { Widget } from "./DashboardCanvas";
import "./Workspace.css";

interface AppWorkspaceProps {
  widgets: Widget[];
  canvas: CanvasConfigV3;
  onCanvasChange: (canvas: CanvasConfigV3, persist?: boolean) => void;
  renderWidgetContent: (widget: Widget) => React.ReactNode;
  onOpenAppStore: () => void;
  onOpenAudit: () => void;
  language: "zh" | "en";
  onLanguageChange: (language: "zh" | "en") => void;
  theme: ThemeSnapshot;
  onThemeChange: (preference: ThemePreference) => void;
}

type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const RESIZE_EDGES: ResizeEdge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

export const AppWorkspace: React.FC<AppWorkspaceProps> = ({
  widgets,
  canvas,
  onCanvasChange,
  renderWidgetContent,
  onOpenAppStore,
  onOpenAudit,
  language,
  onLanguageChange,
  theme,
  onThemeChange,
}) => {
  const isZh = language === "zh";
  const rootRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<number | null>(null);
  const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight });
  const [snapPreview, setSnapPreview] = useState<SnapZone | "maximize" | null>(null);
  const [layoutOpen, setLayoutOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    if (typeof ResizeObserver === "undefined") {
      setViewport({ width: root.clientWidth || window.innerWidth, height: root.clientHeight || window.innerHeight });
      return;
    }
    const observer = new ResizeObserver(([entry]) => {
      setViewport({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  useEffect(() => () => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
  }, []);

  const widgetMap = useMemo(() => new Map(widgets.map((widget) => [widget.id, widget])), [widgets]);
  const activeId = canvas.active_app_id ?? canvas.open_app_ids.at(-1) ?? null;

  const updateCanvas = (next: CanvasConfigV3, persist = false) => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    if (persist) {
      frameRef.current = null;
      onCanvasChange(next, true);
      return;
    }
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      onCanvasChange(next, false);
    });
  };

  const focusWindow = (id: string, persist = false) => {
    const nextOrder = [...canvas.open_app_ids.filter((appId) => appId !== id), id];
    updateCanvas({ ...canvas, open_app_ids: nextOrder, active_app_id: id }, persist);
  };

  const patchWindow = (id: string, patch: Partial<AppWindowState>, persist = false) => {
    updateCanvas({
      ...canvas,
      active_app_id: id,
      open_app_ids: [...canvas.open_app_ids.filter((appId) => appId !== id), id],
      windows: { ...canvas.windows, [id]: { ...canvas.windows[id], ...patch } },
    }, persist);
  };

  const closeWindow = (id: string) => {
    const ids = canvas.open_app_ids.filter((appId) => appId !== id);
    const windows = { ...canvas.windows };
    delete windows[id];
    updateCanvas({ ...canvas, open_app_ids: ids, active_app_id: ids.at(-1) ?? null, windows }, true);
  };

  const toggleMaximize = (id: string) => {
    const current = canvas.windows[id];
    if (!current) return;
    if (current.mode === "maximized") {
      patchWindow(id, { mode: "floating", bounds: current.restoreBounds ?? defaultFloatingBounds(canvas.open_app_ids.indexOf(id)) }, true);
    } else {
      patchWindow(id, { mode: "maximized", restoreBounds: current.bounds, snapZone: undefined }, true);
    }
  };

  const beginMove = (event: React.PointerEvent, id: string) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const element = event.currentTarget as HTMLElement;
    element.setPointerCapture(event.pointerId);
    const current = canvas.windows[id];
    if (!current) return;
    const original = current.mode === "maximized"
      ? current.restoreBounds ?? defaultFloatingBounds(canvas.open_app_ids.indexOf(id))
      : current.bounds;
    const originalPx = boundsToPixels(original, viewport);
    const start = { x: event.clientX, y: event.clientY };
    const anchor = current.mode === "maximized" ? Math.min(0.85, Math.max(0.15, event.clientX / viewport.width)) : 0;
    let latest = original;
    let preview: SnapZone | "maximize" | null = null;

    if (current.mode === "maximized") {
      latest = pixelsToBounds({ ...originalPx, x: event.clientX - originalPx.width * anchor, y: 8 }, viewport);
      patchWindow(id, { mode: "floating", bounds: latest, restoreBounds: latest }, false);
    } else {
      focusWindow(id, false);
    }

    const handleMove = (moveEvent: PointerEvent) => {
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      latest = pixelsToBounds({ ...originalPx, x: originalPx.x + dx, y: originalPx.y + dy }, viewport);
      preview = moveEvent.altKey ? null : detectSnapZone({ x: moveEvent.clientX, y: moveEvent.clientY }, viewport);
      setSnapPreview(preview);
      patchWindow(id, { mode: "floating", bounds: latest, snapZone: undefined }, false);
    };
    const handleUp = () => {
      element.removeEventListener("pointermove", handleMove);
      element.removeEventListener("pointerup", handleUp);
      element.removeEventListener("pointercancel", handleUp);
      setSnapPreview(null);
      if (preview === "maximize") {
        patchWindow(id, { mode: "maximized", bounds: latest, restoreBounds: latest, snapZone: undefined }, true);
      } else if (preview) {
        patchWindow(id, { mode: "snapped", bounds: snapBounds(preview), restoreBounds: latest, snapZone: preview }, true);
      } else {
        patchWindow(id, { mode: "floating", bounds: latest, restoreBounds: latest, snapZone: undefined }, true);
      }
    };
    element.addEventListener("pointermove", handleMove);
    element.addEventListener("pointerup", handleUp);
    element.addEventListener("pointercancel", handleUp);
  };

  const beginResize = (event: React.PointerEvent, id: string, edge: ResizeEdge) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const element = event.currentTarget as HTMLElement;
    element.setPointerCapture(event.pointerId);
    const current = canvas.windows[id];
    if (!current) return;
    const base = current.mode === "floating" ? current.bounds : current.restoreBounds ?? defaultFloatingBounds(canvas.open_app_ids.indexOf(id));
    const startPx = boundsToPixels(base, viewport);
    const start = { x: event.clientX, y: event.clientY };
    let latest = base;
    patchWindow(id, { mode: "floating", bounds: base, snapZone: undefined }, false);

    const handleMove = (moveEvent: PointerEvent) => {
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      const next = { ...startPx };
      if (edge.includes("e")) next.width += dx;
      if (edge.includes("s")) next.height += dy;
      if (edge.includes("w")) { next.x += dx; next.width -= dx; }
      if (edge.includes("n")) { next.y += dy; next.height -= dy; }
      latest = pixelsToBounds(next, viewport);
      patchWindow(id, { mode: "floating", bounds: latest, restoreBounds: latest }, false);
    };
    const handleUp = () => {
      element.removeEventListener("pointermove", handleMove);
      element.removeEventListener("pointerup", handleUp);
      element.removeEventListener("pointercancel", handleUp);
      patchWindow(id, { mode: "floating", bounds: latest, restoreBounds: latest }, true);
    };
    element.addEventListener("pointermove", handleMove);
    element.addEventListener("pointerup", handleUp);
    element.addEventListener("pointercancel", handleUp);
  };

  const applyLayout = (preset: "focus" | "side-by-side" | "grid") => {
    const tiled = tileWindows(canvas.open_app_ids, preset, activeId);
    const windows = preset === "focus" ? { ...canvas.windows, ...tiled } : tiled;
    updateCanvas({ ...canvas, windows, active_app_id: activeId }, true);
    setLayoutOpen(false);
  };

  const previewStyle = snapPreview && snapPreview !== "maximize"
    ? snapBounds(snapPreview)
    : snapPreview === "maximize" ? { x: 0, y: 0, width: 1, height: 1 } : null;

  return (
    <div ref={rootRef} className="workspace-root" data-testid="app-workspace">
      <div className="workspace-wallpaper" />
      {previewStyle && (
        <div className="workspace-snap-preview" style={{
          left: `${previewStyle.x * 100}%`, top: `${previewStyle.y * 100}%`,
          width: `${previewStyle.width * 100}%`, height: `${previewStyle.height * 100}%`,
        }} />
      )}

      {canvas.open_app_ids.map((id, zIndex) => {
        const widget = widgetMap.get(id);
        const state = canvas.windows[id];
        if (!widget || !state) return null;
        const pixels = boundsToPixels(state.bounds, viewport);
        const maximized = state.mode === "maximized";
        return (
          <section
            key={id}
            className={`app-window ${maximized ? "is-maximized" : ""} ${id === activeId ? "is-active" : ""}`}
            data-window-id={id}
            style={maximized ? { zIndex: zIndex + 2 } : {
              zIndex: zIndex + 2,
              width: pixels.width,
              height: pixels.height,
              transform: `translate3d(${pixels.x}px, ${pixels.y}px, 0)`,
            }}
            onPointerDown={() => focusWindow(id, false)}
          >
            <header className="app-window-titlebar" onPointerDown={(event) => beginMove(event, id)} onDoubleClick={() => toggleMaximize(id)}>
              <div className="app-window-traffic" aria-label={isZh ? "窗口控制" : "Window controls"}>
                <button className="is-close" onPointerDown={(event) => event.stopPropagation()} onClick={() => closeWindow(id)} aria-label={isZh ? "关闭应用" : "Close app"}><X size={12} /></button>
                <button onPointerDown={(event) => event.stopPropagation()} onClick={() => toggleMaximize(id)} aria-label={maximized ? (isZh ? "恢复窗口" : "Restore window") : (isZh ? "最大化" : "Maximize")}>{maximized ? <Minimize2 size={11} /> : <Maximize2 size={11} />}</button>
              </div>
              <div className="app-window-title"><AppWindow size={14} /><span>{widget.title}</span></div>
              <div className="app-window-title-spacer" />
            </header>
            <div className="app-window-content">{renderWidgetContent(widget)}</div>
            {!maximized && RESIZE_EDGES.map((edge) => (
              <div key={edge} className={`app-window-resize resize-${edge}`} onPointerDown={(event) => beginResize(event, id, edge)} />
            ))}
          </section>
        );
      })}

      <nav className="workspace-toolbar" aria-label={isZh ? "工作区工具栏" : "Workspace toolbar"}>
        <button onClick={onOpenAppStore} aria-label={isZh ? "打开应用中心" : "Open App Center"}><Store size={18} /></button>
        <div className="workspace-toolbar-divider" />
        <div className="workspace-app-switcher">
          {canvas.open_app_ids.map((id) => {
            const widget = widgetMap.get(id);
            return widget ? <button key={id} className={id === activeId ? "is-active" : ""} onClick={() => focusWindow(id, true)} title={widget.title}><span>{widget.title.slice(0, 1).toUpperCase()}</span></button> : null;
          })}
        </div>
        <div className="workspace-toolbar-divider" />
        <div className="workspace-menu-anchor">
          <button onClick={() => setLayoutOpen((value) => !value)} aria-expanded={layoutOpen} aria-label={isZh ? "布局" : "Layout"}><Grid2X2 size={17} /><ChevronDown size={11} /></button>
          {layoutOpen && <div className="workspace-popover layout-popover">
            <button onClick={() => applyLayout("focus")}><PanelLeft size={15} />{isZh ? "聚焦当前 App" : "Focus current app"}</button>
            <button onClick={() => applyLayout("side-by-side")}><Rows3 size={15} />{isZh ? "左右排列" : "Side by side"}</button>
            <button onClick={() => applyLayout("grid")}><Grid2X2 size={15} />{isZh ? "自动网格" : "Adaptive grid"}</button>
          </div>}
        </div>
        <button onClick={onOpenAudit} aria-label={isZh ? "审计日志" : "Audit log"}><ShieldCheck size={17} /></button>
        <button onClick={() => onLanguageChange(language === "zh" ? "en" : "zh")} aria-label={isZh ? "切换为英文" : "Switch to Chinese"}><Languages size={17} /></button>
        <div className="workspace-menu-anchor">
          <button onClick={() => setThemeOpen((value) => !value)} aria-expanded={themeOpen} aria-label={isZh ? "主题" : "Theme"}>{theme.effective === "dark" ? <Moon size={17} /> : <Sun size={17} />}</button>
          {themeOpen && <div className="workspace-popover theme-popover">
            {(["system", "light", "dark"] as ThemePreference[]).map((preference) => <button key={preference} className={theme.preference === preference ? "is-selected" : ""} onClick={() => { onThemeChange(preference); setThemeOpen(false); }}>{preference === "system" ? (isZh ? "跟随系统" : "System") : preference === "light" ? (isZh ? "浅色" : "Light") : (isZh ? "深色" : "Dark")}</button>)}
          </div>}
        </div>
      </nav>
    </div>
  );
};
