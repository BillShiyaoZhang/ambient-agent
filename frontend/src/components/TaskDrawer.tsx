import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleStop,
  Clock3,
  LoaderCircle,
  Network,
  RotateCcw,
  Server,
  X,
} from "lucide-react";
import {
  runService,
  type AmbientRun,
  type AmbientRunSummary,
  type RunEvent,
  type RuntimeSnapshot,
} from "../services/runs";
import { workflowToGraph } from "../lib/graphScenes";
import { DeferredGraphExplorer } from "./graph/DeferredGraph";
import { SystemDrawer, SystemIconButton } from "./system/SystemUI";
import "./TaskDrawer.css";

interface TaskDrawerProps {
  open: boolean;
  language: "zh" | "en";
  onClose: () => void;
  onCountsChange?: (counts: { active: number; attention: number }) => void;
  onOpenSource?: (run: AmbientRun) => void;
}

type Tab = "active" | "attention" | "history" | "runtimes";
type DetailView = "overview" | "graph";
const ACTIVE = new Set(["queued", "running", "cancel_requested"]);
const ATTENTION = new Set(["waiting_user", "needs_attention"]);
const EVENT_REFRESH_DEBOUNCE_MS = 100;
const EVENT_REFRESH_MAX_WAIT_MS = 500;
const OPEN_RUN_STATUSES = [...ACTIVE, ...ATTENTION].join(",");

function isDurableWorkflowRun(run: AmbientRun | null): boolean {
  if (run?.adapter_type === "internal_agent") return true;
  return run?.adapter_type === "internal"
    && run.workflow_version === 1
    && run.status === "needs_attention"
    && Boolean(run.events?.some((event) => event.type === "migration_attention_required"));
}

function formatDuration(run: AmbientRunSummary): string {
  const start = new Date(run.started_at || run.created_at).getTime();
  const end = new Date(run.finished_at || Date.now()).getTime();
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function TaskDrawer({ open, language, onClose, onCountsChange, onOpenSource }: TaskDrawerProps) {
  const isZh = language === "zh";
  const [runs, setRuns] = useState<AmbientRunSummary[]>([]);
  const [runtimes, setRuntimes] = useState<RuntimeSnapshot[]>([]);
  const [tab, setTab] = useState<Tab>("active");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<AmbientRun | null>(null);
  const [detailView, setDetailView] = useState<DetailView>("overview");
  const [error, setError] = useState("");
  const runsRefreshGeneration = useRef(0);

  const refreshRuns = useCallback(async () => {
    const generation = ++runsRefreshGeneration.current;
    try {
      const [recentRuns, openRuns] = await Promise.all([
        runService.list({ limit: 200, summary_only: true }),
        runService.list({
          status: OPEN_RUN_STATUSES,
          limit: 500,
          summary_only: true,
        }),
      ]);
      if (generation !== runsRefreshGeneration.current) return;
      const recentIds = new Set(recentRuns.map((run) => run.id));
      setRuns([
        ...recentRuns,
        ...openRuns.filter((run) => !recentIds.has(run.id)),
      ]);
      setError("");
    } catch (refreshError) {
      if (generation !== runsRefreshGeneration.current) return;
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
    }
  }, []);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  const refreshRuntimes = useCallback(async () => {
    try {
      setRuntimes(await runService.runtimes());
      setError("");
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number | null = null;
    let maxWaitTimer: number | null = null;
    let inFlight = false;
    let dirty = false;
    let selectedDirty = false;

    const trigger = () => {
      if (timer !== null) window.clearTimeout(timer);
      if (maxWaitTimer !== null) window.clearTimeout(maxWaitTimer);
      timer = null;
      maxWaitTimer = null;
      void flush();
    };
    const schedule = () => {
      if (disposed) return;
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        trigger();
      }, EVENT_REFRESH_DEBOUNCE_MS);
      if (maxWaitTimer === null) {
        maxWaitTimer = window.setTimeout(() => {
          trigger();
        }, EVENT_REFRESH_MAX_WAIT_MS);
      }
    };
    const flush = async () => {
      if (disposed) return;
      if (inFlight) {
        dirty = true;
        return;
      }
      inFlight = true;
      const loadSelected = selectedDirty && selectedId;
      dirty = false;
      selectedDirty = false;
      try {
        await Promise.all([
          refreshRuns(),
          loadSelected
            ? runService.get(loadSelected).then((nextSelected) => {
                if (!disposed) setSelected(nextSelected);
              })
            : Promise.resolve(),
          open && tab === "runtimes" ? refreshRuntimes() : Promise.resolve(),
        ]);
      } catch (refreshError) {
        if (!disposed) {
          setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
        }
      } finally {
        inFlight = false;
        if (!disposed && (dirty || selectedDirty)) schedule();
      }
    };
    const unsubscribe = runService.subscribe((event: RunEvent) => {
      dirty = true;
      if (event.run_id === selectedId) selectedDirty = true;
      schedule();
    });
    return () => {
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
      if (maxWaitTimer !== null) window.clearTimeout(maxWaitTimer);
      unsubscribe();
    };
  }, [open, refreshRuns, refreshRuntimes, selectedId, tab]);

  useEffect(() => {
    if (open && tab === "runtimes") void refreshRuntimes();
  }, [open, refreshRuntimes, tab]);

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
    const groups = new Map<string, AmbientRunSummary[]>();
    for (const run of visible) groups.set(run.owner_id, [...(groups.get(run.owner_id) || []), run]);
    return [...groups.entries()];
  }, [visible]);
  const hasDurableWorkflow = isDurableWorkflowRun(selected);

  const perform = async (operation: () => Promise<unknown>) => {
    try {
      await operation();
      await Promise.all([
        refreshRuns(),
        selectedId ? runService.get(selectedId).then(setSelected) : Promise.resolve(),
        tab === "runtimes" ? refreshRuntimes() : Promise.resolve(),
      ]);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : String(actionError));
    }
  };

  const openDetail = async (run: AmbientRunSummary) => {
    setDetailView("overview");
    setSelectedId(run.id);
    setSelected(await runService.get(run.id));
  };

  return (
    <SystemDrawer
      open={open}
      onClose={onClose}
      label={isZh ? "任务中心" : "Task Center"}
      closeLabel={isZh ? "关闭任务中心" : "Close Task Center"}
      className="task-drawer"
    >
        <header>
          <div><h2>{isZh ? "任务中心" : "Task Center"}</h2><p>{isZh ? "后台工作与运行环境" : "Background work and runtimes"}</p></div>
          <SystemIconButton onClick={onClose} label={isZh ? "关闭" : "Close"}><X size={18} /></SystemIconButton>
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
            <header>
              <button onClick={() => { setSelected(null); setSelectedId(null); setDetailView("overview"); }}>←</button>
              <div><h3>{selected.action_title}</h3><small>{selected.status} · {formatDuration(selected)}</small></div>
            </header>
            <nav className="task-detail-tabs" aria-label={isZh ? "运行详情视图" : "Run detail views"}>
              <button
                aria-pressed={detailView === "overview"}
                className={detailView === "overview" ? "is-active" : ""}
                onClick={() => setDetailView("overview")}
                type="button"
              >
                {isZh ? "概览" : "Overview"}
              </button>
              {hasDurableWorkflow && (
                <button
                  aria-pressed={detailView === "graph"}
                  className={detailView === "graph" ? "is-active" : ""}
                  onClick={() => setDetailView("graph")}
                  type="button"
                >
                  <Network size={13} />
                  {isZh ? "执行图" : "Execution graph"}
                </button>
              )}
            </nav>
            {detailView === "graph" && hasDurableWorkflow ? (
              <div className="task-run-graph">
                <DeferredGraphExplorer
                  dataset={workflowToGraph(selected, language)}
                  language={language}
                  loadingLabel={isZh ? "正在加载执行图" : "Execution graph loading"}
                  loadingMessage={isZh ? "正在加载交互式执行图…" : "Loading interactive execution graph…"}
                />
              </div>
            ) : (
              <>
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
                  {["failed", "cancelled"].includes(selected.status) && !["unknown", "committed"].includes(selected.error?.effect_state || "") ? <button onClick={() => perform(() => runService.retry(selected.id))}><RotateCcw size={15} />{isZh ? "重试" : "Retry"}</button> : null}
                  {selected.status === "needs_attention" ? <>
                    <button onClick={() => perform(() => runService.reconcile(selected.id, "confirmed_not_committed"))}>{isZh ? "确认未执行" : "Not committed"}</button>
                    <button onClick={() => perform(() => runService.reconcile(selected.id, "compensated"))}>{isZh ? "确认已补偿" : "Compensated"}</button>
                    <button onClick={() => perform(() => runService.reconcile(selected.id, "confirmed_committed"))}>{isZh ? "确认已执行" : "Committed"}</button>
                  </> : null}
                </footer>
              </>
            )}
          </section>
        )}
    </SystemDrawer>
  );
}
