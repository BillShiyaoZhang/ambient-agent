import React, { useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  LoaderCircle,
  OctagonX,
  PauseCircle,
  Square,
  Wrench,
} from "lucide-react";
import type {
  ChatRunCard as ChatRunCardModel,
  LiveStreamState,
  RunActivity,
} from "../lib/chatProjection";

interface ChatRunCardProps {
  run: ChatRunCardModel;
  language: "zh" | "en";
  onCancel?: (runId: string) => void;
  liveStreams?: LiveStreamState[];
}

const ACTIVE_STATUSES = new Set(["queued", "running", "waiting_user", "cancel_requested"]);
const PHASES = ["plan", "align_schema", "stage_code", "verify", "promote"] as const;

function canonicalPhase(phase: string): string {
  if (phase === "wait_plan") return "plan";
  if (phase === "wait_schema") return "align_schema";
  if (phase === "wait_override") return "verify";
  return phase;
}

function phaseLabel(phase: string, isZh: boolean): string {
  const labels: Record<string, [string, string]> = {
    route: ["理解请求", "Understand"],
    plan: ["制定方案", "Plan"],
    align_schema: ["对齐数据", "Align data"],
    stage_code: ["生成应用", "Build app"],
    verify: ["验证", "Verify"],
    promote: ["发布", "Publish"],
    converse: ["处理请求", "Working"],
  };
  const pair = labels[canonicalPhase(phase)] ?? [phase.replaceAll("_", " "), phase.replaceAll("_", " ")];
  return pair[isZh ? 0 : 1];
}

function localizedDetail(detail: string | undefined, isZh: boolean): string | undefined {
  if (!detail) return undefined;
  const labels: Record<string, [string, string]> = {
    plan_approval: ["方案已就绪，等待确认", "Plan ready for approval"],
    schema_approval: ["数据方案已就绪，等待确认", "Data proposal ready for approval"],
    verification_approval: ["验证需要确认后继续", "Verification needs approval"],
    permission_approval: ["等待授权后继续", "Waiting for permission"],
  };
  const pair = labels[detail];
  return pair ? pair[isZh ? 0 : 1] : detail;
}

function statusMeta(status: ChatRunCardModel["status"], isZh: boolean) {
  const values = {
    queued: { label: isZh ? "排队中" : "Queued", icon: Circle, tone: "neutral" },
    running: { label: isZh ? "生成中" : "Running", icon: LoaderCircle, tone: "active" },
    waiting_user: { label: isZh ? "等待确认" : "Waiting", icon: PauseCircle, tone: "warning" },
    cancel_requested: { label: isZh ? "正在停止" : "Stopping", icon: LoaderCircle, tone: "neutral" },
    needs_attention: { label: isZh ? "需要处理" : "Needs attention", icon: AlertTriangle, tone: "danger" },
    succeeded: { label: isZh ? "已完成" : "Completed", icon: CheckCircle2, tone: "success" },
    failed: { label: isZh ? "未完成" : "Failed", icon: OctagonX, tone: "danger" },
    cancelled: { label: isZh ? "已停止" : "Stopped", icon: Square, tone: "neutral" },
  } as const;
  return values[status];
}

function activitySummary(activity: RunActivity, isZh: boolean): string {
  const parts: string[] = [];
  if (activity.toolCalls > 0) {
    parts.push(isZh ? `${activity.toolCalls} 次工具调用` : `${activity.toolCalls} tool call${activity.toolCalls === 1 ? "" : "s"}`);
  }
  if (activity.updates > 1) {
    parts.push(isZh ? `${activity.updates} 条进度已归并` : `${activity.updates} updates grouped`);
  }
  return parts.join(" · ");
}

function liveStreamText(stream: LiveStreamState, isZh: boolean): string {
  if (stream.kind !== "tool_progress") return stream.text.slice(-1_200);
  const tool = stream.tool || stream.text;
  const labels: Record<string, [string, string]> = {
    started: [`正在运行 ${tool}`, `Running ${tool}`],
    succeeded: [`${tool} 已完成`, `${tool} completed`],
    completed: [`${tool} 已完成`, `${tool} completed`],
    failed: [`${tool} 未完成`, `${tool} failed`],
    cancelled: [`${tool} 已停止`, `${tool} stopped`],
  };
  const pair = labels[stream.toolStatus ?? "started"] ?? [`正在运行 ${tool}`, `Running ${tool}`];
  return pair[isZh ? 0 : 1];
}

export const ChatRunCard: React.FC<ChatRunCardProps> = ({ run, language, onCancel, liveStreams = [] }) => {
  const isZh = language === "zh";
  const [expanded, setExpanded] = useState(() => ACTIVE_STATUSES.has(run.status));
  const active = ACTIVE_STATUSES.has(run.status);
  const meta = statusMeta(run.status, isZh);
  const StatusIcon = meta.icon;
  const currentPhase = canonicalPhase(run.phase);
  const currentPhaseIndex = PHASES.indexOf(currentPhase as typeof PHASES[number]);
  const widgetRun = run.workflowType?.startsWith("widget") || currentPhaseIndex >= 0;
  const latestLive = liveStreams.at(-1);

  useEffect(() => {
    if (run.status === "needs_attention" || run.status === "failed") setExpanded(true);
  }, [run.status]);

  return (
    <section className={`chat-run-card is-${meta.tone}`} aria-label={widgetRun ? (isZh ? "生成任务" : "Generation run") : (isZh ? "Agent 任务" : "Agent run")}>
      <button
        type="button"
        className="chat-run-header"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="chat-run-status-icon">
          <StatusIcon size={16} className={run.status === "running" || run.status === "cancel_requested" ? "is-spinning" : ""} />
        </span>
        <span className="chat-run-heading">
          <strong>{widgetRun ? (isZh ? "生成 App" : "Build app") : (isZh ? "处理任务" : "Agent run")}</strong>
          <small>{meta.label} · {phaseLabel(run.phase, isZh)}</small>
        </span>
        {run.repairCount > 0 ? <span className="chat-run-repair"><Wrench size={11} />{isZh ? `已自动修复 ${run.repairCount} 次` : `${run.repairCount} auto-repair${run.repairCount === 1 ? "" : "s"}`}</span> : null}
        {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      </button>

      {widgetRun ? <div className="chat-run-phase-rail" aria-label={isZh ? "生成阶段" : "Generation phases"}>
        {PHASES.map((phase, index) => {
          const activity = run.activities.find((item) => canonicalPhase(item.phase) === phase);
          const complete = activity?.status === "completed" || currentPhaseIndex > index || run.status === "succeeded";
          const current = currentPhaseIndex === index && !complete;
          return (
            <span
              key={phase}
              className={`${complete ? "is-complete" : ""} ${current ? "is-current" : ""}`.trim()}
              title={phaseLabel(phase, isZh)}
            >
              {complete ? <Check size={10} /> : current ? <LoaderCircle size={10} className={active ? "is-spinning" : ""} /> : <Circle size={8} />}
            </span>
          );
        })}
      </div> : null}

      {expanded ? <div className="chat-run-body">
        {run.activities.length > 0 ? <ol className="chat-run-activities">
          {run.activities.map((activity) => {
            const aggregate = activitySummary(activity, isZh);
            return <li key={activity.id} className={`is-${activity.status}`}>
              <span className="chat-run-activity-marker" />
              <div>
                <strong>{phaseLabel(activity.phase, isZh)}</strong>
                {localizedDetail(activity.detail, isZh) ? <p>{localizedDetail(activity.detail, isZh)}</p> : null}
                {aggregate ? <small>{aggregate}</small> : null}
              </div>
            </li>;
          })}
        </ol> : <p className="chat-run-summary">{run.summary || (isZh ? "正在准备…" : "Preparing…")}</p>}
        {latestLive ? <div className={`chat-run-live is-${latestLive.kind}`}>
          <span aria-hidden="true" />
          <p>{liveStreamText(latestLive, isZh)}<i aria-hidden="true" /></p>
          {latestLive.hasGap ? <small>{isZh ? "部分实时片段已跳过，完成后将以持久结果校准。" : "Some live fragments were skipped; durable completion will reconcile the result."}</small> : null}
        </div> : null}
        {run.error ? <p className="chat-run-error">{run.error}</p> : null}
        <footer className="chat-run-footer">
          <span>
            {run.attempt > 1 ? (isZh ? `第 ${run.attempt} 次尝试` : `Attempt ${run.attempt}`) : null}
            {run.modelTurns > 0 ? `${run.attempt > 1 ? " · " : ""}${isZh ? `${run.modelTurns} 个 Agent 回合` : `${run.modelTurns} agent turns`}` : null}
          </span>
          {active && onCancel ? <button type="button" onClick={() => onCancel(run.id)} disabled={run.status === "cancel_requested"}>
            <Square size={11} />{run.status === "cancel_requested" ? (isZh ? "正在停止" : "Stopping") : (isZh ? "停止" : "Stop")}
          </button> : null}
        </footer>
      </div> : null}
    </section>
  );
};
