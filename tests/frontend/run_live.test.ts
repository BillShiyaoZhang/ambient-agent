import { afterEach, describe, expect, it, vi } from "vitest";
import {
  normalizeRunLiveEvent,
  RunLiveEventBatcher,
  RunLiveService,
  type RunLiveEvent,
} from "../../frontend/src/services/runLive";

class MockLiveWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockLiveWebSocket[] = [];

  readyState = MockLiveWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;

  constructor(public url: string) {
    MockLiveWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = MockLiveWebSocket.OPEN;
    this.onopen?.();
  }

  closeWith(code: number): void {
    this.readyState = MockLiveWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  close(): void {
    this.closeWith(1000);
  }
}

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
  vi.unstubAllGlobals();
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

  it("does not reconnect a normally closed live stream", () => {
    vi.useFakeTimers();
    MockLiveWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockLiveWebSocket);
    const statuses: string[] = [];
    const service = new RunLiveService();
    const unsubscribe = service.subscribe(
      "session-one",
      vi.fn(),
      vi.fn(),
      (status) => statuses.push(status),
    );
    const socket = MockLiveWebSocket.instances[0];
    socket.open();
    socket.closeWith(1000);
    vi.advanceTimersByTime(60_000);

    expect(MockLiveWebSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("disconnected");
    unsubscribe();
  });

  it("bounds abnormal live-stream reconnects with exponential backoff", () => {
    vi.useFakeTimers();
    MockLiveWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockLiveWebSocket);
    const statuses: string[] = [];
    const service = new RunLiveService();
    const unsubscribe = service.subscribe(
      "session-one",
      vi.fn(),
      vi.fn(),
      (status) => statuses.push(status),
    );
    MockLiveWebSocket.instances[0].open();

    const delays = [500, 1_000, 2_000, 4_000, 8_000];
    for (const [index, delay] of delays.entries()) {
      MockLiveWebSocket.instances[index].closeWith(1006);
      vi.advanceTimersByTime(delay - 1);
      expect(MockLiveWebSocket.instances).toHaveLength(index + 1);
      vi.advanceTimersByTime(1);
      expect(MockLiveWebSocket.instances).toHaveLength(index + 2);
    }
    MockLiveWebSocket.instances.at(-1)!.closeWith(1006);
    vi.advanceTimersByTime(60_000);

    expect(MockLiveWebSocket.instances).toHaveLength(6);
    expect(statuses.at(-1)).toBe("unavailable");
    unsubscribe();
  });

  it("lets the user explicitly recover an exhausted live stream", () => {
    vi.useFakeTimers();
    MockLiveWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockLiveWebSocket);
    const service = new RunLiveService();
    const unsubscribe = service.subscribe("session-one", vi.fn(), vi.fn());

    for (const delay of [500, 1_000, 2_000, 4_000, 8_000]) {
      MockLiveWebSocket.instances.at(-1)!.closeWith(1006);
      vi.advanceTimersByTime(delay);
    }
    MockLiveWebSocket.instances.at(-1)!.closeWith(1006);
    expect(service.getConnectionState()).toBe("unavailable");

    expect(service.retryConnection()).toBe(true);
    expect(MockLiveWebSocket.instances).toHaveLength(7);
    MockLiveWebSocket.instances.at(-1)!.open();
    expect(service.getConnectionState()).toBe("connected");
    expect(service.retryConnection()).toBe(false);
    unsubscribe();
  });
});
