import type { AmbientRun, RunEvent, RunStatus } from "../services/runs";
import type { RunLiveEvent, RunLiveEventKind } from "../services/runLive";

export interface RunActivity {
  id: string;
  phase: string;
  kind?: "phase" | "plan" | "schema" | "code" | "tool" | "verification" | "repair" | "artifact" | "approval";
  status: "running" | "completed" | "failed" | "waiting";
  title?: string;
  detail?: string;
  metadata?: Record<string, unknown>;
  updates: number;
  toolCalls: number;
  startedAt: string;
  updatedAt: string;
}

export interface RunDebugEvent {
  type: string;
  sequence: number;
  stepId?: string;
  createdAt: string;
  payload: string;
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
  debugEvents?: RunDebugEvent[];
}

export interface RunInteractionState {
  id: string;
  runId: string;
  kind: string;
  status: "pending" | "resolved" | "cancelled";
  payload: Record<string, unknown>;
  createdAt: string;
  resolvedAt?: string;
}

export interface LiveStreamState {
  streamId: string;
  runId: string;
  stepId: string;
  kind: RunLiveEventKind;
  text: string;
  lastSequence: number;
  hasGap: boolean;
  updatedAt: string;
  tool?: string;
  toolStatus?: string;
}

export interface ConversationProjection {
  order: string[];
  runs: Record<string, ChatRunCard>;
  liveStreams: Record<string, LiveStreamState>;
  liveStepWatermarks: Record<string, number>;
  interactions: Record<string, RunInteractionState>;
  seenEventIds: Record<string, true>;
}

export const EMPTY_CONVERSATION_PROJECTION: ConversationProjection = {
  order: [],
  runs: {},
  liveStreams: {},
  liveStepWatermarks: {},
  interactions: {},
  seenEventIds: {},
};

const TERMINAL_STATUSES = new Set<RunStatus>(["succeeded", "failed", "cancelled", "needs_attention"]);
const liveStepKey = (runId: string, stepId: string): string => `${runId}:${stepId}`;

function markLiveStepCompleted(
  projection: ConversationProjection,
  runId: string,
  stepId: string,
  attempt: number,
): ConversationProjection {
  if (!stepId || attempt < 1) return projection;
  const key = liveStepKey(runId, stepId);
  if ((projection.liveStepWatermarks[key] ?? 0) >= attempt) return projection;
  return {
    ...projection,
    liveStepWatermarks: {
      ...projection.liveStepWatermarks,
      [key]: attempt,
    },
  };
}

const asRecord = (value: unknown): Record<string, unknown> | null => (
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const asString = (value: unknown): string => typeof value === "string" ? value : "";

const asNumber = (value: unknown): number => (
  typeof value === "number" && Number.isFinite(value) ? value : 0
);

const SENSITIVE_DEBUG_KEY = /(?:authorization|credential|password|secret|token|api[_-]?key)/i;

function boundedDebugPayload(value: unknown): string {
  let serialized = "";
  try {
    serialized = JSON.stringify(value, (key, item) => (
      SENSITIVE_DEBUG_KEY.test(key) ? "[REDACTED]" : item
    ), 2);
  } catch {
    serialized = String(value);
  }
  return serialized.length > 2_400
    ? `${serialized.slice(0, 2_400)}\n…[truncated]`
    : serialized;
}

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
    debugEvents: [],
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
    ...projection,
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
  const activityId = patch.id ?? `phase:${phase}`;
  const index = card.activities.findIndex((activity) => activity.id === activityId);
  const existing = index >= 0 ? card.activities[index] : {
    id: activityId,
    phase,
    kind: "phase" as const,
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

function appendDebugEvent(card: ChatRunCard, event: RunEvent): ChatRunCard {
  const debugEvent: RunDebugEvent = {
    type: event.type,
    sequence: event.sequence,
    stepId: event.step_id || undefined,
    createdAt: event.created_at,
    payload: boundedDebugPayload(event.payload),
  };
  const currentDebugEvents = card.debugEvents ?? [];
  const existingIndex = currentDebugEvents.findIndex((item) => (
    item.sequence === debugEvent.sequence && item.type === debugEvent.type
  ));
  const debugEvents = existingIndex >= 0
    ? currentDebugEvents.map((item, index) => index === existingIndex ? debugEvent : item)
    : [...currentDebugEvents, debugEvent].slice(-24);
  return { ...card, debugEvents };
}

function payloadType(payload: Record<string, unknown> | null): string {
  return asString(payload?.type);
}

function projectBusinessPayload(card: ChatRunCard, payload: Record<string, unknown>, now: string): ChatRunCard {
  const type = payloadType(payload);
  if (type === "activity_updated") {
    const activityId = asString(payload.activity_id);
    const activityType = asString(payload.activity_type);
    const status = asString(payload.status);
    if (
      !activityId
      || !["plan", "schema", "code", "tool", "verification", "repair", "artifact", "approval"].includes(activityType)
      || !["running", "completed", "failed", "waiting"].includes(status)
    ) return card;
    return upsertActivity(card, card.phase, now, {
      id: activityId,
      kind: activityType as RunActivity["kind"],
      status: status as RunActivity["status"],
      title: asString(payload.summary),
      detail: trimDetail(payload.detail) || undefined,
      metadata: asRecord(payload.metadata) ?? undefined,
    });
  }
  if (["tool_started", "tool_succeeded", "tool_failed", "tool_cancelled"].includes(type)) {
    const tool = asString(payload.tool) || "tool";
    const activityId = `tool:${card.phase}:${tool}`;
    const existing = card.activities.find((item) => item.id === activityId);
    const durationMs = asNumber(payload.duration_ms);
    const status: RunActivity["status"] = type === "tool_started"
      ? "running"
      : type === "tool_succeeded"
        ? "completed"
        : "failed";
    const detail = type === "tool_failed"
      ? asString(payload.error) || undefined
      : durationMs > 0
        ? `${Math.round(durationMs)} ms`
        : undefined;
    return upsertActivity(card, card.phase, now, {
      id: activityId,
      kind: "tool",
      status,
      title: tool,
      detail,
      metadata: {
        effect: payload.effect,
        duration_ms: durationMs || undefined,
        output_bytes: asNumber(payload.output_bytes) || undefined,
      },
      toolCalls: (existing?.toolCalls ?? 0) + (type === "tool_started" ? 1 : 0),
      updates: (existing?.updates ?? 0) + 1,
    });
  }
  if (type === "artifact_ready") {
    const artifactId = asString(payload.artifact_id);
    return upsertActivity(
      { ...card, artifactCount: Math.max(1, card.artifactCount) },
      card.phase,
      now,
      {
        id: `artifact:${artifactId || "ready"}`,
        kind: "artifact",
        status: "completed",
        title: asString(payload.summary) || "Artifact ready",
        detail: asString(payload.title) || artifactId || undefined,
        metadata: {
          artifact_type: payload.artifact_type,
          artifact_id: artifactId,
        },
      },
    );
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

const INLINE_INTERACTION_TYPES: Record<string, string> = {
  plan_approval_request: "plan_approval",
  schema_approval_request: "schema_approval",
  verification_approval_request: "verification_approval",
};

function projectInteractionRequest(
  projection: ConversationProjection,
  event: RunEvent,
  payload: Record<string, unknown> | null,
): ConversationProjection {
  if (!payload) return projection;
  const payloadTypeValue = payloadType(payload);
  const kind = INLINE_INTERACTION_TYPES[payloadTypeValue];
  const interactionId = asString(payload.request_id);
  if (!kind || !interactionId) return projection;
  return {
    ...projection,
    interactions: {
      ...projection.interactions,
      [interactionId]: {
        id: interactionId,
        runId: event.run_id,
        kind,
        status: "pending",
        payload,
        createdAt: event.created_at,
      },
    },
  };
}

function projectInteractionResolution(
  projection: ConversationProjection,
  interactionId: string,
  status: string,
  now: string,
): ConversationProjection {
  if (!interactionId) return projection;
  const existing = projection.interactions[interactionId];
  const normalized = status === "cancelled" ? "cancelled" : "resolved";
  const interactions = existing
    ? {
        ...projection.interactions,
        [interactionId]: {
          ...existing,
          status: normalized as RunInteractionState["status"],
          resolvedAt: now,
        },
      }
    : projection.interactions;
  const runId = existing?.runId;
  if (!runId || !projection.runs[runId]) return { ...projection, interactions };
  const card = projection.runs[runId];
  return {
    ...projection,
    interactions,
    runs: {
      ...projection.runs,
      [runId]: {
        ...card,
        activities: card.activities.map((activity) => (
          activity.metadata?.interaction_id === interactionId
            ? { ...activity, status: normalized === "resolved" ? "completed" : "failed", updatedAt: now }
            : activity
        )),
      },
    },
  };
}

export function projectRunEvent(
  projection: ConversationProjection,
  event: RunEvent,
): ConversationProjection {
  if (projection.seenEventIds[event.event_id]) return projection;
  const payload = asRecord(event.payload);
  const now = event.created_at;
  let next = withCard(projection, event.run_id, now, (current) => {
    let card = {
      ...current,
      updatedAt: now,
      attempt: event.attempt ?? current.attempt,
      modelTurns: Math.max(current.modelTurns, asNumber(event.model_usage?.model_turns)),
    };
    if (
      event.step_id
      && ["activity_updated", "tool_started", "tool_succeeded", "tool_failed", "tool_cancelled", "artifact_ready"].includes(event.type)
    ) {
      card = { ...card, phase: event.step_id };
    }

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
  next = withCard(next, event.run_id, now, (card) => appendDebugEvent(card, event));
  next = projectInteractionRequest(next, event, payload);
  if (event.type === "interaction_requested") {
    const interactionId = asString(payload?.interaction_id);
    if (interactionId && !next.interactions[interactionId]) {
      next = {
        ...next,
        interactions: {
          ...next.interactions,
          [interactionId]: {
            id: interactionId,
            runId: event.run_id,
            kind: asString(payload?.type) || "interaction",
            status: "pending",
            payload: payload ?? {},
            createdAt: now,
          },
        },
      };
    }
  } else if (event.type === "interaction_resolved") {
    next = projectInteractionResolution(
      next,
      asString(payload?.interaction_id),
      asString(payload?.status),
      now,
    );
  }
  const replyMessage = asRecord(payload?.message);
  const positiveReply = payloadType(payload) === "reply"
    && replyMessage !== null
    && asNumber(replyMessage.id) > 0;
  if (event.type === "step_committed") {
    const phase = asString(payload?.step_key) || event.step_id;
    next = clearLiveStreams(next, (stream) => (
      stream.runId === event.run_id && (!phase || stream.stepId === phase)
    ));
    next = markLiveStepCompleted(
      next,
      event.run_id,
      phase || "",
      asNumber(event.attempt) || asNumber(payload?.attempt),
    );
  } else if (
    event.type === "interaction_requested"
    || (event.type === "status_changed" && ["waiting_user", "cancelled", "failed", "needs_attention", "succeeded"].includes(asString(payload?.to)))
  ) {
    next = clearLiveStreams(next, (stream) => stream.runId === event.run_id);
  } else if (positiveReply) {
    next = clearLiveStreams(next, (stream) => (
      stream.runId === event.run_id && stream.kind === "assistant_message_delta"
    ));
    next = markLiveStepCompleted(
      next,
      event.run_id,
      event.step_id || "",
      asNumber(event.attempt),
    );
  }
  return {
    ...next,
    seenEventIds: {
      ...next.seenEventIds,
      [event.event_id]: true,
    },
  };
}

export function projectRunSnapshot(
  projection: ConversationProjection,
  run: AmbientRun,
): ConversationProjection {
  const replayed = (run.events ?? []).reduce(projectRunEvent, projection);
  const checkpoint = asRecord(run.checkpoint);
  const lastStep = asString(checkpoint?.last_step);
  const next = withCard(replayed, run.id, run.created_at, (current) => {
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
    const data = asRecord(state?.data);
    const budget = asRecord(state?.budget);
    const statePhase = asString(state?.phase);
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
  const checkpointAttempt = asNumber(checkpoint?.attempt);
  const reconciled = lastStep
    ? markLiveStepCompleted(next, run.id, lastStep, checkpointAttempt)
    : next;
  let withInteractions = reconciled;
  for (const interaction of run.interactions ?? []) {
    const interactionPayload = asRecord(interaction.payload) ?? {};
    withInteractions = {
      ...withInteractions,
      interactions: {
        ...withInteractions.interactions,
        [interaction.id]: {
          id: interaction.id,
          runId: run.id,
          kind: interaction.type,
          status: interaction.status === "pending"
            ? "pending"
            : interaction.status === "cancelled"
              ? "cancelled"
              : "resolved",
          payload: interactionPayload,
          createdAt: interaction.created_at,
          resolvedAt: interaction.resolved_at ?? undefined,
        },
      },
    };
  }
  return ["queued", "running"].includes(run.status)
    ? withInteractions
    : clearLiveStreams(withInteractions, (stream) => stream.runId === run.id);
}

export function projectLiveRunEvent(
  projection: ConversationProjection,
  event: RunLiveEvent,
): ConversationProjection {
  const currentStream = projection.liveStreams[event.stream_id];
  if (currentStream && event.chunk_sequence <= currentStream.lastSequence) return projection;
  if (
    event.attempt <= (projection.liveStepWatermarks[liveStepKey(event.run_id, event.step_id)] ?? 0)
  ) return projection;
  const currentRun = projection.runs[event.run_id];
  if (
    currentRun
    && ["waiting_user", "cancel_requested", "needs_attention", "succeeded", "failed", "cancelled"].includes(currentRun.status)
  ) return projection;

  const next = withCard(projection, event.run_id, event.created_at, (card) => {
    const status = card.status === "queued" ? "running" : card.status;
    return { ...card, status, phase: event.step_id || card.phase, updatedAt: event.created_at };
  });
  const previousText = currentStream?.text ?? "";
  const text = (event.replace ? event.delta : previousText + event.delta).slice(-12_000);
  return {
    ...next,
    liveStreams: {
      ...next.liveStreams,
      [event.stream_id]: {
        streamId: event.stream_id,
        runId: event.run_id,
        stepId: event.step_id,
        kind: event.kind,
        text,
        lastSequence: event.chunk_sequence,
        hasGap: currentStream?.hasGap === true
          || (currentStream === undefined
            ? event.chunk_sequence > 1
            : event.chunk_sequence > currentStream.lastSequence + 1),
        updatedAt: event.created_at,
        tool: event.tool,
        toolStatus: event.tool_status,
      },
    },
  };
}

export function projectLiveRunEvents(
  projection: ConversationProjection,
  events: RunLiveEvent[],
): ConversationProjection {
  return events.reduce(projectLiveRunEvent, projection);
}

export function clearLiveStreams(
  projection: ConversationProjection,
  predicate: (stream: LiveStreamState) => boolean = () => true,
): ConversationProjection {
  const liveStreams = Object.fromEntries(
    Object.entries(projection.liveStreams).filter(([, stream]) => !predicate(stream))
  );
  return Object.keys(liveStreams).length === Object.keys(projection.liveStreams).length
    ? projection
    : { ...projection, liveStreams };
}

export function liveStreamsForRun(
  streams: Record<string, LiveStreamState>,
  runId: string,
): LiveStreamState[] {
  return Object.values(streams)
    .filter((stream) => stream.runId === runId)
    .sort((left, right) => left.updatedAt.localeCompare(right.updatedAt));
}

export function interactionsForRun(
  interactions: Record<string, RunInteractionState>,
  runId: string,
): RunInteractionState[] {
  return Object.values(interactions)
    .filter((interaction) => interaction.runId === runId)
    .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
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
