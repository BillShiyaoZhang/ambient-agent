import React from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "../../frontend/src/components/ErrorBoundary";
import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import type { Widget } from "../../frontend/src/components/DashboardCanvas";


class RuntimeSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: RuntimeSocket[] = [];
  readyState = RuntimeSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];

  constructor(readonly url: string) {
    RuntimeSocket.instances.push(this);
  }

  send(message: string) {
    this.sent.push(message);
  }

  close() {
    this.readyState = RuntimeSocket.CLOSED;
    this.onclose?.();
  }

  open() {
    this.readyState = RuntimeSocket.OPEN;
    this.onopen?.();
  }

  emit(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
}


const widget = (overrides: Partial<Widget> = {}): Widget => ({
  id: "runtime-widget",
  title: "Runtime Widget",
  js: "globalThis.__hostControllerExecuted = true",
  manifest_revision: "2:1.0.0",
  grants_digest: "sha256:one",
  capabilities: [],
  ...overrides,
});


describe("SandboxWidget rendering and containment", () => {
  beforeEach(() => {
    RuntimeSocket.instances = [];
    vi.stubGlobal("WebSocket", RuntimeSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete (globalThis as any).__hostControllerExecuted;
  });

  it("catches host rendering errors around the frame player", () => {
    const CrashingComponent = () => {
      throw new Error("Test Crash");
    };
    const spyConsole = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <CrashingComponent />
      </ErrorBoundary>,
    );

    expect(screen.getByText("Widget Crashed")).toBeDefined();
    expect(screen.getByText("Test Crash")).toBeDefined();
    spyConsole.mockRestore();
  });

  it("never evaluates Controller source in the host page", () => {
    render(<SandboxWidget widget={widget()} />);

    expect((globalThis as any).__hostControllerExecuted).toBeUndefined();
    expect(RuntimeSocket.instances).toHaveLength(1);
    expect(RuntimeSocket.instances[0].sent.join("\n")).not.toContain("globalThis");
  });

  it("replaces a failed runtime session when the published revision changes", () => {
    const original = widget();
    const { rerender } = render(<SandboxWidget widget={original} />);
    const first = RuntimeSocket.instances[0];
    act(() => {
      first.open();
      first.emit({
        type: "runtime_error",
        error: { code: "controller_load_failed", message: "broken" },
      });
    });
    expect(screen.getByText("broken")).toBeDefined();

    rerender(
      <SandboxWidget
        widget={widget({
          manifest_revision: "2:1.0.1",
          grants_digest: "sha256:two",
          js: "export default function Fixed() {}",
        })}
      />,
    );

    expect(first.readyState).toBe(RuntimeSocket.CLOSED);
    expect(RuntimeSocket.instances).toHaveLength(2);
    act(() => {
      RuntimeSocket.instances[1].open();
      RuntimeSocket.instances[1].emit({
        type: "frame",
        format: "jpeg",
        data: "Zml4ZWQ=",
        width: 640,
        height: 480,
      });
    });
    expect(screen.queryByText("broken")).toBeNull();
    expect(screen.getByRole("img", { name: "Runtime Widget" })).toBeDefined();
  });
});
