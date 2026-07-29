import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunService, type RunEvent } from "../../frontend/src/services/runs";

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.OPEN;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  emit(value: unknown): void {
    const data = typeof value === "string" ? value : JSON.stringify(value);
    this.onmessage?.({ data } as MessageEvent<string>);
  }
}

function event(sequence: number, overrides: Partial<RunEvent> = {}): RunEvent {
  return {
    sequence,
    event_id: `event-${sequence}`,
    schema_version: 1,
    stream_epoch: "epoch-one",
    run_id: "run-one",
    session_id: null,
    step_id: null,
    attempt: null,
    trace_id: "trace-one",
    type: "status_changed",
    payload: { status: "running" },
    created_at: "2026-07-19T00:00:00Z",
    ...overrides,
  };
}

describe("RunService event stream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("persists an epoch-aware cursor and safely delivers opaque future events", () => {
    const service = new RunService();
    const listener = vi.fn();
    const unsubscribe = service.subscribe(listener);
    const socket = MockWebSocket.instances[0];

    expect(socket.url).toContain("after_sequence=0");
    socket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 0 });
    expect(() => socket.emit("not json")).not.toThrow();
    socket.emit({ type: "future_control_frame", data: "ignored" });
    socket.emit({
      type: "run_event",
      event: event(1, { schema_version: 99, type: "future_event_type" }),
    });

    expect(listener).toHaveBeenCalledOnce();
    expect(listener.mock.calls[0][0]).toMatchObject({
      schema_version: 99,
      stream_epoch: "epoch-one",
      sequence: 1,
      type: "future_event_type",
    });
    expect(sessionStorage.getItem("ambient_run_stream_epoch")).toBe("epoch-one");
    expect(sessionStorage.getItem("ambient_run_sequence")).toBe("1");
    unsubscribe();
  });

  it("unwraps the /ws/runs transport frame exactly once and preserves the canonical payload", () => {
    const service = new RunService();
    const listener = vi.fn();
    const unsubscribe = service.subscribe(listener);
    const socket = MockWebSocket.instances[0];
    const payload = {
      type: "reply",
      message: {
        id: 42,
        sender: "agent",
        role: "agent",
        content: "Projected from the canonical stream",
      },
    };
    const canonical = event(1, {
      type: "reply",
      session_id: "session-one",
      payload,
    });

    socket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 1 });
    socket.emit({ type: "run_event", event: canonical });

    expect(listener).toHaveBeenCalledOnce();
    expect(listener.mock.calls[0][0]).toMatchObject({
      event_id: "event-1",
      type: "reply",
      session_id: "session-one",
      payload,
    });
    expect(listener.mock.calls[0][0]).not.toHaveProperty("event");
    expect(listener.mock.calls[0][0].payload).toStrictEqual(payload);
    unsubscribe();
  });

  it("uses the persisted epoch and sequence when reconnecting", () => {
    sessionStorage.setItem("ambient_run_stream_epoch", "epoch-stable");
    sessionStorage.setItem("ambient_run_sequence", "7");
    const service = new RunService();
    const unsubscribe = service.subscribe(() => {});

    const url = new URL(MockWebSocket.instances[0].url);
    expect(url.searchParams.get("stream_epoch")).toBe("epoch-stable");
    expect(url.searchParams.get("after_sequence")).toBe("7");
    unsubscribe();
  });

  it("does not trust a legacy sequence-only cursor across a possible database reset", () => {
    sessionStorage.setItem("ambient_run_sequence", "900");
    const service = new RunService();
    const unsubscribe = service.subscribe(() => {});

    const url = new URL(MockWebSocket.instances[0].url);
    expect(url.searchParams.has("stream_epoch")).toBe(false);
    expect(url.searchParams.get("after_sequence")).toBe("0");
    expect(sessionStorage.getItem("ambient_run_sequence")).toBe("0");
    unsubscribe();
  });

  it("resets a stale cursor and replays from zero after the database epoch changes", () => {
    sessionStorage.setItem("ambient_run_stream_epoch", "old-database");
    sessionStorage.setItem("ambient_run_sequence", "900");
    const service = new RunService();
    const listener = vi.fn();
    const unsubscribe = service.subscribe(listener);

    MockWebSocket.instances[0].emit({
      type: "run_stream_reset",
      stream_epoch: "new-database",
      reason: "epoch_mismatch",
    });
    expect(sessionStorage.getItem("ambient_run_stream_epoch")).toBe("new-database");
    expect(sessionStorage.getItem("ambient_run_sequence")).toBe("0");

    vi.runOnlyPendingTimers();
    const replaySocket = MockWebSocket.instances[1];
    const replayUrl = new URL(replaySocket.url);
    expect(replayUrl.searchParams.get("stream_epoch")).toBe("new-database");
    expect(replayUrl.searchParams.get("after_sequence")).toBe("0");

    replaySocket.emit({ type: "run_stream_ready", stream_epoch: "new-database", latest_sequence: 1 });
    replaySocket.emit({
      type: "run_event",
      event: event(1, { stream_epoch: "new-database", event_id: "new-event" }),
    });
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it("detects a sequence gap and replays without delivering duplicates", () => {
    const service = new RunService();
    const listener = vi.fn();
    const gapListener = vi.fn();
    window.addEventListener("ambient_run_stream_gap", gapListener);
    const unsubscribe = service.subscribe(listener);
    const firstSocket = MockWebSocket.instances[0];
    firstSocket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 3 });
    firstSocket.emit({ type: "run_event", event: event(1) });
    firstSocket.emit({ type: "run_event", event: event(3) });

    expect(listener).toHaveBeenCalledTimes(1);
    expect(gapListener).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem("ambient_run_sequence")).toBe("1");

    vi.runOnlyPendingTimers();
    const replaySocket = MockWebSocket.instances[1];
    expect(new URL(replaySocket.url).searchParams.get("after_sequence")).toBe("1");
    replaySocket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 3 });
    replaySocket.emit({ type: "run_event", event: event(1) });
    replaySocket.emit({ type: "run_event", event: event(2) });
    replaySocket.emit({ type: "run_event", event: event(3) });

    expect(listener.mock.calls.map(([received]) => received.sequence)).toEqual([1, 2, 3]);
    expect(sessionStorage.getItem("ambient_run_sequence")).toBe("3");
    unsubscribe();
    window.removeEventListener("ambient_run_stream_gap", gapListener);
  });

  it("accepts a confirmed server-side sequence hole after one replay attempt", () => {
    const service = new RunService();
    const listener = vi.fn();
    const unsubscribe = service.subscribe(listener);
    const firstSocket = MockWebSocket.instances[0];
    firstSocket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 3 });
    firstSocket.emit({ type: "run_event", event: event(1) });
    firstSocket.emit({ type: "run_event", event: event(3) });

    vi.runOnlyPendingTimers();
    const replaySocket = MockWebSocket.instances[1];
    replaySocket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 3 });
    replaySocket.emit({ type: "run_event", event: event(3) });

    expect(listener.mock.calls.map(([received]) => received.sequence)).toEqual([1, 3]);
    expect(sessionStorage.getItem("ambient_run_sequence")).toBe("3");
    unsubscribe();
  });

  it("ignores malformed envelopes without advancing the cursor", () => {
    const service = new RunService();
    const listener = vi.fn();
    const unsubscribe = service.subscribe(listener);
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 0 });
    socket.emit({ type: "run_event", event: { sequence: 1, type: "missing_fields" } });
    socket.emit({ type: "run_event", event: event(1, { schema_version: 0 }) });

    expect(listener).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("ambient_run_sequence")).toBe("0");
    unsubscribe();
  });

  it("rebuilds a correlated capability completion when replay events overtake a failed lookup", async () => {
    const terminalResponse = {
      ok: true,
      json: async () => ({
        id: "run-one",
        status: "succeeded",
        result: { sum: 15 },
        correlation: {
          projection_type: "capability_call_response",
          catalog_id: "calculator",
          call_id: "call-after-restart",
        },
      }),
    } as Response;
    const fetchRun = vi.fn()
      .mockRejectedValueOnce(new Error("backend is still restarting"))
      .mockResolvedValue(terminalResponse);
    vi.stubGlobal("fetch", fetchRun);
    const service = new RunService();
    const completion = vi.fn();
    const eventName = "capability_call_response:calculator:call-after-restart";
    window.addEventListener(eventName, completion);
    const unsubscribe = service.subscribe(() => {});
    const socket = MockWebSocket.instances[0];

    socket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 1 });
    socket.emit({
      type: "run_event",
      event: event(1, {
        type: "run_created",
        payload: {
          status: "queued",
          correlation: {
            projection_type: "capability_call_response",
            catalog_id: "calculator",
            call_id: "call-after-restart",
          },
        },
      }),
    });
    socket.emit({ type: "run_event", event: event(2, { type: "step_committed" }) });
    socket.emit({ type: "run_event", event: event(3, { type: "status_changed" }) });

    await vi.waitFor(() => expect(completion).toHaveBeenCalledOnce());
    expect(fetchRun).toHaveBeenCalledTimes(2);
    expect((completion.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      type: "capability_call_response",
      run_id: "run-one",
      catalog_id: "calculator",
      call_id: "call-after-restart",
      result: { sum: 15 },
    });
    unsubscribe();
    window.removeEventListener(eventName, completion);
  });

  it("retries a correlated terminal lookup even when no later event arrives", async () => {
    const fetchRun = vi.fn()
      .mockRejectedValueOnce(new Error("transient lookup failure"))
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          id: "run-one",
          status: "failed",
          summary: "Remote call failed",
          correlation: {
            projection_type: "capability_call_response",
            catalog_id: "calculator",
            call_id: "call-retry",
          },
        }),
      } as Response);
    vi.stubGlobal("fetch", fetchRun);
    const service = new RunService();
    const completion = vi.fn();
    const eventName = "capability_call_response:calculator:call-retry";
    window.addEventListener(eventName, completion);
    const unsubscribe = service.subscribe(() => {});
    const socket = MockWebSocket.instances[0];
    socket.emit({ type: "run_stream_ready", stream_epoch: "epoch-one", latest_sequence: 1 });
    socket.emit({
      type: "run_event",
      event: event(1, {
        type: "run_created",
        payload: {
          status: "queued",
          correlation: {
            projection_type: "capability_call_response",
            catalog_id: "calculator",
            call_id: "call-retry",
          },
        },
      }),
    });

    await vi.advanceTimersByTimeAsync(250);
    await vi.waitFor(() => expect(completion).toHaveBeenCalledOnce());
    expect((completion.mock.calls[0][0] as CustomEvent).detail.error).toBe("Remote call failed");
    unsubscribe();
    window.removeEventListener(eventName, completion);
  });
});
