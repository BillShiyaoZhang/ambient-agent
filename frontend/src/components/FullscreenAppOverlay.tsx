import React from "react";
import type { Widget } from "./DashboardCanvas";
import { SandboxWidget } from "./SandboxWidget";

interface FullscreenAppOverlayProps {
  widget: Widget | null;
  onClose: () => void;
}

export const FullscreenAppOverlay: React.FC<FullscreenAppOverlayProps> = ({
  widget,
  onClose,
}) => {
  if (!widget) return null;

  return (
    <div className="fixed inset-0 z-50 bg-[#07050d] flex flex-col animate-fade-in text-slate-100">
      {/* Top Header Bar */}
      <div className="px-6 py-4 border-b border-white/5 flex justify-between items-center bg-white/[0.01]">
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

        {/* Action Controls */}
        <button
          onClick={onClose}
          className="px-4 py-2 text-xs font-semibold bg-white/5 hover:bg-white/10 text-white/80 rounded-xl border border-white/10 flex items-center gap-1.5 transition-colors cursor-pointer"
          title="Exit Fullscreen"
        >
          <svg className="w-4 h-4 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
          Exit Fullscreen
        </button>
      </div>

      {/* Main Fullscreen Viewport */}
      <div className="flex-1 p-8 overflow-auto flex items-center justify-center bg-black/20">
        <div className="glass w-full h-full max-w-6xl max-h-[85vh] rounded-3xl p-8 border border-white/5 shadow-2xl flex flex-col overflow-hidden">
          <SandboxWidget
            widget={widget}
            onMinimize={onClose}
          />
        </div>
      </div>
    </div>
  );
};
