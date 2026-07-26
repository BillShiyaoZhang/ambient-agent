import type { AmbientRun, RunEvent, RunStatus } from "../services/runs";

export interface RunActivity {
  id: string;
  phase: string;
  status: "running" | "completed" | "failed" | "waiting";
  detail?: string;
  updates: number;
  toolCalls: number;
  startedAt: string;
  updatedAt: string;
}

export interface ChatRunCard {
  id: string;
  status: RunStatus;
  phase: string;
  attempt: number;
  workflowType?: string;
  summary: string;
  error?: string;
  createdAt: string;
  updatedAt: string;
  finishedAt?: string;
  modelTurns: number;
  repairCount: number;
  artifactCount: number;
  activities: RunActivity[];
}

export interface ConversationProjection {
  order: string[];
  runs: Record<string, ChatRunCard>;
}

export const EMPTY_CONVERSATION_PROJECTION: ConversationProjection = {
  order: [],
  runs: {},
};

const TERMINAL_STATUSES = new Set<RunStatus>(["succeeded", "failed", "cancelled", "needs_attention"]);

const asRecord = (value: unknown): Record<string, unknown> | null => (
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const asString = (value: unknown): string => typeof value === "string" ? value : "";

const asNumber = (value: unknown): number => (
  typeof value === "number" && Number.isFinite(value) ? value : 0
);

function normalizedStatus(value: unknown, fallback: RunStatus = "queued"): RunStatus {
  return [
    "queued",
    "running",
    "waiting_user",
    "cancel_requested",
    "needs_attention",
    "succeeded",
    "failed",
    "cancelled",
  ].includes(String(value))
    ? value as RunStatus
    : fallback;
}

function trimDetail(value: unknown): string {
  const text = asString(value).replace(/\r/g, "").trim();
  if (!text) return "";
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  const useful = [...lines].reverse().find((line) => (
    !/^✅ Tool call completed/.test(line)
    && !/^🛠️ Calling tool/.test(line)
    && !/^🛠️ .*调用工具/.test(line)
  )) ?? lines.at(-1) ?? "";
  return useful.replace(/^#+\s*/, "").slice(0, 320);
}

function activityStatusFromRun(status: RunStatus): RunActivity["status"] {
  if (status === "waiting_user") return "waiting";
  if (status === "failed" || status === "cancelled" || status === "needs_attention") return "failed";
  if (status === "succeeded") return "completed";
  return "running";
}

function defaultCard(runId: string, createdAt: string): ChatRunCard {
  return {
    id: runId,
    status: "queued",
    phase: "route",
    attempt: 1,
    summary: "",
    createdAt,
    updatedAt: createdAt,
    modelTurns: 0,
    repairCount: 0,
    artifactCount: 0,
    activities: [],
  };
}

function withCard(
  projection: ConversationProjection,
  runId: string,
  createdAt: string,
  updater: (card: ChatRunCard) => ChatRunCard,
): ConversationProjection {
  const existing = projection.runs[runId] ?? defaultCard(runId, createdAt);
  const next = updater(existing);
  return {
    order: projection.order.includes(runId) ? projection.order : [...projection.order, runId],
    runs: { ...projection.runs, [runId]: next },
  };
}

function upsertActivity(
  card: ChatRunCard,
  phase: string,
  now: string,
  patch: Partial<RunActivity> = {},
): ChatRunCard {
  const activityId = `phase:${phase}`;
  const index = card.activities.findIndex((activity) => activity.id === activityId);
  const existing = index >= 0 ? card.activities[index] : {
    id: activityId,
    phase,
    status: "running" as const,
    updates: 0,
    toolCalls: 0,
    startedAt: now,
    updatedAt: now,
  };
  const activity = {
    ...existing,
    ...patch,
    updates: patch.updates ?? existing.updates,
    toolCalls: patch.toolCalls ?? existing.toolCalls,
    updatedAt: now,
  };
  const activities = index >= 0
    ? card.activities.map((item, itemIndex) => itemIndex === index ? activity : item)
    : [...card.activities, activity];
  return { ...card, activities };
}

function payloadType(payload: Record<string, unknown> | null): string {
  return asString(payload?.type);
}

function projectBusinessPayload(card: ChatRunCard, payload: Record<string, unknown>, now: string): ChatRunCard {
  const type = payloadType(payload);
  if (type === "reply") {
    const message = asRecord(payload.message);
    if (message?.id === -1) {
      const content = asString(message.content);
      const detail = trimDetail(content);
      const toolCalls = (content.match(/(?:Calling tool|调用工具)/g) ?? []).length;
      let next = upsertActivity(card, card.phase, now);
      const activity = next.activities.find((item) => item.id === `phase:${card.phase}`);
      next = upsertActivity(next, card.phase, now, {
        detail: detail || activity?.detail,
        status: "running",
        updates: (activity?.updates ?? 0) + 1,
        toolCalls: Math.max(activity?.toolCalls ?? 0, toolCalls),
      });
      return next;
    }
    return card;
  }
  if (type === "plan_approval_request") {
    return upsertActivity({ ...card, status: "waiting_user", phase: "wait_plan" }, "plan", now, {
      status: "waiting",
      detail: "plan_approval",
    });
  }
  if (type === "schema_approval_request") {
    return upsertActivity({ ...card, status: "waiting_user", phase: "wait_schema" }, "align_schema", now, {
      status: "waiting",
      detail: "schema_approval",
    });
  }
  if (type === "verification_approval_request") {
    return upsertActivity({ ...card, status: "waiting_user", phase: "wait_override" }, "verify", now, {
      status: "waiting",
      detail: "verification_approval",
    });
  }
  if (type === "permission_request" || type === "backend_permission_request") {
    return upsertActivity({ ...card, status: "waiting_user" }, card.phase, now, {
      status: "waiting",
      detail: "permission_approval",
    });
  }
  if (type === "widget") {
    return { ...card, artifactCount: Math.max(1, card.artifactCount) };
  }
  return card;
}

export function projectRunEvent(
  projection: ConversationProjection,
  event: RunEvent,
): ConversationProjection {
  const payload = asRecord(event.payload);
  const now = event.created_at;
  return withCard(projection, event.run_id, now, (current) => {
    let card = {
      ...current,
      updatedAt: now,
      attempt: event.attempt ?? current.attempt,
      modelTurns: Math.max(current.modelTurns, asNumber(event.model_usage?.model_turns)),
    };

    if (event.type === "run_created") {
      return {
        ...card,
        status: normalizedStatus(payload?.status, card.status),
        workflowType: asString(payload?.workflow_type) || card.workflowType,
      };
    }

    if (event.type === "step_started") {
      const phase = asString(payload?.step_key) || event.step_id || card.phase;
      card = { ...card, status: "running", phase };
      return upsertActivity(card, phase, now, { status: "running" });
    }

    if (event.type === "step_committed") {
      const phase = asString(payload?.step_key) || event.step_id || card.phase;
      const outcome = asRecord(payload?.outcome);
      const outcomeKind = asString(outcome?.kind);
      const nextStatus: RunStatus = outcomeKind === "wait"
        ? "waiting_user"
        : outcomeKind === "succeeded"
          ? "succeeded"
          : outcomeKind === "failed"
            ? "failed"
            : outcomeKind === "cancelled"
              ? "cancelled"
              : "running";
      const summary = asString(outcome?.summary) || card.summary;
      card = {
        ...card,
        phase,
        status: nextStatus,
        workflowType: asString(payload?.workflow_type) || card.workflowType,
        summary,
        finishedAt: TERMINAL_STATUSES.has(nextStatus) ? now : card.finishedAt,
        repairCount: Math.max(card.repairCount, asNumber(payload?.repair_count)),
        artifactCount: Math.max(card.artifactCount, asNumber(payload?.artifact_count)),
      };
      return upsertActivity(card, phase, now, {
        status: activityStatusFromRun(nextStatus),
        detail: trimDetail(summary) || undefined,
      });
    }

    if (event.type === "status_changed") {
      const status = normalizedStatus(payload?.to, card.status);
      return {
        ...card,
        status,
        finishedAt: TERMINAL_STATUSES.has(status) ? now : card.finishedAt,
      };
    }

    if (event.type === "progress") {
      const summary = asString(payload?.summary);
      return summary ? { ...card, summary } : card;
    }

    if (event.type === "interaction_requested") {
      return upsertActivity({ ...card, status: "waiting_user" }, card.phase, now, {
        status: "waiting",
      });
    }

    if (event.type === "interaction_resolved") {
      return upsertActivity({ ...card, status: "running" }, card.phase, now, {
        status: "completed",
      });
    }

    return payload ? projectBusinessPayload(card, payload, now) : card;
  });
}

export function projectRunSnapshot(
  projection: ConversationProjection,
  run: AmbientRun,
): ConversationProjection {
  return withCard(projection, run.id, run.created_at, (current) => {
    const input = asRecord(run.input);
    const content = trimDetail(input?.content);
    const error = run.error?.message ?? "";
    let card: ChatRunCard = {
      ...current,
      status: run.status,
      attempt: run.attempt,
      workflowType: run.workflow_type ?? current.workflowType,
      summary: run.summary || content || current.summary,
      error,
      createdAt: run.created_at,
      updatedAt: run.updated_at,
      finishedAt: run.finished_at ?? current.finishedAt,
      artifactCount: run.artifacts?.length ?? current.artifactCount,
    };
    const state = asRecord(run.state);
    const checkpoint = asRecord(run.checkpoint);
    const data = asRecord(state?.data);
    const budget = asRecord(state?.budget);
    const statePhase = asString(state?.phase);
    const lastStep = asString(checkpoint?.last_step);
    const phase = statePhase && statePhase !== "done"
      ? statePhase
      : lastStep || (run.status === "succeeded" && run.workflow_type?.startsWith("widget") ? "promote" : current.phase);
    card = {
      ...card,
      phase,
      modelTurns: Math.max(current.modelTurns, asNumber(budget?.model_turns)),
      repairCount: Math.max(current.repairCount, asNumber(data?.repair_count)),
    };
    return upsertActivity(card, phase, run.updated_at, {
      status: activityStatusFromRun(run.status),
      detail: trimDetail(run.summary || error) || undefined,
    });
  });
}

export function orderedRunCards(projection: ConversationProjection): ChatRunCard[] {
  return projection.order
    .map((runId) => projection.runs[runId])
    .filter((run): run is ChatRunCard => Boolean(run))
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
}

export function markRunCancelling(
  projection: ConversationProjection,
  runId: string,
): ConversationProjection {
  const card = projection.runs[runId];
  if (!card) return projection;
  return {
    ...projection,
    runs: {
      ...projection.runs,
      [runId]: { ...card, status: "cancel_requested", updatedAt: new Date().toISOString() },
    },
  };
}
