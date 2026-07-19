import React from "react";
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
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    await waitFor(() => expect(screen.getByText("Local forecast")).toBeDefined());
  });
});
