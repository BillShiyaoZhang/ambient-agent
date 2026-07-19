import { useEffect, useState } from "react";
import { SystemToast } from "./system/SystemUI";

export interface MutationPreviewData {
  ticket_id: string;
  session_id: string;
  actions: Array<Record<string, unknown>>;
  summary: string;
  soft_window_seconds: number;
}

export interface MutationPreviewProps {
  preview: MutationPreviewData | null;
  onRollback: (ticketId: string) => void;
  onPin: (ticketId: string) => void;
  onDismiss: (ticketId: string) => void;
}

export function MutationPreview({ preview, onRollback, onPin, onDismiss }: MutationPreviewProps) {
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);

  useEffect(() => {
    if (!preview) {
      setSecondsLeft(null);
      return;
    }
    setSecondsLeft(preview.soft_window_seconds);
    const interval = setInterval(() => {
      setSecondsLeft((prev) => (prev === null ? null : Math.max(0, prev - 1)));
    }, 1000);
    return () => clearInterval(interval);
  }, [preview]);

  if (!preview) return null;

  const canRollback = (secondsLeft ?? 0) > 0;

  return (
    <SystemToast tone="success" className="mutation-toast">
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="font-semibold text-emerald-400 mb-1 flex items-center gap-1.5">
            <span className="inline-block w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            数据已更新
          </div>
          <div className="text-slate-300 leading-relaxed">
            {preview.summary}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2 text-[11px]">
        {canRollback && (
          <button
            data-testid="mutation-rollback"
            onClick={() => onRollback(preview.ticket_id)}
            className="px-2.5 py-1 rounded-md bg-rose-500/15 text-rose-300 border border-rose-500/30 hover:bg-rose-500/25"
          >
            ⟲ 撤销 ({secondsLeft}s)
          </button>
        )}
        <button
          data-testid="mutation-pin"
          onClick={() => onPin(preview.ticket_id)}
          className="px-2.5 py-1 rounded-md bg-amber-500/15 text-amber-300 border border-amber-500/30 hover:bg-amber-500/25"
          title="永久保留（不因会话超时被丢弃）"
        >
          ⭐ 永久可撤销
        </button>
        <button
          onClick={() => onDismiss(preview.ticket_id)}
          className="ml-auto px-2 py-1 rounded-md text-slate-500 hover:bg-white/5"
        >
          ✕
        </button>
      </div>
    </SystemToast>
  );
}
