import { lazy, Suspense } from "react";
import type { GraphExplorerProps } from "./GraphExplorer";
import { SystemDialog } from "../system/SystemUI";
import "./DeferredGraph.css";

const LoadedGraphExplorer = lazy(async () => {
  const module = await import("./GraphExplorer");
  return { default: module.GraphExplorer };
});

const LoadedGraphWorkbench = lazy(async () => {
  const module = await import("./GraphWorkbench");
  return { default: module.GraphWorkbench };
});

export interface GraphLoadingStatusProps {
  label: string;
  message?: string;
  className?: string;
  compact?: boolean;
}

export function GraphLoadingStatus({
  label,
  message = "Loading interactive graph…",
  className = "",
  compact = false,
}: GraphLoadingStatusProps) {
  return (
    <div
      aria-busy="true"
      aria-label={label}
      aria-live="polite"
      className={[
        "deferred-graph-status",
        compact ? "is-compact" : "",
        className,
      ].filter(Boolean).join(" ")}
      role="status"
    >
      <span aria-hidden="true" className="deferred-graph-spinner" />
      <span>{message}</span>
    </div>
  );
}

export interface DeferredGraphExplorerProps extends GraphExplorerProps {
  loadingLabel?: string;
  loadingMessage?: string;
}

export function DeferredGraphExplorer({
  loadingLabel,
  loadingMessage,
  ...props
}: DeferredGraphExplorerProps) {
  const label = loadingLabel ?? `${props.ariaLabel ?? "Interactive graph explorer"} loading`;
  return (
    <Suspense
      fallback={(
        <GraphLoadingStatus
          className={props.className}
          compact={props.compact}
          label={label}
          message={loadingMessage}
        />
      )}
    >
      <LoadedGraphExplorer {...props} />
    </Suspense>
  );
}

export interface DeferredGraphWorkbenchProps {
  open: boolean;
  language: "zh" | "en";
  onClose: () => void;
}

export function DeferredGraphWorkbench({
  open,
  language,
  onClose,
}: DeferredGraphWorkbenchProps) {
  if (!open) return null;
  const isZh = language === "zh";
  return (
    <Suspense
      fallback={(
        <SystemDialog
          description={isZh
            ? "正在准备交互式本体、知识图谱、Agent 工作流和隐私数据图。"
            : "Preparing interactive ontology, knowledge graph, agent workflow, and privacy map views."}
          onClose={onClose}
          open
          size="workbench"
          title={isZh ? "图谱探索" : "Graph Explorer"}
        >
          <GraphLoadingStatus
            className="deferred-graph-workbench-status"
            label={isZh ? "正在加载图谱探索" : "Graph Explorer loading"}
            message={isZh ? "正在加载交互式图谱…" : "Loading interactive graph…"}
          />
        </SystemDialog>
      )}
    >
      <LoadedGraphWorkbench language={language} onClose={onClose} open />
    </Suspense>
  );
}

