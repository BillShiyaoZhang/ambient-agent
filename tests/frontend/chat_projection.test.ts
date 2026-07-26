import { describe, expect, it } from "vitest";
import {
  clearLiveStreams,
  EMPTY_CONVERSATION_PROJECTION,
  projectLiveRunEvent,
  projectRunEvent,
  projectRunSnapshot,
} from "../../frontend/src/lib/chatProjection";
import type { RunLiveEvent } from "../../frontend/src/services/runLive";
import type { RunEvent } from "../../frontend/src/services/runs";

function event(
  sequence: number,
  type: string,
  payload: unknown,
  createdAt = `2026-07-26T00:00:0${sequence}Z`,
): RunEvent {
  return {
    sequence,
    event_id: `event-${sequence}`,
    schema_version: 1,
    stream_epoch: "epoch-one",
    run_id: "run-one",
    session_id: "session-one",
    step_id: "stage_code",
    attempt: 1,
    trace_id: "trace-one",
    type,
    payload,
    created_at: createdAt,
  };
}

function liveEvent(
  sequence: number,
  delta: string,
  replace = false,
): RunLiveEvent {
  return {
    schema_version: 1,
    run_id: "run-one",
    session_id: "session-one",
    step_id: "stage_code",
    attempt: 1,
    stream_id: "run-one:stage_code:1:activity",
    chunk_sequence: sequence,
    kind: "activity_delta",
    delta,
    replace,
    created_at: `2026-07-26T00:00:0${sequence}Z`,
  };
}

describe("conversation Run projection", () => {
  it("updates structured progress in place without transient chat replies", () => {
    let projection = projectRunEvent(
      EMPTY_CONVERSATION_PROJECTION,
      event(1, "activity_updated", {
        type: "activity_updated",
        activity_id: "code:generation",
        activity_type: "code",
        status: "running",
        summary: "Generating staged App",
        detail: "正在准备组件文件",
        metadata: {},
      }),
    );
    projection = projectRunEvent(
      projection,
      event(2, "activity_updated", {
        type: "activity_updated",
        activity_id: "code:generation",
        activity_type: "code",
        status: "running",
        summary: "Generating staged App",
        detail: "正在检查组件样式",
        metadata: {},
      }),
    );

    const card = projection.runs["run-one"];
    expect(projection.order).toEqual(["run-one"]);
    expect(card.phase).toBe("stage_code");
    expect(card.activities).toHaveLength(1);
    expect(card.activities[0]).toMatchObject({
      id: "code:generation",
      kind: "code",
      detail: "正在检查组件样式",
    });
  });

  it("restores terminal metadata and repair count from a durable snapshot", () => {
    const projection = projectRunSnapshot(EMPTY_CONVERSATION_PROJECTION, {
      id: "run-one",
      owner_id: "owner",
      action_id: "generate",
      action_title: "Generate",
      source_type: "chat",
      source_id: "session-one",
      adapter_type: "internal",
      workflow_type: "widget_generation",
      runtime_id: "runtime",
      status: "succeeded",
      progress: 1,
      summary: "App verified and published",
      input: { content: "Build a tracker" },
      state: { phase: "done", budget: { model_turns: 7 }, data: { repair_count: 2 } },
      checkpoint: { last_step: "promote" },
      artifacts: [{ type: "widget", id: "widget-one" }],
      attempt: 2,
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:01:00Z",
      finished_at: "2026-07-26T00:01:00Z",
    });

    expect(projection.runs["run-one"]).toMatchObject({
      status: "succeeded",
      phase: "promote",
      workflowType: "widget_generation",
      repairCount: 2,
      modelTurns: 7,
      artifactCount: 1,
      attempt: 2,
    });
  });

  it("deduplicates and orders live deltas, then clears them on durable commit", () => {
    let projection = projectLiveRunEvent(
      EMPTY_CONVERSATION_PROJECTION,
      liveEvent(1, "正在"),
    );
    const afterFirst = projection;
    projection = projectLiveRunEvent(projection, liveEvent(1, "重复"));
    expect(projection).toBe(afterFirst);

    projection = projectLiveRunEvent(projection, liveEvent(3, "生成"));
    const stream = projection.liveStreams["run-one:stage_code:1:activity"];
    expect(stream.text).toBe("正在生成");
    expect(stream.lastSequence).toBe(3);
    expect(stream.hasGap).toBe(true);

    projection = projectLiveRunEvent(projection, liveEvent(2, "乱序"));
    expect(projection.liveStreams["run-one:stage_code:1:activity"].text).toBe("正在生成");

    projection = projectRunEvent(
      projection,
      event(4, "step_committed", { step_key: "stage_code", attempt: 1 }),
    );
    expect(projection.liveStreams).toEqual({});

    const afterLateEvent = projectLiveRunEvent(projection, liveEvent(4, "迟到"));
    expect(afterLateEvent).toBe(projection);

    const retryEvent = {
      ...liveEvent(1, "重新尝试"),
      attempt: 2,
      stream_id: "run-one:stage_code:2:activity",
    };
    projection = projectLiveRunEvent(projection, retryEvent);
    expect(projection.liveStreams[retryEvent.stream_id].text).toBe("重新尝试");
  });

  it("replaces a reset snapshot and clears live state explicitly", () => {
    let projection = projectLiveRunEvent(
      EMPTY_CONVERSATION_PROJECTION,
      liveEvent(1, "旧内容"),
    );
    projection = projectLiveRunEvent(projection, liveEvent(2, "新内容", true));
    expect(projection.liveStreams["run-one:stage_code:1:activity"].text).toBe("新内容");

    projection = clearLiveStreams(projection);
    expect(projection.liveStreams).toEqual({});
  });

  it("projects structured verification, repair, tool, and artifact activities", () => {
    let projection = projectRunEvent(
      EMPTY_CONVERSATION_PROJECTION,
      event(1, "activity_updated", {
        type: "activity_updated",
        activity_id: "verification:contract",
        activity_type: "verification",
        status: "failed",
        summary: "Verification found required changes",
        detail: "Unknown field temperature",
        metadata: { finding_count: 1 },
      }),
    );
    projection = projectRunEvent(
      projection,
      event(2, "tool_started", {
        type: "tool_started",
        tool: "read_graph",
        arguments: { api_token: "must-not-render" },
      }),
    );
    projection = projectRunEvent(
      projection,
      event(3, "tool_succeeded", {
        type: "tool_succeeded",
        tool: "read_graph",
        duration_ms: 42,
      }),
    );
    projection = projectRunEvent(
      projection,
      event(4, "artifact_ready", {
        type: "artifact_ready",
        artifact_type: "app",
        artifact_id: "weather-app",
        title: "Weather App",
        summary: "Verified App is ready",
      }),
    );

    const card = projection.runs["run-one"];
    expect(card.activities.find((activity) => activity.id === "verification:contract")).toMatchObject({
      kind: "verification",
      status: "failed",
      title: "Verification found required changes",
    });
    expect(card.activities.find((activity) => activity.id === "tool:stage_code:read_graph")).toMatchObject({
      kind: "tool",
      status: "completed",
      toolCalls: 1,
      updates: 2,
      detail: "42 ms",
    });
    expect(card.activities.find((activity) => activity.id === "artifact:weather-app")).toMatchObject({
      kind: "artifact",
      status: "completed",
    });
    expect(card.artifactCount).toBe(1);
    expect(card.debugEvents?.at(1)?.payload).toContain("[REDACTED]");
    expect(card.debugEvents?.at(1)?.payload).not.toContain("must-not-render");
  });

  it("keeps ordinary approvals as durable inline interaction records", () => {
    let projection = projectRunEvent(
      EMPTY_CONVERSATION_PROJECTION,
      event(1, "plan_approval_request", {
        type: "plan_approval_request",
        request_id: "interaction-one",
        app_id: "weather-app",
        plan: "Build a weather dashboard",
      }),
    );

    expect(projection.interactions["interaction-one"]).toMatchObject({
      runId: "run-one",
      kind: "plan_approval",
      status: "pending",
    });

    projection = projectRunEvent(
      projection,
      event(2, "interaction_resolved", {
        interaction_id: "interaction-one",
        run_version: 2,
        status: "resolved",
      }),
    );
    expect(projection.interactions["interaction-one"].status).toBe("resolved");
  });

  it("rehydrates detailed Run snapshots without double-projecting streamed events", () => {
    const structured = event(7, "activity_updated", {
      type: "activity_updated",
      activity_id: "repair:auto",
      activity_type: "repair",
      status: "completed",
      summary: "Automatic repair completed",
      metadata: { repair_count: 1 },
    });
    let projection = projectRunSnapshot(EMPTY_CONVERSATION_PROJECTION, {
      id: "run-one",
      owner_id: "owner",
      action_id: "generate",
      action_title: "Generate",
      source_type: "chat",
      source_id: "session-one",
      adapter_type: "internal",
      workflow_type: "widget_generation",
      runtime_id: "runtime",
      status: "running",
      progress: 0.5,
      summary: "Repairing",
      input: { content: "Build an app" },
      state: { phase: "stage_code", data: {} },
      checkpoint: null,
      artifacts: [],
      interactions: [{
        id: "interaction-snapshot",
        run_id: "run-one",
        type: "plan_approval",
        prompt: "Approve plan",
        payload: {
          type: "plan_approval_request",
          request_id: "interaction-snapshot",
          plan: "Plan from snapshot",
        },
        status: "pending",
        created_at: "2026-07-26T00:00:06Z",
      }],
      events: [structured],
      attempt: 1,
      created_at: "2026-07-26T00:00:00Z",
      updated_at: "2026-07-26T00:00:07Z",
    });

    expect(projection.runs["run-one"].activities.find((item) => item.id === "repair:auto")).toBeDefined();
    expect(projection.interactions["interaction-snapshot"].status).toBe("pending");
    const beforeDuplicate = projection;
    projection = projectRunEvent(projection, structured);
    expect(projection).toBe(beforeDuplicate);
  });
});
