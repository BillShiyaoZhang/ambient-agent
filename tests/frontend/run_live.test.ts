import { afterEach, describe, expect, it, vi } from "vitest";
import {
  normalizeRunLiveEvent,
  RunLiveEventBatcher,
  type RunLiveEvent,
} from "../../frontend/src/services/runLive";

function liveEvent(sequence: number): RunLiveEvent {
  return {
    schema_version: 1,
    run_id: "run-one",
    session_id: "session-one",
    step_id: "stage_code",
    attempt: 1,
    stream_id: "run-one:stage_code:1:activity",
    chunk_sequence: sequence,
    kind: "activity_delta",
    delta: String(sequence),
    replace: false,
    created_at: `2026-07-26T00:00:0${sequence}Z`,
  };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("Run live event transport", () => {
  it("accepts protocol v1 events only for the subscribed session", () => {
    const valid = liveEvent(1);
    expect(normalizeRunLiveEvent(valid, "session-one")).toEqual(valid);
    expect(normalizeRunLiveEvent(valid, "session-two")).toBeNull();
    expect(normalizeRunLiveEvent({ ...valid, schema_version: 2 })).toBeNull();
    expect(normalizeRunLiveEvent({ ...valid, chunk_sequence: 0 })).toBeNull();
    expect(normalizeRunLiveEvent({ ...valid, kind: "unknown" })).toBeNull();
  });

  it("flushes bursty deltas in a single render batch", () => {
    vi.useFakeTimers();
    const batches: RunLiveEvent[][] = [];
    const batcher = new RunLiveEventBatcher((events) => batches.push(events), 50);

    batcher.push(liveEvent(1));
    batcher.push(liveEvent(2));
    batcher.push(liveEvent(3));
    expect(batches).toHaveLength(0);

    vi.advanceTimersByTime(49);
    expect(batches).toHaveLength(0);
    vi.advanceTimersByTime(1);
    expect(batches).toEqual([[liveEvent(1), liveEvent(2), liveEvent(3)]]);
  });

  it("drops pending deltas when a session disconnects", () => {
    vi.useFakeTimers();
    const onFlush = vi.fn();
    const batcher = new RunLiveEventBatcher(onFlush, 50);
    batcher.push(liveEvent(1));

    batcher.clear();
    vi.advanceTimersByTime(100);

    expect(onFlush).not.toHaveBeenCalled();
  });
});
