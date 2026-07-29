import React, { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppCenter } from "../../frontend/src/components/AppCenter";
import wsService from "../../frontend/src/services/websocket";

vi.mock("../../frontend/src/services/websocket", () => ({
  default: { sendMessage: vi.fn() },
}));

const state = {
  version: 1,
  revision: 2,
  items: [
    {
      catalog_id: "app:weather",
      kind: "generated_app",
      title: "Weather",
      description: "Local forecast",
      version: "1.0.0",
      provider: "Ambient Agent",
      tags: ["forecast"],
      ui_app_id: "weather",
      status: "ready",
    },
    {
      catalog_id: "mcp:acme:calendar",
      kind: "mcp",
      title: "Calendar Tools",
      description: "Manage events",
      version: "2.0.0",
      provider: "Acme",
      tags: ["events"],
      ui_app_id: null,
      status: "needs_ui",
    },
  ],
  root: ["app:weather", "mcp:acme:calendar"],
  folders: [],
};

describe("App Center", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve(state) })
    );
  });

  it("searches and filters the unified catalog", async () => {
    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );

    await screen.findByText("Weather");
    fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "events" } });
    expect(screen.getByText("Calendar Tools")).toBeDefined();
    expect(screen.queryByText("Weather")).toBeNull();

    fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Skills" }));
    expect(screen.getByText("No results found")).toBeDefined();
  });

  it("launches ready apps directly", async () => {
    const run = vi.fn();
    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={run}
        language="en"
      />
    );
    fireEvent.click(await screen.findByRole("button", { name: "Open Weather" }));
    expect(run).toHaveBeenCalledWith("weather");
  });

  it("opens capability details and requests UI generation", async () => {
    const close = vi.fn();
    render(
      <AppCenter
        isOpen
        onClose={close}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );
    fireEvent.click(await screen.findByRole("button", { name: "Open Calendar Tools" }));
    fireEvent.click(screen.getByRole("button", { name: "Generate interface" }));
    expect(wsService.sendMessage).toHaveBeenCalledWith({
      type: "generate_capability_ui",
      catalog_id: "mcp:acme:calendar",
    });
    expect(close).toHaveBeenCalled();
  });

  it("opens details from the contextual management menu", async () => {
    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        language="en"
      />
    );
    await screen.findByText("Weather");
    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Weather" }), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "View details" }));
    await waitFor(() => expect(screen.getByText("Local forecast")).toBeDefined());
  });

  it("opens the management menu by holding the app icon without launching it", async () => {
    vi.useFakeTimers();
    const run = vi.fn();
    try {
      const { container } = render(
        <AppCenter
          isOpen
          onClose={vi.fn()}
          pinnedWidgetIds={[]}
          onPinWidget={vi.fn()}
          onUnpinWidget={vi.fn()}
          onRunFullscreen={run}
          language="en"
        />
      );
      await act(async () => {});
      const tile = screen.getByRole("button", { name: "Open Weather" });
      const icon = container.querySelector('[data-app-icon="app:weather"]');
      expect(icon).not.toBeNull();

      fireEvent.pointerDown(icon!, { pointerId: 1, pointerType: "touch", clientX: 32, clientY: 48 });
      act(() => vi.advanceTimersByTime(520));
      fireEvent.pointerUp(icon!, { pointerId: 1, pointerType: "touch", clientX: 32, clientY: 48 });
      fireEvent.click(tile);

      expect(screen.getByRole("menu", { name: "Manage Weather" })).toBeDefined();
      expect(screen.getByRole("menuitem", { name: "Configure properties" })).toBeDefined();
      expect(screen.getByRole("menuitem", { name: "Rename" })).toBeDefined();
      expect(screen.getByRole("menuitem", { name: "Uninstall app" })).toBeDefined();
      expect(run).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("cancels an icon hold when the pointer moves beyond the gesture tolerance", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <AppCenter
          isOpen
          onClose={vi.fn()}
          pinnedWidgetIds={[]}
          onPinWidget={vi.fn()}
          onUnpinWidget={vi.fn()}
          onRunFullscreen={vi.fn()}
          language="en"
        />
      );
      await act(async () => {});
      const icon = container.querySelector('[data-app-icon="app:weather"]');
      fireEvent.pointerDown(icon!, { pointerId: 1, pointerType: "touch", clientX: 32, clientY: 48 });
      fireEvent.pointerMove(icon!, { pointerId: 1, pointerType: "touch", clientX: 48, clientY: 48 });
      act(() => vi.advanceTimersByTime(520));
      expect(screen.queryByRole("menu", { name: "Manage Weather" })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renames and configures generated app properties through partial updates", async () => {
    let currentState = structuredClone(state);
    const appUpdated = vi.fn();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/apps/weather") && init?.method === "PATCH") {
        const update = JSON.parse(String(init.body));
        const item = currentState.items.find((candidate) => candidate.catalog_id === "app:weather")!;
        Object.assign(item, {
          title: update.title ?? item.title,
          description: update.description ?? item.description,
          version: update.app_version ?? item.version,
          tags: update.intents ?? item.tags,
        });
        return { ok: true, status: 200, json: async () => ({
          id: "weather",
          title: item.title,
          description: item.description,
          app_version: item.version,
          intents: item.tags,
        }) } as Response;
      }
      return { ok: true, status: 200, json: async () => currentState } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AppCenter
        isOpen
        onClose={vi.fn()}
        pinnedWidgetIds={[]}
        onPinWidget={vi.fn()}
        onUnpinWidget={vi.fn()}
        onRunFullscreen={vi.fn()}
        onAppUpdated={appUpdated}
        language="en"
      />
    );
    await screen.findByText("Weather");

    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Weather" }), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByLabelText("App name"), { target: { value: "Weather Desk" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));
    await screen.findByRole("button", { name: "Open Weather Desk" });

    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Weather Desk" }), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Configure properties" }));
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Forecast dashboard" } });
    fireEvent.change(screen.getByLabelText("Version"), { target: { value: "2.0.0" } });
    fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "weather, local" } });
    fireEvent.click(screen.getByRole("button", { name: "Save properties" }));

    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH");
      expect(patchCalls).toHaveLength(2);
      expect(JSON.parse(String(patchCalls[0][1]?.body))).toEqual({ title: "Weather Desk" });
      expect(JSON.parse(String(patchCalls[1][1]?.body))).toEqual({
        description: "Forecast dashboard",
        app_version: "2.0.0",
        intents: ["weather", "local"],
      });
      expect(appUpdated).toHaveBeenCalledTimes(2);
      expect(appUpdated).toHaveBeenLastCalledWith("weather");
    });
  });
});
