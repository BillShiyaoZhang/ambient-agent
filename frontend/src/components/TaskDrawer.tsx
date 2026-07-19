import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleStop,
  Clock3,
  LoaderCircle,
  RotateCcw,
  Server,
  X,
} from "lucide-react";
import { runService, type AmbientRun, type RuntimeSnapshot } from "../services/runs";
import "./TaskDrawer.css";

interface TaskDrawerProps {
  open: boolean;
  language: "zh" | "en";
  onClose: () => void;
  onCountsChange?: (counts: { active: number; attention: number }) => void;
  onOpenSource?: (run: AmbientRun) => void;
}

type Tab = "active" | "attention" | "history" | "runtimes";
const ACTIVE = new Set(["queued", "running", "cancel_requested"]);
const ATTENTION = new Set(["waiting_user", "needs_attention"]);

function formatDuration(run: AmbientRun): string {
  const start = new Date(run.started_at || run.created_at).getTime();
  const end = new Date(run.finished_at || Date.now()).getTime();
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function TaskDrawer({ open, language, onClose, onCountsChange, onOpenSource }: TaskDrawerProps) {
  const isZh = language === "zh";
  const [runs, setRuns] = useState<AmbientRun[]>([]);
  const [runtimes, setRuntimes] = useState<RuntimeSnapshot[]>([]);
  const [tab, setTab] = useState<Tab>("active");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<AmbientRun | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextRuns, nextRuntimes] = await Promise.all([runService.list({ limit: 200 }), runService.runtimes()]);
      setRuns(nextRuns);
      setRuntimes(nextRuntimes);
      setError("");
      if (selectedId) setSelected(await runService.get(selectedId));
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
    }
  }, [selectedId]);

  useEffect(() => {
    void refresh();
    return runService.subscribe(() => void refresh());
  }, [refresh]);

  const counts = useMemo(() => ({
    active: runs.filter((run) => ACTIVE.has(run.status)).length,
    attention: runs.filter((run) => ATTENTION.has(run.status)).length,
  }), [runs]);

  useEffect(() => onCountsChange?.(counts), [counts, onCountsChange]);

  const visible = runs.filter((run) => {
    if (tab === "active") return ACTIVE.has(run.status);
    if (tab === "attention") return ATTENTION.has(run.status);
    if (tab === "history") return !ACTIVE.has(run.status) && !ATTENTION.has(run.status);
    return false;
  });

  const grouped = useMemo(() => {
    const groups = new Map<string, AmbientRun[]>();
    for (const run of visible) groups.set(run.owner_id, [...(groups.get(run.owner_id) || []), run]);
    return [...groups.entries()];
  }, [visible]);

  const perform = async (operation: () => Promise<unknown>) => {
    try {
      await operation();
      await refresh();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : String(actionError));
    }
  };

  const openDetail = async (run: AmbientRun) => {
    setSelectedId(run.id);
    setSelected(await runService.get(run.id));
  };

  return (
    <div className={`task-drawer-layer ${open ? "is-open" : ""}`} aria-hidden={!open}>
      <button className="task-drawer-scrim" onClick={onClose} tabIndex={open ? 0 : -1} aria-label={isZh ? "关闭任务" : "Close tasks"} />
      <aside className="task-drawer" role="dialog" aria-modal="true" aria-label={isZh ? "任务中心" : "Task Center"}>
        <header>
          <div><h2>{isZh ? "任务中心" : "Task Center"}</h2><p>{isZh ? "后台工作与运行环境" : "Background work and runtimes"}</p></div>
          <button onClick={onClose} aria-label={isZh ? "关闭" : "Close"}><X size={18} /></button>
        </header>
        <nav>
          <button className={tab === "active" ? "is-active" : ""} onClick={() => setTab("active")}>{isZh ? "进行中" : "Active"}<span>{counts.active}</span></button>
          <button className={tab === "attention" ? "is-active" : ""} onClick={() => setTab("attention")}>{isZh ? "待处理" : "Attention"}<span>{counts.attention}</span></button>
          <button className={tab === "history" ? "is-active" : ""} onClick={() => setTab("history")}>{isZh ? "历史" : "History"}</button>
          <button className={tab === "runtimes" ? "is-active" : ""} onClick={() => setTab("runtimes")}>{isZh ? "后台" : "Runtimes"}</button>
        </nav>
        {error && <button className="task-drawer-error" onClick={() => setError("")}><AlertTriangle size={14} />{error}</button>}
        <div className="task-drawer-body">
          {tab === "runtimes" ? (
            <div className="runtime-list">
              {runtimes.map((runtime) => (
                <article key={runtime.id}>
                  <Server size={17} />
                  <div><strong>{runtime.id}</strong><small>{runtime.type} · {runtime.status}</small></div>
                  {runtime.managed && runtime.status !== "stopped" && (
                    <button onClick={() => perform(() => runService.stopRuntime(runtime.id))}>{isZh ? "停止" : "Stop"}</button>
                  )}
                </article>
              ))}
            </div>
          ) : grouped.length === 0 ? (
            <div className="task-drawer-empty"><CheckCircle2 size={30} /><p>{isZh ? "这里暂时没有任务" : "No tasks here"}</p></div>
          ) : (
            grouped.map(([owner, ownerRuns]) => (
              <section className="task-run-group" key={owner}>
                <h3>{owner}</h3>
                {ownerRuns.map((run) => (
                  <button className="task-run-row" key={run.id} onClick={() => void openDetail(run)}>
                    <span className={`task-run-status is-${run.status}`}>{run.status === "running" ? <LoaderCircle size={15} /> : run.status === "waiting_user" || run.status === "needs_attention" ? <AlertTriangle size={15} /> : <Clock3 size={15} />}</span>
                    <span><strong>{run.action_title}</strong><small>{run.summary || run.status} · {formatDuration(run)}</small></span>
                    {run.status === "running" && <i style={{ width: `${Math.round(run.progress * 100)}%` }} />}
                    <ChevronRight size={15} />
                  </button>
                ))}
              </section>
            ))
          )}
        </div>
        {selected && (
          <section className="task-run-detail">
            <header><button onClick={() => { setSelected(null); setSelectedId(null); }}>←</button><div><h3>{selected.action_title}</h3><small>{selected.status} · {formatDuration(selected)}</small></div></header>
            <p>{selected.summary || (isZh ? "暂无摘要" : "No summary")}</p>
            {selected.source_id && <button className="task-source-link" onClick={() => onOpenSource?.(selected)}>{isZh ? "打开来源" : "Open source"} · {selected.source_type}:{selected.source_id}</button>}
            {(selected.interactions || []).filter((item) => item.status === "pending").map((interaction) => (
              <div className="task-interaction" key={interaction.id}>
                <strong>{interaction.prompt}</strong>
                <pre>{JSON.stringify(interaction.payload, null, 2)}</pre>
                <div><button onClick={() => perform(() => runService.resolve(interaction.id, { approved: false }))}>{isZh ? "拒绝" : "Deny"}</button><button className="is-primary" onClick={() => perform(() => runService.resolve(interaction.id, { approved: true }))}>{isZh ? "允许" : "Allow"}</button></div>
              </div>
            ))}
            {selected.result !== undefined && selected.result !== null && <><h4>{isZh ? "结果" : "Result"}</h4><pre>{JSON.stringify(selected.result, null, 2)}</pre></>}
            {(selected.artifacts || []).length > 0 && <><h4>{isZh ? "产物" : "Artifacts"}</h4><pre>{JSON.stringify(selected.artifacts, null, 2)}</pre></>}
            {selected.error && <><h4>{isZh ? "错误" : "Error"}</h4><pre>{JSON.stringify(selected.error, null, 2)}</pre></>}
            <h4>{isZh ? "输入" : "Input"}</h4><pre>{JSON.stringify(selected.input, null, 2)}</pre>
            <footer>
              {ACTIVE.has(selected.status) || selected.status === "waiting_user" ? <button onClick={() => perform(() => runService.cancel(selected.id))}><CircleStop size={15} />{isZh ? "取消" : "Cancel"}</button> : null}
              {["failed", "cancelled", "needs_attention"].includes(selected.status) ? <button onClick={() => perform(() => runService.retry(selected.id))}><RotateCcw size={15} />{isZh ? "重试" : "Retry"}</button> : null}
            </footer>
          </section>
        )}
      </aside>
    </div>
  );
}
