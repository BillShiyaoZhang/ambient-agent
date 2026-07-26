import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PixelSandboxWidget as SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import type { Widget } from "../../frontend/src/components/DashboardCanvas";


class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  binaryType = "";
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
  }

  send(payload: string) {
    this.sent.push(payload);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}


class MockResizeObserver {
  static instances: MockResizeObserver[] = [];
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    MockResizeObserver.instances.push(this);
  }

  observe(target: Element) {
    this.callback(
      [{ target, contentRect: { width: 640, height: 480 } as DOMRectReadOnly } as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }

  disconnect() {}
  unobserve() {}
}


const widget: Widget = {
  id: "notes-app",
  title: "Notes",
  js: "throw new Error('must never execute in the host')",
  manifest_revision: "2:1.0.0",
  grants_digest: "sha256:notes",
  capabilities: [{ id: "file.read", scope: { paths: ["notes/**"] } }],
};


describe("SandboxWidget remote runtime player", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    MockResizeObserver.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", MockWebSocket);
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("opens an app-scoped runtime stream without sending Controller source or grants", () => {
    render(
      <SandboxWidget
        widget={widget}
        presentationContext={{
          theme: { preference: "system", effective: "light" },
          locale: "zh-CN",
          reduced_motion: true,
        }}
      />,
    );
    act(() => vi.runOnlyPendingTimers());

    expect(MockWebSocket.instances).toHaveLength(1);
    const socket = MockWebSocket.instances[0];
    const runtimeUrl = new URL(socket.url);
    expect(runtimeUrl.pathname).toBe("/ws/widgets/notes-app/runtime");
    expect(runtimeUrl.searchParams.get("width")).toBe("640");
    expect(runtimeUrl.searchParams.get("height")).toBe("480");
    expect(runtimeUrl.searchParams.get("device_scale_factor")).toBe("1");
    expect(runtimeUrl.searchParams.get("theme_preference")).toBe("system");
    expect(runtimeUrl.searchParams.get("theme_effective")).toBe("light");
    expect(runtimeUrl.searchParams.get("locale")).toBe("zh-CN");
    expect(runtimeUrl.searchParams.get("reduced_motion")).toBe("true");

    act(() => socket.open());

    const messages = socket.sent.map((message) => JSON.parse(message));
    expect(messages).toContainEqual({
      type: "viewport",
      width: 640,
      height: 480,
      device_scale_factor: 1,
    });
    expect(messages).toContainEqual({
      type: "presentation_context",
      theme: { preference: "system", effective: "light" },
      locale: "zh-CN",
      reduced_motion: true,
    });
    expect(socket.sent.join("\n")).not.toContain(widget.js);
    expect(socket.sent.join("\n")).not.toContain("file.read");
    expect(socket.sent.join("\n")).not.toContain("grants_digest");
  });

  it("updates presentation context in the same runtime session", () => {
    const { rerender } = render(
      <SandboxWidget
        widget={widget}
        presentationContext={{
          theme: { preference: "system", effective: "dark" },
          locale: "en-US",
          reduced_motion: false,
        }}
      />,
    );
    act(() => vi.runOnlyPendingTimers());
    const socket = MockWebSocket.instances[0];
    act(() => socket.open());
    socket.sent = [];

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

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(socket.sent.map((message) => JSON.parse(message))).toContainEqual({
      type: "presentation_context",
      theme: { preference: "light", effective: "light" },
      locale: "zh-CN",
      reduced_motion: true,
    });
  });

  it("renders only runtime frames and surfaces structured runtime errors", () => {
    render(<SandboxWidget widget={widget} />);
    act(() => vi.runOnlyPendingTimers());
    const socket = MockWebSocket.instances[0];

    act(() => {
      socket.open();
      socket.emit({
        type: "frame",
        format: "jpeg",
        data: "ZmFrZS1qcGVn",
        width: 640,
        height: 480,
      });
    });

    expect(screen.getByRole("img", { name: "Notes" }).getAttribute("src")).toBe(
      "data:image/jpeg;base64,ZmFrZS1qcGVn",
    );

    act(() => {
      socket.emit({
        type: "runtime_error",
        error: { code: "controller_render_failed", message: "Render failed" },
      });
    });
    expect(screen.getByText("Render failed")).toBeDefined();
  });

  it("normalizes pointer input without app identity fields", () => {
    render(<SandboxWidget widget={widget} />);
    act(() => vi.runOnlyPendingTimers());
    const socket = MockWebSocket.instances[0];
    act(() => socket.open());
    socket.sent = [];

    const player = screen.getByTestId("sandbox-notes-app");
    Object.defineProperty(player, "getBoundingClientRect", {
      value: () => ({ left: 10, top: 20, width: 640, height: 480 }),
    });

    fireEvent.pointerDown(player, { clientX: 42, clientY: 65, button: 0, buttons: 1 });

    expect(socket.sent.map((message) => JSON.parse(message))).toContainEqual({
      type: "pointer",
      event: "mousePressed",
      x: 32,
      y: 45,
      button: "left",
      buttons: 1,
      click_count: 1,
    });
    expect(socket.sent.join("\n")).not.toContain("notes-app");
    expect(socket.sent.join("\n")).not.toContain("manifest_revision");
  });

  it("keeps the same runtime connection when parent callback references change", () => {
    const { rerender } = render(
      <SandboxWidget
        widget={widget}
        onFullscreen={() => undefined}
        onMinimize={() => undefined}
      />,
    );
    act(() => vi.runOnlyPendingTimers());
    const socket = MockWebSocket.instances[0];

    rerender(
      <SandboxWidget
        widget={widget}
        onFullscreen={() => undefined}
        onMinimize={() => undefined}
      />,
    );

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(socket.readyState).toBe(MockWebSocket.CONNECTING);
  });

  it("does not create a throwaway socket during StrictMode effect replay", () => {
    render(
      <React.StrictMode>
        <SandboxWidget widget={widget} />
      </React.StrictMode>,
    );
    act(() => vi.runOnlyPendingTimers());

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it("reconnects after an unexpected runtime disconnect", () => {
    render(<SandboxWidget widget={widget} />);
    act(() => vi.runOnlyPendingTimers());
    const first = MockWebSocket.instances[0];
    act(() => {
      first.open();
      first.close();
    });

    act(() => vi.runOnlyPendingTimers());

    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1].url).toBe(first.url);
  });
});
