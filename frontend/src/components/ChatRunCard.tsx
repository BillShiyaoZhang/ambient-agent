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
  RunInteractionState,
} from "../lib/chatProjection";

export type RunInteractionAction =
  | "approve"
  | "deny"
  | "rework_code"
  | "rework_schema"
  | "rework_plan";

interface ChatRunCardProps {
  run: ChatRunCardModel;
  language: "zh" | "en";
  onCancel?: (runId: string) => void;
  liveStreams?: LiveStreamState[];
  interactions?: RunInteractionState[];
  onResolveInteraction?: (interaction: RunInteractionState, action: RunInteractionAction) => void;
  onInspectInteraction?: (interaction: RunInteractionState) => void;
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

function humanizeTool(value: string): string {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}

function structuredActivityTitle(activity: RunActivity, isZh: boolean): string {
  const kind = activity.kind ?? "phase";
  if (kind === "phase") return phaseLabel(activity.phase, isZh);
  if (activity.kind === "tool") {
    const tool = humanizeTool(activity.title || "tool");
    if (activity.status === "running") return isZh ? `正在运行 ${tool}` : `Running ${tool}`;
    if (activity.status === "failed") return isZh ? `${tool} 未完成` : `${tool} failed`;
    return isZh ? `${tool} 已完成` : `${tool} completed`;
  }
  const labels: Record<NonNullable<RunActivity["kind"]>, [string, string]> = {
    phase: ["处理阶段", "Phase"],
    plan: ["开发计划已生成", "Development plan prepared"],
    schema: ["数据与能力方案已生成", "Data and capability proposal prepared"],
    code: activity.status === "running"
      ? ["正在生成应用", "Generating the app"]
      : ["应用草稿已生成", "Staged app generated"],
    tool: ["工具执行", "Tool execution"],
    verification: activity.status === "running"
      ? ["正在独立验证", "Running independent verification"]
      : activity.status === "failed"
        ? ["验证发现需要调整", "Verification found required changes"]
        : ["独立验证已通过", "Independent verification passed"],
    repair: activity.status === "failed"
      ? ["自动修复已停止", "Automatic repair stopped"]
      : ["自动修复已完成", "Automatic repair completed"],
    artifact: activity.status === "running"
      ? ["正在发布 App", "Publishing app"]
      : ["App 已发布", "App published"],
    approval: activity.status === "waiting"
      ? ["等待你的确认", "Waiting for your decision"]
      : ["确认已处理", "Decision recorded"],
  };
  return labels[kind][isZh ? 0 : 1];
}

function interactionSummary(interaction: RunInteractionState, isZh: boolean): {
  title: string;
  detail: string;
} {
  if (interaction.kind === "plan_approval") {
    return {
      title: isZh ? "确认开发计划" : "Review development plan",
      detail: String(interaction.payload.plan || "").slice(0, 360),
    };
  }
  if (interaction.kind === "schema_approval") {
    const proposal = interaction.payload.proposal;
    const record = typeof proposal === "object" && proposal !== null && !Array.isArray(proposal)
      ? proposal as Record<string, unknown>
      : {};
    const reused = Array.isArray(record.reused_schemas) ? record.reused_schemas.length : 0;
    const created = Array.isArray(record.new_schemas) ? record.new_schemas.length : 0;
    const capabilities = Array.isArray(record.capabilities) ? record.capabilities.length : 0;
    return {
      title: isZh ? "确认数据与能力方案" : "Review data and capability proposal",
      detail: isZh
        ? `复用 ${reused} 个 Schema · 新增 ${created} 个 · ${capabilities} 项能力`
        : `${reused} reused schemas · ${created} new · ${capabilities} capabilities`,
    };
  }
  return {
    title: isZh ? "选择验证修复方式" : "Choose a verification repair path",
    detail: String(interaction.payload.report || "").replace(/^#+\s*/gm, "").slice(0, 360),
  };
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

export const ChatRunCard: React.FC<ChatRunCardProps> = ({
  run,
  language,
  onCancel,
  liveStreams = [],
  interactions = [],
  onResolveInteraction,
  onInspectInteraction,
}) => {
  const isZh = language === "zh";
  const [expanded, setExpanded] = useState(() => ACTIVE_STATUSES.has(run.status));
  const active = ACTIVE_STATUSES.has(run.status);
  const meta = statusMeta(run.status, isZh);
  const StatusIcon = meta.icon;
  const currentPhase = canonicalPhase(run.phase);
  const currentPhaseIndex = PHASES.indexOf(currentPhase as typeof PHASES[number]);
  const widgetRun = run.workflowType?.startsWith("widget") || currentPhaseIndex >= 0;
  const latestLive = liveStreams.at(-1);
  const debugEvents = run.debugEvents ?? [];

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
                <strong>{structuredActivityTitle(activity, isZh)}</strong>
                {localizedDetail(activity.detail, isZh) ? <p>{localizedDetail(activity.detail, isZh)}</p> : null}
                {aggregate ? <small>{aggregate}</small> : null}
              </div>
            </li>;
          })}
        </ol> : <p className="chat-run-summary">{run.summary || (isZh ? "正在准备…" : "Preparing…")}</p>}
        {interactions.map((interaction) => {
          const summary = interactionSummary(interaction, isZh);
          const pending = interaction.status === "pending";
          const allowedActions = Array.isArray(interaction.payload.allowed_actions)
            ? interaction.payload.allowed_actions.map(String)
            : ["rework_code", "rework_schema", "rework_plan"];
          return <section className={`chat-run-interaction is-${interaction.status}`} key={interaction.id}>
            <header>
              <strong>{summary.title}</strong>
              <span>{pending ? (isZh ? "需要操作" : "Action needed") : (isZh ? "已处理" : "Resolved")}</span>
            </header>
            {summary.detail ? <p>{summary.detail}</p> : null}
            {pending ? <div>
              {pending && interaction.kind === "plan_approval" && onResolveInteraction ? <>
                <button type="button" onClick={() => onResolveInteraction(interaction, "deny")}>{isZh ? "取消" : "Cancel"}</button>
                <button type="button" onClick={() => onInspectInteraction?.(interaction)}>{isZh ? "查看 / 调整" : "Review / edit"}</button>
                <button type="button" className="is-primary" onClick={() => onResolveInteraction(interaction, "approve")}>{isZh ? "批准计划" : "Approve plan"}</button>
              </> : null}
              {pending && interaction.kind === "schema_approval" && onResolveInteraction ? <>
                <button type="button" onClick={() => onResolveInteraction(interaction, "deny")}>{isZh ? "取消" : "Cancel"}</button>
                <button type="button" onClick={() => onInspectInteraction?.(interaction)}>{isZh ? "查看 / 编辑" : "Review / edit"}</button>
                <button type="button" className="is-primary" onClick={() => onResolveInteraction(interaction, "approve")}>{isZh ? "批准并编码" : "Approve"}</button>
              </> : null}
              {pending && interaction.kind === "verification_approval" && onResolveInteraction ? <>
                {allowedActions.includes("rework_plan") ? <button type="button" onClick={() => onResolveInteraction(interaction, "rework_plan")}>{isZh ? "重做方案" : "Rework plan"}</button> : null}
                {allowedActions.includes("rework_schema") ? <button type="button" onClick={() => onResolveInteraction(interaction, "rework_schema")}>{isZh ? "调整数据" : "Rework data"}</button> : null}
                {allowedActions.includes("rework_code") ? <button type="button" className="is-primary" onClick={() => onResolveInteraction(interaction, "rework_code")}>{isZh ? "修复代码" : "Repair code"}</button> : null}
                <button type="button" onClick={() => onInspectInteraction?.(interaction)}>{isZh ? "查看详情" : "Details"}</button>
              </> : null}
            </div> : null}
          </section>;
        })}
        {latestLive ? <div className={`chat-run-live is-${latestLive.kind}`}>
          <span aria-hidden="true" />
          <p>{liveStreamText(latestLive, isZh)}<i aria-hidden="true" /></p>
          {latestLive.hasGap ? <small>{isZh ? "部分实时片段已跳过，完成后将以持久结果校准。" : "Some live fragments were skipped; durable completion will reconcile the result."}</small> : null}
        </div> : null}
        {run.error ? <p className="chat-run-error">{run.error}</p> : null}
        {debugEvents.length > 0 ? <details className="chat-run-debug">
          <summary>{isZh ? `调试详情 · ${debugEvents.length}` : `Debug details · ${debugEvents.length}`}</summary>
          <pre>{debugEvents.map((event) => (
            `[${event.sequence}] ${event.type}${event.stepId ? ` · ${event.stepId}` : ""}\n${event.payload}`
          )).join("\n\n")}</pre>
        </details> : null}
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
