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
  it("groups streaming agent updates into one phase activity", () => {
    let projection = projectRunEvent(
      EMPTY_CONVERSATION_PROJECTION,
      event(1, "step_started", { step_key: "stage_code", attempt: 1, lease_epoch: 1 }),
    );
    projection = projectRunEvent(
      projection,
      event(2, "agent_update", {
        type: "reply",
        message: { id: -1, sender: "agent", content: "🛠️ Calling tool: write\n正在写入组件文件" },
      }),
    );
    projection = projectRunEvent(
      projection,
      event(3, "agent_update", {
        type: "reply",
        message: { id: -1, sender: "agent", content: "正在检查组件样式" },
      }),
    );

    const card = projection.runs["run-one"];
    expect(projection.order).toEqual(["run-one"]);
    expect(card.phase).toBe("stage_code");
    expect(card.activities).toHaveLength(1);
    expect(card.activities[0]).toMatchObject({
      detail: "正在检查组件样式",
      updates: 2,
      toolCalls: 1,
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
});
