import React from "react";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PixelSandboxWidget as SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import type { Widget } from "../../frontend/src/components/DashboardCanvas";
import wsService from "../../frontend/src/services/websocket";
import { runService } from "../../frontend/src/services/runs";


class RuntimeSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: RuntimeSocket[] = [];
  readyState = RuntimeSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  sent: string[] = [];

  constructor(readonly url: string) {
    RuntimeSocket.instances.push(this);
  }

  send(message: string) {
    this.sent.push(message);
  }

  closeWith(code: number) {
    this.readyState = RuntimeSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  close() {
    this.closeWith(1000);
  }

  open() {
    this.readyState = RuntimeSocket.OPEN;
    this.onopen?.();
  }

  emit(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
}


const privilegedWidget: Widget = {
  id: "privileged-widget",
  title: "Privileged Widget",
  js: `
    fetch("/api/apps/other-app/files/read");
    ambient.graph.subscribe({ type: "Task" }, () => {});
    ambient.capabilities.invoke("mcp:calendar:calendar", {}, "list-events");
  `,
  manifest_revision: "2:1.0.0",
  grants_digest: "sha256:privileged",
  capabilities: [
    { id: "graph.query", scope: { entities: ["Task"] } },
    { id: "graph.mutate", scope: { entities: ["Task"], operations: ["create"] } },
    { id: "network.request", scope: { sources: {} } },
    { id: "file.read", scope: { paths: ["notes/**"] } },
    { id: "file.write", scope: { paths: ["notes/**"], max_bytes: 4096 } },
    {
      id: "capability.invoke",
      scope: { catalog_ids: ["mcp:calendar:calendar"], actions: ["list-events"] },
    },
  ],
};


describe("SandboxWidget SDK boundary", () => {
  beforeEach(() => {
    RuntimeSocket.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", RuntimeSocket);
    vi.stubGlobal("fetch", vi.fn());
    vi.spyOn(wsService, "registerPersistentMessage");
    vi.spyOn(wsService, "sendMessage").mockImplementation(() => true);
    vi.spyOn(runService, "start");
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("does not construct Graph, Network, Files, or Run SDKs in the host realm", () => {
    render(<SandboxWidget widget={privilegedWidget} />);
    act(() => vi.runOnlyPendingTimers());
    const socket = RuntimeSocket.instances[0];
    act(() => socket.open());

    expect(global.fetch).not.toHaveBeenCalled();
    expect(wsService.registerPersistentMessage).not.toHaveBeenCalled();
    expect(runService.start).not.toHaveBeenCalled();
    expect(socket.sent.join("\n")).not.toContain("graph.query");
    expect(socket.sent.join("\n")).not.toContain("mcp:calendar:calendar");
    expect(socket.sent.join("\n")).not.toContain("other-app");
  });

  it("accepts only bounded host events from the server-owned runtime session", () => {
    const onFullscreen = vi.fn();
    const onMinimize = vi.fn();
    render(
      <SandboxWidget
        widget={privilegedWidget}
        onFullscreen={onFullscreen}
        onMinimize={onMinimize}
      />,
    );
    act(() => vi.runOnlyPendingTimers());
    const socket = RuntimeSocket.instances[0];

    act(() => {
      socket.emit({ type: "host_event", event: "fullscreen" });
      socket.emit({ type: "host_event", event: "minimize" });
      socket.emit({ type: "host_event", event: "send_message", text: "open tasks" });
      socket.emit({
        type: "host_event",
        event: "capability.invoke",
        catalog_id: "mcp:calendar:calendar",
      });
    });

    expect(onFullscreen).toHaveBeenCalledWith("privileged-widget");
    expect(onMinimize).toHaveBeenCalledWith("privileged-widget");
    expect(wsService.sendMessage).toHaveBeenCalledWith({
      sender: "user",
      content: "open tasks",
    });
    expect(runService.start).not.toHaveBeenCalled();
  });

  it("bounds legacy pixel-runtime reconnects and offers an explicit retry", () => {
    render(<SandboxWidget widget={privilegedWidget} />);
    act(() => vi.runOnlyPendingTimers());

    for (const delay of [500, 1_000, 2_000, 4_000, 8_000]) {
      act(() => {
        RuntimeSocket.instances.at(-1)!.closeWith(1006);
        vi.advanceTimersByTime(delay);
      });
    }
    act(() => RuntimeSocket.instances.at(-1)!.closeWith(1006));
    expect(RuntimeSocket.instances).toHaveLength(6);

    const retry = document.querySelector("button");
    expect(retry?.textContent).toContain("Retry Widget Runtime");
    act(() => retry?.click());
    act(() => vi.runOnlyPendingTimers());
    expect(RuntimeSocket.instances).toHaveLength(7);
  });

  it("never sends manifest revision, grant digest, capability scopes, or source", () => {
    render(<SandboxWidget widget={privilegedWidget} />);
    act(() => vi.runOnlyPendingTimers());
    const socket = RuntimeSocket.instances[0];
    act(() => socket.open());
    const outbound = socket.sent.join("\n");

    expect(outbound).not.toContain(privilegedWidget.js);
    expect(outbound).not.toContain(privilegedWidget.manifest_revision);
    expect(outbound).not.toContain(privilegedWidget.grants_digest);
    expect(outbound).not.toContain("file.write");
  });
});
