import { describe, expect, it } from "vitest";
import {
  EMPTY_CONVERSATION_PROJECTION,
  projectRunEvent,
  projectRunSnapshot,
} from "../../frontend/src/lib/chatProjection";
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
});
