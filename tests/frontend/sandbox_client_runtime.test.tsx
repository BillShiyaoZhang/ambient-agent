import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import type { Widget } from "../../frontend/src/components/DashboardCanvas";
import { WidgetStorageBroker } from "../../frontend/src/services/widgetStorage";
import wsService from "../../frontend/src/services/websocket";


class ClientRuntimeSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: ClientRuntimeSocket[] = [];

  readonly url: string;
  readonly protocols: string[];
  readyState = ClientRuntimeSocket.CONNECTING;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event?: CloseEvent) => void) | null = null;

  constructor(url: string, protocols: string[]) {
    this.url = url;
    this.protocols = protocols;
    ClientRuntimeSocket.instances.push(this);
  }

  open() {
    this.readyState = ClientRuntimeSocket.OPEN;
    this.onopen?.();
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  send(payload: string) {
    this.sent.push(payload);
  }

  closeWith(code: number) {
    this.readyState = ClientRuntimeSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  close() {
    this.closeWith(1000);
  }
}


class MockPort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  sent: unknown[] = [];
  started = false;
  closed = false;
  failMessageType: string | null = null;

  postMessage(payload: unknown) {
    if (
      this.failMessageType
      && typeof payload === "object"
      && payload !== null
      && (payload as { type?: string }).type === this.failMessageType
    ) {
      throw new Error(`Mock port rejected ${this.failMessageType}`);
    }
    this.sent.push(payload);
  }

  start() {
    this.started = true;
  }

  close() {
    this.closed = true;
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: payload } as MessageEvent);
  }
}


class MockMessageChannel {
  static instances: MockMessageChannel[] = [];
  port1 = new MockPort();
  port2 = new MockPort();

  constructor() {
    MockMessageChannel.instances.push(this);
  }
}


const widget: Widget = {
  id: "notes/app",
  title: "Notes",
  js: "globalThis.__controllerExecutedInHost = true",
  manifest_revision: "2:1.0.0",
  grants_digest: "sha256:notes",
  capabilities: [{ id: "graph.query", scope: { entities: ["Note"] } }],
};


const ticketResponse = {
  ticket: "signed-ticket",
  expires_at: "2026-07-26T13:00:00Z",
  frame_url: "http://localhost:8001/widget-frame.html",
  protocol: "ambient-widget-client-v1",
};


const lastPortMessage = (port: MockPort, type: string) =>
  [...port.sent].reverse().find(
    (message) => typeof message === "object"
      && message !== null
      && (message as { type?: string }).type === type,
  ) as Record<string, any> | undefined;


describe("SandboxWidget isolated client runtime", () => {
  beforeEach(() => {
    ClientRuntimeSocket.instances = [];
    MockMessageChannel.instances = [];
    vi.useFakeTimers();
    vi.stubEnv("VITE_WIDGET_UI_TRANSPORT", "");
    vi.stubEnv("VITE_API_BASE_URL", "");
    vi.stubGlobal("WebSocket", ClientRuntimeSocket);
    vi.stubGlobal("MessageChannel", MockMessageChannel);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ticketResponse,
    }));
    vi.stubGlobal("crypto", {
      randomUUID: () => "host-nonce",
    });
    vi.spyOn(wsService, "sendMessage").mockImplementation(() => true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    delete (globalThis as any).__controllerExecutedInHost;
  });

  const startSession = async () => {
    render(<SandboxWidget widget={widget} />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    const frame = screen.getByTitle("Notes");
    vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    const socket = ClientRuntimeSocket.instances[0];
    act(() => {
      socket.open();
      fireEvent.load(frame);
    });
    const channel = MockMessageChannel.instances[0];
    return { frame, socket, channel };
  };

  const startReadySession = async (
    props: Omit<React.ComponentProps<typeof SandboxWidget>, "widget"> = {},
  ) => {
    const rendered = render(<SandboxWidget widget={widget} {...props} />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    const frame = screen.getByTitle("Notes");
    vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    const socket = ClientRuntimeSocket.instances[0];
    act(() => {
      socket.open();
      fireEvent.load(frame);
    });
    const channel = MockMessageChannel.instances[0];
    act(() => {
      socket.emit({
        type: "bootstrap",
        protocol_version: 1,
        controller_source: widget.js,
        capability_ids: ["graph.query"],
      });
      channel.port1.emit({ type: "port_ready", nonce: "host-nonce" });
      channel.port1.emit({ type: "ready" });
    });
    return { ...rendered, frame, socket, channel };
  };

  it("uses a one-time ticket, strict iframe attributes, and ticket WebSocket protocols", async () => {
    const { frame, socket, channel } = await startSession();

    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/apps/notes%2Fapp/client-runtime-ticket",
      expect.objectContaining({ method: "POST" }),
    );
    expect(vi.mocked(global.fetch).mock.calls[0][1]).not.toHaveProperty("credentials");
    expect(frame.getAttribute("src")).toBe(ticketResponse.frame_url);
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame.getAttribute("allow")).toBe("");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(socket.url).toBe("ws://localhost:8000/ws/widgets/notes%2Fapp/client-runtime");
    expect(socket.protocols).toEqual([
      "ambient-widget-client-v1",
      "ticket.signed-ticket",
    ]);
    expect(channel.port1.started).toBe(true);
    expect(globalThis.__controllerExecutedInHost).toBeUndefined();

    const transferred = vi.mocked(frame.contentWindow!.postMessage).mock.calls[0];
    expect(transferred[0]).toEqual({
      type: "ambient-widget-port",
      protocol_version: 1,
      nonce: "host-nonce",
    });
    expect(transferred[1]).toBe("*");
    expect(transferred[2]).toEqual([channel.port2]);
  });

  it("surfaces a host message that could not reach the Ambient command socket", async () => {
    vi.mocked(wsService.sendMessage).mockReturnValueOnce(false);
    const { channel } = await startReadySession();

    act(() => {
      channel.port1.emit({ type: "host_event", event: "send_message", text: "open notes" });
    });

    expect(screen.getByRole("alert").textContent).toContain("Message not sent");
  });

  it("gets a fresh one-time ticket after a transient runtime disconnect", async () => {
    const { socket } = await startReadySession();
    expect(global.fetch).toHaveBeenCalledTimes(1);

    act(() => socket.closeWith(1006));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(global.fetch).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(ClientRuntimeSocket.instances).toHaveLength(2);
  });

  it("offers a retry when the initial one-time ticket request fails", async () => {
    vi.mocked(global.fetch)
      .mockRejectedValueOnce(new Error("temporary ticket outage"))
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ticketResponse,
      } as Response);
    render(<SandboxWidget widget={widget} />);

    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(screen.getByText("temporary ticket outage")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Retry Widget Runtime" }));
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(ClientRuntimeSocket.instances).toHaveLength(1);
  });

  it("uses the configured HTTPS API base for both ticket and WebSocket traffic", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://ambient.example/runtime/");

    render(<SandboxWidget widget={widget} />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    expect(global.fetch).toHaveBeenCalledWith(
      "https://ambient.example/runtime/api/apps/notes%2Fapp/client-runtime-ticket",
      expect.objectContaining({ method: "POST" }),
    );
    expect(ClientRuntimeSocket.instances[0].url).toBe(
      "wss://ambient.example/runtime/ws/widgets/notes%2Fapp/client-runtime",
    );
  });

  it("waits for both bootstrap and nonce-bound port readiness before initializing", async () => {
    const { socket, channel } = await startSession();
    const bootstrap = {
      type: "bootstrap",
      protocol_version: 1,
      controller_source: widget.js,
      capability_ids: ["graph.query"],
    };

    act(() => socket.emit(bootstrap));
    expect(lastPortMessage(channel.port1, "init")).toBeUndefined();

    act(() => channel.port1.emit({ type: "port_ready", nonce: "wrong" }));
    expect(lastPortMessage(channel.port1, "init")).toBeUndefined();

    act(() => channel.port1.emit({ type: "port_ready", nonce: "host-nonce" }));
    expect(lastPortMessage(channel.port1, "init")).toEqual({
      type: "init",
      protocol_version: 1,
      nonce: "host-nonce",
      controller_source: widget.js,
      capability_ids: ["graph.query"],
      presentation_context: {
        theme: { preference: "system", effective: "dark" },
        locale: "en-US",
        reduced_motion: false,
      },
    });
    expect(globalThis.__controllerExecutedInHost).toBeUndefined();
  });

  it("promotes the host window when the fixed frame reports trusted focus", async () => {
    const onActivate = vi.fn();
    render(<SandboxWidget widget={widget} onActivate={onActivate} />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    const frame = screen.getByTitle("Notes");
    vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    const socket = ClientRuntimeSocket.instances[0];
    act(() => {
      socket.open();
      fireEvent.load(frame);
    });
    const channel = MockMessageChannel.instances[0];
    act(() => {
      socket.emit({
        type: "bootstrap",
        protocol_version: 1,
        controller_source: widget.js,
        capability_ids: ["graph.query"],
      });
      channel.port1.emit({ type: "port_ready", nonce: "host-nonce" });
      channel.port1.emit({ type: "host_event", event: "focus" });
    });

    expect(onActivate).toHaveBeenCalledTimes(1);
  });

  it("relays bounded RPC and server events only through the transferred port", async () => {
    const { socket, channel } = await startSession();
    act(() => {
      socket.emit({
        type: "bootstrap",
        protocol_version: 1,
        controller_source: widget.js,
        capability_ids: ["graph.query"],
      });
      channel.port1.emit({ type: "port_ready", nonce: "host-nonce" });
      channel.port1.emit({
        type: "rpc_request",
        request_id: "rpc-1",
        method: "graph.query",
        params: {
          query: { type: "Note" },
          app_id: "other-widget",
          session_id: "forged",
        },
      });
    });

    expect(socket.sent.map((message) => JSON.parse(message))).toContainEqual({
      type: "rpc_request",
      request_id: "rpc-1",
      method: "graph.query",
      params: { query: { type: "Note" } },
    });

    act(() => {
      socket.emit({
        type: "rpc_response",
        request_id: "rpc-1",
        result: [{ id: "note-1" }],
      });
      socket.emit({
        type: "subscription_event",
        subscription_id: "subscription-1",
        data: [{ id: "note-2" }],
      });
    });

    expect(lastPortMessage(channel.port1, "rpc_response")).toEqual({
      type: "rpc_response",
      request_id: "rpc-1",
      result: [{ id: "note-1" }],
    });
    expect(lastPortMessage(channel.port1, "subscription_event")).toEqual({
      type: "subscription_event",
      subscription_id: "subscription-1",
      data: [{ id: "note-2" }],
    });
  });

  it("binds storage to the host widget identity and allows only bounded host events", async () => {
    const storage = vi.spyOn(WidgetStorageBroker.prototype, "handle")
      .mockImplementation(async (message) => ({
        type: "storage_response",
        request_id: String(message.request_id),
        result: message.operation === "get" ? "saved" : { status: "ok" },
      }));
    const onFullscreen = vi.fn();
    const onMinimize = vi.fn();
    const { rerender } = render(
      <SandboxWidget
        widget={widget}
        onFullscreen={onFullscreen}
        onMinimize={onMinimize}
      />,
    );
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    const frame = screen.getByTitle("Notes");
    vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    const socket = ClientRuntimeSocket.instances[0];
    act(() => {
      socket.open();
      fireEvent.load(frame);
    });
    const port = MockMessageChannel.instances[0].port1;
    act(() => {
      socket.emit({
        type: "bootstrap",
        protocol_version: 1,
        controller_source: widget.js,
        capability_ids: ["graph.query"],
      });
      port.emit({ type: "port_ready", nonce: "host-nonce" });
    });

    await act(async () => {
      port.emit({
        type: "storage_request",
        request_id: "storage-1",
        operation: "get",
        key: "draft",
        widget_id: "other-widget",
      });
      port.emit({ type: "host_event", event: "fullscreen" });
      port.emit({ type: "host_event", event: "minimize" });
      port.emit({ type: "host_event", event: "send_message", text: "open notes" });
      port.emit({ type: "host_event", event: "capability.invoke" });
      await Promise.resolve();
    });

    expect(storage).toHaveBeenCalledWith(expect.objectContaining({
      type: "storage_request",
      request_id: "storage-1",
      operation: "get",
      key: "draft",
    }));
    expect((storage.mock.instances[0] as WidgetStorageBroker).widgetId).toBe("notes/app");
    expect(lastPortMessage(port, "storage_response")).toEqual({
      type: "storage_response",
      request_id: "storage-1",
      result: "saved",
    });
    expect(onFullscreen).toHaveBeenCalledWith("notes/app");
    expect(onMinimize).toHaveBeenCalledWith("notes/app");
    expect(wsService.sendMessage).toHaveBeenCalledTimes(1);
    expect(wsService.sendMessage).toHaveBeenCalledWith({
      sender: "user",
      content: "open notes",
    });

    rerender(
      <SandboxWidget
        widget={widget}
        presentationContext={{
          theme: { preference: "light", effective: "light" },
          locale: "zh-CN",
          reduced_motion: true,
        }}
      />,
    );
    expect(lastPortMessage(port, "presentation_context")).toEqual({
      type: "presentation_context",
      presentation_context: {
        theme: { preference: "light", effective: "light" },
        locale: "zh-CN",
        reduced_motion: true,
      },
    });
  });

  it("waits for the matching frame acknowledgement and uses the latest completion callback", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn()
        .mockReturnValueOnce("host-nonce")
        .mockReturnValueOnce("suspend-request-1"),
    });
    const firstCompletion = vi.fn();
    const latestCompletion = vi.fn();
    const { channel, rerender } = await startReadySession({
      onSuspendReady: firstCompletion,
    });
    const port = channel.port1;

    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={firstCompletion}
      />,
    );
    const request = lastPortMessage(port, "before_suspend");
    expect(request).toEqual({
      type: "before_suspend",
      request_id: "suspend-request-1",
    });
    expect(request?.request_id.length).toBeLessThanOrEqual(200);

    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={latestCompletion}
      />,
    );
    expect(ClientRuntimeSocket.instances).toHaveLength(1);
    expect(port.sent.filter(
      (message) => (message as { type?: string }).type === "before_suspend",
    )).toHaveLength(1);

    act(() => {
      port.emit({ type: "suspend_ready", request_id: "wrong-request" });
    });
    expect(firstCompletion).not.toHaveBeenCalled();
    expect(latestCompletion).not.toHaveBeenCalled();

    act(() => {
      port.emit({
        type: "suspend_ready",
        request_id: request?.request_id,
      });
      port.emit({
        type: "suspend_ready",
        request_id: request?.request_id,
      });
    });
    expect(firstCompletion).not.toHaveBeenCalled();
    expect(latestCompletion).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(latestCompletion).toHaveBeenCalledTimes(1);
  });

  it("bounds suspend flushing at one second and ignores a cancelled request", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn()
        .mockReturnValueOnce("host-nonce")
        .mockReturnValueOnce("suspend-request-1")
        .mockReturnValueOnce("suspend-request-2")
        .mockReturnValueOnce("suspend-request-3"),
    });
    const onSuspendReady = vi.fn();
    const { channel, rerender } = await startReadySession({ onSuspendReady });
    const port = channel.port1;

    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={onSuspendReady}
      />,
    );
    const cancelledRequest = lastPortMessage(port, "before_suspend");
    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested={false}
        onSuspendReady={onSuspendReady}
      />,
    );
    act(() => {
      port.emit({
        type: "suspend_ready",
        request_id: cancelledRequest?.request_id,
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(onSuspendReady).not.toHaveBeenCalled();

    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={onSuspendReady}
      />,
    );
    const timedRequest = lastPortMessage(port, "before_suspend");
    expect(timedRequest?.request_id).not.toBe(cancelledRequest?.request_id);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(999);
    });
    expect(onSuspendReady).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(onSuspendReady).toHaveBeenCalledTimes(1);

    act(() => {
      port.emit({
        type: "suspend_ready",
        request_id: timedRequest?.request_id,
      });
    });
    expect(onSuspendReady).toHaveBeenCalledTimes(1);
  });

  it("completes immediately without a ready port or when port delivery fails", async () => {
    const notReadyCompletion = vi.fn();
    const notReady = render(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={notReadyCompletion}
      />,
    );
    expect(notReadyCompletion).toHaveBeenCalledTimes(1);
    notReady.unmount();

    const failedDeliveryCompletion = vi.fn();
    const {
      channel,
      rerender,
    } = await startReadySession({ onSuspendReady: failedDeliveryCompletion });
    channel.port1.failMessageType = "before_suspend";
    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={failedDeliveryCompletion}
      />,
    );
    expect(failedDeliveryCompletion).toHaveBeenCalledTimes(1);
  });

  it("clears a pending suspend timeout during session cleanup", async () => {
    const onSuspendReady = vi.fn();
    const { rerender, unmount } = await startReadySession({ onSuspendReady });
    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={onSuspendReady}
      />,
    );

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(onSuspendReady).not.toHaveBeenCalled();
  });

  it("does not let a replaced session acknowledge its successor", async () => {
    const onSuspendReady = vi.fn();
    const { channel, rerender } = await startReadySession({ onSuspendReady });
    rerender(
      <SandboxWidget
        widget={widget}
        suspendRequested
        onSuspendReady={onSuspendReady}
      />,
    );
    const oldPort = channel.port1;
    const oldRequest = lastPortMessage(oldPort, "before_suspend");

    rerender(
      <SandboxWidget
        widget={{ ...widget, id: "tasks/app", title: "Tasks" }}
        suspendRequested
        onSuspendReady={onSuspendReady}
      />,
    );
    expect(onSuspendReady).toHaveBeenCalledTimes(1);
    expect(oldPort.closed).toBe(true);

    act(() => {
      oldPort.emit({
        type: "suspend_ready",
        request_id: oldRequest?.request_id,
      });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(onSuspendReady).toHaveBeenCalledTimes(1);
  });

  it("surfaces session errors and closes the socket and port on unmount", async () => {
    const { unmount } = render(<SandboxWidget widget={widget} />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    const frame = screen.getByTitle("Notes");
    vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    const socket = ClientRuntimeSocket.instances[0];
    act(() => {
      socket.open();
      fireEvent.load(frame);
      socket.emit({
        type: "runtime_error",
        error: {
          code: "controller_load_failed",
          message: "Controller failed",
          classification: "code_only",
        },
      });
    });

    expect(screen.getByText("Controller failed")).toBeDefined();
    const port = MockMessageChannel.instances[0].port1;
    unmount();
    expect(socket.readyState).toBe(ClientRuntimeSocket.CLOSED);
    expect(port.closed).toBe(true);
  });

  it("closes a session that never completes the frame handshake", async () => {
    const { socket, channel } = await startSession();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });

    expect(screen.getByText("Widget Runtime handshake timed out")).toBeDefined();
    expect(socket.readyState).toBe(ClientRuntimeSocket.CLOSED);
    expect(lastPortMessage(channel.port1, "session_invalidated")).toEqual({
      type: "session_invalidated",
      reason: "Widget Runtime handshake timed out",
    });
  });

  it("does not transfer a new privileged port after unexpected frame navigation", async () => {
    const { frame, socket } = await startSession();
    expect(MockMessageChannel.instances).toHaveLength(1);

    act(() => {
      fireEvent.load(frame);
    });

    expect(MockMessageChannel.instances).toHaveLength(1);
    expect(socket.readyState).toBe(ClientRuntimeSocket.CLOSED);
    expect(
      screen.getByText("Widget frame navigated or reloaded unexpectedly"),
    ).toBeDefined();
  });

  it("reports an invalid ticket without creating a frame or socket", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ ...ticketResponse, protocol: "unknown" }),
    } as Response);

    render(<SandboxWidget widget={widget} />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    expect(screen.getByText("Invalid Widget Runtime ticket")).toBeDefined();
    expect(screen.queryByTitle("Notes")).toBeNull();
    expect(ClientRuntimeSocket.instances).toHaveLength(0);
  });

  it("keeps the pixel transport as an explicit environment fallback", () => {
    vi.stubEnv("VITE_WIDGET_UI_TRANSPORT", "pixels");

    render(<SandboxWidget widget={widget} />);
    act(() => vi.runOnlyPendingTimers());

    expect(global.fetch).not.toHaveBeenCalled();
    expect(ClientRuntimeSocket.instances).toHaveLength(1);
    expect(ClientRuntimeSocket.instances[0].url).toContain(
      "/ws/widgets/notes%2Fapp/runtime?",
    );
    expect(screen.queryByTitle("Notes")).toBeNull();
  });
});
