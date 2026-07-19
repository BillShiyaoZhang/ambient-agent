import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock WebSocket class
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

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
    setTimeout(() => {
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
    // Import service dynamically so it grabs the mocked WebSocket
    const module = await import("../../frontend/src/services/websocket");
    wsService = module.default;
  });

  afterEach(() => {
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

    wsService.sendMessage({ sender: "user", content: "test send" });
    expect(wsService.socket.send).toHaveBeenCalledWith(
      JSON.stringify({ sender: "user", content: "test send" })
    );
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
});
