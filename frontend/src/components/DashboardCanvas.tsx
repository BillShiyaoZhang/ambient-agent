import React from "react";

export interface Widget {
  id: string;
  title: string;
  html: string;
  css: string;
  js: string;
}

interface DashboardCanvasProps {
  activeSessionId?: string | null;
  widgets: Widget[];
  onRemoveWidget: (id: string) => void;
  renderWidgetContent: (widget: Widget) => React.ReactNode;
  onOpenAudit?: () => void;
  onOpenAppStore?: () => void;
  onFullscreenWidget?: (id: string) => void;
  fullscreenAppId?: string | null;
  widgetSpans: WidgetSpans;
  onWidgetSpansChange: (spans: WidgetSpans) => void;
  showChat: boolean;
  onToggleChat: () => void;
}

interface WidgetSpans {
  [widgetId: string]: {
    cols: number;
    rows: number;
  };
}

export const DashboardCanvas: React.FC<DashboardCanvasProps> = ({
  activeSessionId: _activeSessionId = null,
  widgets,
  onRemoveWidget,
  renderWidgetContent,
  onOpenAudit = () => {},
  onOpenAppStore,
  onFullscreenWidget,
  fullscreenAppId = null,
  widgetSpans,
  onWidgetSpansChange,
  showChat,
  onToggleChat,
}) => {
  const handleResizeMouseDown = (
    e: React.MouseEvent,
    widgetId: string,
    startCols: number,
    startRows: number
  ) => {
    e.preventDefault();
    e.stopPropagation();

    const startX = e.clientX;
    const startY = e.clientY;

    const gridContainer = e.currentTarget.closest(".grid") as HTMLElement | null;
    if (!gridContainer) return;

    const rect = gridContainer.getBoundingClientRect();
    const containerWidth = rect.width;
    const computedStyle = window.getComputedStyle(gridContainer);
    
    const gapX = parseFloat(computedStyle.columnGap) || parseFloat(computedStyle.gap) || 16;
    const gapY = parseFloat(computedStyle.rowGap) || parseFloat(computedStyle.gap) || 16;

    const colUnit = (containerWidth + gapX) / 12;
    const rowUnit = 80 + gapY;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startX;
      const deltaY = moveEvent.clientY - startY;

      const addedCols = Math.round(deltaX / colUnit);
      const addedRows = Math.round(deltaY / rowUnit);

      const newCols = Math.max(2, Math.min(12, startCols + addedCols));
      const newRows = Math.max(1, Math.min(24, startRows + addedRows));

      const updated = {
        ...widgetSpans,
        [widgetId]: { cols: newCols, rows: newRows },
      };
      onWidgetSpansChange(updated);
    };

    const handleMouseUp = () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  };

  return (
    <div className="flex-1 h-full flex flex-col overflow-hidden bg-[#0a0712]/40">
      {/* Top Header */}
      <div className="p-4 border-b border-white/5 flex justify-between items-center bg-white/[0.01]">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white/90">
            Workspace Canvas
          </h1>
          <p className="text-xs text-white/40 mt-0.5">
            Widgets generated dynamically by your agent are pinned here.
          </p>
        </div>
        
        <div className="flex gap-2">
          <button
            onClick={onToggleChat}
            className={`px-3 py-1.5 text-xs font-semibold rounded-lg border flex items-center gap-1.5 transition-colors cursor-pointer ${
              showChat
                ? "bg-white/5 hover:bg-white/10 text-white/80 border-white/10"
                : "bg-purple-600/20 hover:bg-purple-600/35 text-purple-200 border-purple-500/20"
            }`}
            title={showChat ? "Hide Chat Panel" : "Show Chat Panel"}
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
            {showChat ? "Hide Chat" : "Show Chat"}
          </button>

          {onOpenAppStore && (
            <button
              onClick={onOpenAppStore}
              className="px-3 py-1.5 text-xs font-semibold bg-purple-600/20 hover:bg-purple-600/35 text-purple-200 rounded-lg border border-purple-500/20 flex items-center gap-1.5 transition-colors cursor-pointer"
            >
              <svg className="w-3.5 h-3.5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
              </svg>
              App Store
            </button>
          )}
          
          <button
            onClick={onOpenAudit}
            className="px-3 py-1.5 text-xs font-semibold bg-white/5 hover:bg-white/10 text-white/80 rounded-lg border border-white/10 flex items-center gap-1.5 transition-colors cursor-pointer"
          >
            <svg className="w-3.5 h-3.5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
            Audit Log
          </button>
        </div>
      </div>

      {/* Main Workspace Area */}
      <div className="flex-1 overflow-y-auto p-6">
        {widgets.length === 0 ? (
          <div className="h-full min-h-[300px] flex flex-col items-center justify-center border-2 border-dashed border-white/5 rounded-2xl p-8 text-center">
            <div className="w-12 h-12 rounded-full bg-purple-500/10 flex items-center justify-center mb-3">
              <svg
                className="w-6 h-6 text-purple-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"
                />
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-white/80">No active widgets</h3>
            <p className="text-xs text-white/40 mt-1 max-w-md">
              Ask your agent a question that benefits from a GUI—like checking weather, writing notes, or listing tasks.
            </p>
          </div>
        ) : (
          <div
            style={{ gridAutoRows: "80px" }}
            className="grid grid-cols-1 md:grid-cols-6 xl:grid-cols-12 gap-4"
          >
            {widgets.map((widget) => {
              const isFullscreen = widget.id === fullscreenAppId;
              const span = widgetSpans[widget.id] || { cols: 4, rows: 4 };
              return (
                <div
                  key={widget.id}
                  style={
                    isFullscreen
                      ? {}
                      : ({
                          "--widget-cols-xl": span.cols,
                          "--widget-cols-md": Math.min(6, span.cols),
                          "--widget-rows": span.rows,
                          height: "100%",
                        } as React.CSSProperties)
                  }
                  className={`glass-card overflow-hidden flex flex-col border border-white/5 transition-all duration-300 relative ${
                    isFullscreen
                      ? "fixed inset-0 z-50 w-screen h-screen max-w-none max-h-none rounded-none bg-[#07050d]"
                      : "widget-grid-item rounded-2xl relative group"
                  }`}
                >
                  {/* Widget Header */}
                  <div className={`flex items-center justify-between border-b border-white/5 bg-white/[0.01] ${
                    isFullscreen ? "px-6 py-4" : "px-4 py-3"
                  }`}>
                    {isFullscreen ? (
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-xl bg-purple-500/20 flex items-center justify-center border border-purple-500/30">
                          <svg className="w-4.5 h-4.5 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                          </svg>
                        </div>
                        <div>
                          <h2 className="text-lg font-bold text-white/90 leading-tight">
                            {widget.title}
                          </h2>
                          <p className="text-[10px] text-purple-400 font-semibold uppercase tracking-wider mt-0.5">
                            Running App: {widget.id}
                          </p>
                        </div>
                      </div>
                    ) : (
                      <span className="text-xs font-semibold text-purple-300 tracking-wider uppercase truncate max-w-[65%]">
                        {widget.title}
                      </span>
                    )}
                    
                    <div className="flex gap-1.5 shrink-0">
                      {onFullscreenWidget && (
                        <button
                          onClick={() => onFullscreenWidget(isFullscreen ? "" : widget.id)}
                          className={`flex items-center gap-1.5 hover:bg-white/10 rounded text-white/40 hover:text-white/80 transition-colors cursor-pointer ${
                            isFullscreen ? "px-4 py-2 text-xs font-semibold bg-white/5 border border-white/10 rounded-xl" : "p-1"
                          }`}
                          title={isFullscreen ? "Exit Fullscreen" : "Fullscreen App"}
                        >
                          {isFullscreen ? (
                            <>
                              <svg className="w-4 h-4 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                              </svg>
                              Exit Fullscreen
                            </>
                          ) : (
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
                            </svg>
                          )}
                        </button>
                      )}
                      
                      {!isFullscreen && (
                        <button
                          onClick={() => onRemoveWidget(widget.id)}
                          className="p-1 hover:bg-white/10 rounded text-white/40 hover:text-white/80 transition-colors cursor-pointer"
                          title="Remove Widget"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Widget Content Renderer */}
                  <div className={`flex-1 overflow-auto bg-black/10 relative ${
                    isFullscreen ? "p-8 flex items-center justify-center bg-black/20" : "p-4"
                  }`}>
                    <div className={
                      isFullscreen 
                        ? "glass w-full h-full max-w-6xl max-h-[85vh] rounded-3xl p-8 border border-white/5 shadow-2xl flex flex-col overflow-hidden"
                        : "w-full h-full"
                    }>
                      {renderWidgetContent(widget)}
                    </div>
                  </div>

                  {/* Resize Handle */}
                  {!isFullscreen && (
                    <div
                      onMouseDown={(e) => handleResizeMouseDown(e, widget.id, span.cols, span.rows)}
                      className="absolute bottom-1 right-1 w-4 h-4 cursor-se-resize flex items-end justify-end p-0.5 text-white/30 hover:text-white/70 select-none z-10"
                      title="Drag to resize widget"
                    >
                      <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19 19L5 5M19 11v8h-8" />
                      </svg>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
