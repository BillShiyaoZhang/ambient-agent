import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock WebSocket class
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState: number = 0; // CONNECTING
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((event: any) => void) | null = null;
  send: (data: string) => void = vi.fn();
  close: () => void = vi.fn();

  constructor(url: string) {
    this.readyState = MockWebSocket.CONNECTING;
    this.url = url;
    MockWebSocket.instances.push(this);
    setTimeout(() => {
      if (this.readyState !== MockWebSocket.CONNECTING) return;
      this.readyState = MockWebSocket.OPEN;
      if (this.onopen) this.onopen();
    }, 10);
  }
}

// Attach MockWebSocket to global
vi.stubGlobal("WebSocket", MockWebSocket);

describe("WebSocket Client Service", () => {
  let wsService: any;

  beforeEach(async () => {
    MockWebSocket.instances = [];
    // Import service dynamically so it grabs the mocked WebSocket
    const module = await import("../../frontend/src/services/websocket");
    wsService = module.default;
  });

  afterEach(() => {
    wsService.disconnect();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("should connect and handle messages", async () => {
    const messageHandler = vi.fn();
    wsService.connect("ws://localhost:8000/ws/chat", messageHandler);

    // Wait for connection to open
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(wsService.isConnected()).toBe(true);

    // Mock receiving a message
    const mockMessage = { type: "ack", message: { id: 1, sender: "user", content: "hello" } };
    wsService.socket.onmessage({ data: JSON.stringify(mockMessage) });

    expect(messageHandler).toHaveBeenCalledWith(mockMessage);
  });

  it("should send message through socket", async () => {
    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(wsService.sendMessage({ sender: "user", content: "test send" })).toBe(true);
    expect(wsService.socket.send).toHaveBeenCalledWith(
      JSON.stringify({ sender: "user", content: "test send" })
    );
  });

  it("reports messages that were not queued instead of silently dropping them", async () => {
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(wsService.sendMessage({ sender: "user", content: "offline" })).toBe(false);

    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    await new Promise((resolve) => setTimeout(resolve, 20));
    wsService.socket.send.mockImplementationOnce(() => {
      throw new Error("socket closed during send");
    });

    expect(wsService.sendMessage({ sender: "user", content: "raced" })).toBe(false);
    expect(errorLog).toHaveBeenCalledTimes(2);
  });

  it("sends only persistent registrations that are still active when the socket opens", async () => {
    const subscription = { type: "graph_subscribe", subscription_id: "sub-1", query: { type: "Task" } };
    wsService.registerPersistentMessage("graph:sub-1", subscription);
    wsService.registerPersistentMessage("graph:stale", { ...subscription, subscription_id: "stale" });

    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    wsService.unregisterPersistentMessage("graph:stale", { type: "graph_unsubscribe", subscription_id: "stale" });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(wsService.socket.send).toHaveBeenCalledTimes(1);
    expect(wsService.socket.send).toHaveBeenCalledWith(JSON.stringify(subscription));
    wsService.unregisterPersistentMessage("graph:sub-1");
  });

  it("replays active persistent registrations after reconnecting", async () => {
    const subscription = { type: "graph_subscribe", subscription_id: "sub-reconnect", query: { type: "Task" } };
    wsService.registerPersistentMessage("graph:sub-reconnect", subscription);
    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    await new Promise((resolve) => setTimeout(resolve, 20));

    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(wsService.socket.send).toHaveBeenCalledTimes(1);
    expect(wsService.socket.send).toHaveBeenCalledWith(JSON.stringify(subscription));
    wsService.unregisterPersistentMessage("graph:sub-reconnect");
  });

  it("does not reconnect after a normal close", () => {
    vi.useFakeTimers();
    const states: string[] = [];
    const unsubscribe = wsService.subscribeStatus((state: string) => states.push(state));
    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    vi.advanceTimersByTime(10);
    const first = MockWebSocket.instances[0];

    first.readyState = MockWebSocket.CLOSED;
    first.onclose?.({ code: 1000 } as CloseEvent);
    vi.advanceTimersByTime(60_000);

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(states.at(-1)).toBe("disconnected");
    unsubscribe();
  });

  it("cancels an abnormal-close retry when explicitly disconnected", () => {
    vi.useFakeTimers();
    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    vi.advanceTimersByTime(10);
    const first = MockWebSocket.instances[0];
    first.readyState = MockWebSocket.CLOSED;
    first.onclose?.({ code: 1006 } as CloseEvent);

    wsService.disconnect();
    vi.advanceTimersByTime(60_000);

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(wsService.getConnectionState()).toBe("disconnected");
  });

  it("uses bounded exponential backoff and supports an explicit retry", () => {
    vi.useFakeTimers();
    const states: string[] = [];
    const unsubscribe = wsService.subscribeStatus((state: string) => states.push(state));
    wsService.connect("ws://localhost:8000/ws/chat", () => {});
    vi.advanceTimersByTime(10);

    const delays = [500, 1_000, 2_000, 4_000, 8_000];
    for (const [index, delay] of delays.entries()) {
      const socket = MockWebSocket.instances[index];
      socket.readyState = MockWebSocket.CLOSED;
      socket.onclose?.({ code: 1006 } as CloseEvent);
      vi.advanceTimersByTime(delay - 1);
      expect(MockWebSocket.instances).toHaveLength(index + 1);
      vi.advanceTimersByTime(1);
      expect(MockWebSocket.instances).toHaveLength(index + 2);
    }

    const exhausted = MockWebSocket.instances.at(-1)!;
    exhausted.readyState = MockWebSocket.CLOSED;
    exhausted.onclose?.({ code: 1006 } as CloseEvent);
    vi.advanceTimersByTime(60_000);
    expect(MockWebSocket.instances).toHaveLength(6);
    expect(states.at(-1)).toBe("unavailable");

    expect(wsService.retry()).toBe(true);
    expect(MockWebSocket.instances).toHaveLength(7);
    expect(states.at(-1)).toBe("connecting");
    unsubscribe();
  });
});
