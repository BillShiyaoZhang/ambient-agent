import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppCenter } from "../../frontend/src/components/AppCenter";

vi.mock("../../frontend/src/services/websocket", () => ({
  default: { sendMessage: vi.fn() },
}));

const catalog = {
  spec_version: 1,
  types: [
    {
      id: "calendar",
      title: { zh: "日历", en: "Calendar" },
      description: { zh: "安排日程", en: "Schedule your time" },
      features: [
        { id: "calendar.events", title: { zh: "事件管理", en: "Event management" } },
        { id: "calendar.reminders", title: { zh: "提醒", en: "Reminders" } },
        { id: "calendar.views", title: { zh: "日历视图", en: "Calendar views" } },
      ],
    },
    {
      id: "tasks",
      title: { zh: "任务", en: "Tasks" },
      description: { zh: "跟进任务", en: "Track your work" },
      features: [{ id: "tasks.items", title: { zh: "任务管理", en: "Task management" } }],
    },
  ],
};

const spec = {
  spec_version: 1,
  types: ["calendar", "tasks", "custom:practice"],
  features: [
    { id: "calendar.events", status: "implemented", surfaces: ["data", "ui"], notes: "Stores and displays events" },
    { id: "calendar.reminders", status: "planned", surfaces: [] },
    { id: "tasks.items", status: "partial", surfaces: ["tools"] },
    { id: "custom:practice.sessions", status: "implemented", surfaces: ["ui"] },
  ],
};

const baseItem = {
  kind: "generated_app", description: "", version: "1.0.0", provider: "Ambient", tags: [], status: "ready",
};

const state = {
  version: 1,
  revision: 2,
  app_type_catalog: catalog,
  items: [
    { ...baseItem, catalog_id: "app:desk", title: "My Desk", ui_app_id: "desk", app_spec: spec },
    { ...baseItem, kind: "mcp", catalog_id: "mcp:calendar", title: "Time Tools", launch_mode: "details", app_spec: { ...spec, types: ["calendar"], features: [spec.features[0]] } },
    { ...baseItem, catalog_id: "app:weather", title: "Weather", ui_app_id: "weather" },
  ],
  root: ["folder:personal", "mcp:calendar", "app:weather"],
  folders: [{ id: "personal", name: "Personal", items: ["app:desk"] }],
};

function renderCenter(language: "zh" | "en" = "en") {
  return render(<AppCenter isOpen onClose={vi.fn()} pinnedWidgetIds={[]} onPinWidget={vi.fn()}
    onUnpinWidget={vi.fn()} onRunFullscreen={vi.fn()} language={language} />);
}

async function configureDesk() {
  fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "My Desk" } });
  const tile = await screen.findByRole("button", { name: "Open My Desk" });
  fireEvent.contextMenu(tile);
  fireEvent.click(screen.getByRole("menuitem", { name: "Configure properties" }));
}

describe("App Center app type standard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => state }));
  });

  it("filters by any declared purpose independently of catalog source without changing layout", async () => {
    renderCenter();
    await screen.findByText("Weather");
    const purpose = screen.getByLabelText("App purpose");
    fireEvent.change(purpose, { target: { value: "calendar" } });
    expect(screen.getByRole("button", { name: "Open My Desk" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Open Time Tools" })).toBeDefined();
    expect(screen.queryByText("Weather")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Apps" }));
    expect(screen.queryByRole("button", { name: "Open Time Tools" })).toBeNull();
    fireEvent.change(purpose, { target: { value: "tasks" } });
    expect(screen.getByRole("button", { name: "Open My Desk" })).toBeDefined();
    fireEvent.change(purpose, { target: { value: "custom:practice" } });
    expect(screen.getByRole("button", { name: "Open My Desk" })).toBeDefined();
    fireEvent.change(purpose, { target: { value: "unclassified" } });
    expect(screen.getByRole("button", { name: "Open Weather" })).toBeDefined();
    fireEvent.change(purpose, { target: { value: "all" } });
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByRole("button", { name: "Open folder Personal" })).toBeDefined();
    expect(vi.mocked(fetch).mock.calls).toHaveLength(1);
  });

  it("searches bilingual type and declared feature names regardless of UI language", async () => {
    renderCenter();
    await screen.findByText("Weather");
    for (const value of ["任务", "Task management", "事件管理", "custom:practice.sessions"]) {
      fireEvent.change(screen.getByLabelText("Search apps"), { target: { value } });
      expect(screen.getByRole("button", { name: "Open My Desk" })).toBeDefined();
      expect(screen.queryByText("Weather")).toBeNull();
    }
  });

  it("shows author declarations, surfaces, undeclared standard features and custom features", async () => {
    renderCenter();
    await screen.findByText("Weather");
    fireEvent.change(screen.getByLabelText("Search apps"), { target: { value: "My Desk" } });
    fireEvent.contextMenu(screen.getByRole("button", { name: "Open My Desk" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "View details" }));
    const declarations = screen.getByRole("region", { name: "Implementation declarations" });
    expect(within(declarations).getByText("Calendar")).toBeDefined();
    expect(within(declarations).getByText("Tasks")).toBeDefined();
    expect(within(declarations).getByText("custom:practice")).toBeDefined();
    expect(within(declarations).getByText("Event management")).toBeDefined();
    expect(within(declarations).getAllByText("Implemented")).toHaveLength(2);
    expect(within(declarations).getByText("Partial")).toBeDefined();
    expect(within(declarations).getByText("Planned")).toBeDefined();
    expect(within(declarations).getByText("Calendar views")).toBeDefined();
    expect(within(declarations).getByText("Not declared")).toBeDefined();
    expect(within(declarations).getByText("Data storage")).toBeDefined();
    expect(within(declarations).getByText("Agent tools")).toBeDefined();
    expect(within(declarations).getAllByText("Visual interface")).toHaveLength(2);
    expect(within(declarations).getByText("custom:practice.sessions")).toBeDefined();
    expect(within(declarations).getByText("Stores and displays events")).toBeDefined();
    expect(within(declarations).queryByText("Verified")).toBeNull();
  });

  it("keeps old catalog records usable and identifies their absent classification", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({
      ...state, app_type_catalog: undefined, items: [state.items[2]], root: ["app:weather"], folders: [],
    }) }));
    renderCenter();
    fireEvent.contextMenu(await screen.findByRole("button", { name: "Open Weather" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "View details" }));
    expect(within(screen.getByRole("region", { name: "Implementation declarations" })).getByText("Unclassified")).toBeDefined();
  });

  it("updates app types while preserving feature declarations and custom types", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      ok: true, status: 200, json: async () => init?.method === "PATCH" ? JSON.parse(String(init.body)) : state,
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderCenter();
    await screen.findByText("Weather");
    await configureDesk();
    expect((screen.getByRole("checkbox", { name: "custom:practice" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.change(screen.getByLabelText("Custom type ID"), { target: { value: "custom:journal" } });
    fireEvent.click(screen.getByRole("button", { name: "Add type" }));
    fireEvent.click(screen.getByRole("button", { name: "Save properties" }));
    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
      expect(patch).toBeDefined();
      expect(JSON.parse(String(patch![1]!.body)).app_spec).toEqual({
        ...spec, types: ["calendar", "tasks", "custom:practice", "custom:journal"],
      });
    });
  });

  it("explains removal of a type with feature declarations without silently deleting features", async () => {
    renderCenter();
    await screen.findByText("Weather");
    await configureDesk();
    fireEvent.click(screen.getByRole("checkbox", { name: "Calendar" }));
    fireEvent.click(screen.getByRole("button", { name: "Save properties" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("calendar.events"));
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
    expect(screen.getByRole("dialog", { name: "Configure app properties" })).toBeDefined();
  });

  it("preserves the namespace type required by a custom feature declaration", async () => {
    renderCenter();
    await screen.findByText("Weather");
    await configureDesk();
    fireEvent.click(screen.getByRole("checkbox", { name: "custom:practice" }));
    fireEvent.click(screen.getByRole("button", { name: "Save properties" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("custom:practice.sessions"));
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
  });

  it("accepts the standard's lowercase kebab namespace including a leading number", async () => {
    renderCenter();
    await screen.findByText("Weather");
    await configureDesk();
    fireEvent.change(screen.getByLabelText("Custom type ID"), { target: { value: "custom:2026-planning" } });
    fireEvent.click(screen.getByRole("button", { name: "Add type" }));
    expect((screen.getByRole("checkbox", { name: "custom:2026-planning" }) as HTMLInputElement).checked).toBe(true);
  });

  it("classifies an old app with an empty declaration list and permits clearing types without features", async () => {
    const classified = { ...state.items[2], app_spec: { spec_version: 1, types: ["calendar"], features: [] } };
    let current = { ...state, items: [state.items[2]], root: ["app:weather"], folders: [] };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      ok: true, status: 200, json: async () => {
        if (init?.method !== "PATCH") return current;
        const payload = JSON.parse(String(init.body));
        current = { ...current, items: [classified] };
        return payload;
      },
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderCenter();
    for (const clearing of [false, true]) {
      fireEvent.contextMenu(await screen.findByRole("button", { name: "Open Weather" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Configure properties" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "Calendar" }));
      fireEvent.click(screen.getByRole("button", { name: "Save properties" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Configure app properties" })).toBeNull());
      const patches = fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH");
      expect(JSON.parse(String(patches.at(-1)![1]!.body)).app_spec).toEqual(clearing ? null : classified.app_spec);
    }
  });

  it("rejects a malformed custom type locally", async () => {
    renderCenter();
    await screen.findByText("Weather");
    await configureDesk();
    fireEvent.change(screen.getByLabelText("Custom type ID"), { target: { value: "unknown" } });
    fireEvent.click(screen.getByRole("button", { name: "Add type" }));
    expect(screen.getByRole("alert").textContent).toContain("custom:");
    expect(screen.queryByRole("checkbox", { name: "unknown" })).toBeNull();
  });

  it("resets a custom purpose filter when editing its only app removes that type", async () => {
    const weather = { ...state.items[2], app_spec: { spec_version: 1, types: ["custom:temporary"], features: [] } };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      ok: true, status: 200, json: async () => init?.method === "PATCH"
        ? JSON.parse(String(init.body))
        : { ...state, items: [weather], root: [weather.catalog_id], folders: [] },
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderCenter();
    await screen.findByText("Weather");
    fireEvent.change(screen.getByLabelText("App purpose"), { target: { value: "custom:temporary" } });
    fireEvent.contextMenu(screen.getByRole("button", { name: "Open Weather" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Configure properties" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "custom:temporary" }));
    fireEvent.click(screen.getByRole("button", { name: "Save properties" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Configure app properties" })).toBeNull());
    expect((screen.getByLabelText("App purpose") as HTMLSelectElement).value).toBe("all");
    expect(screen.getByRole("button", { name: "Open Weather" })).toBeDefined();
    expect(screen.queryByRole("option", { name: "custom:temporary" })).toBeNull();
  });

  it("resets a removed custom purpose after the installed catalog refreshes", async () => {
    let current = {
      ...state,
      items: [state.items[2], { ...baseItem, catalog_id: "app:temporary", title: "Temporary", ui_app_id: "temporary", app_spec: { spec_version: 1, types: ["custom:temporary"], features: [] } }],
      root: ["app:weather", "app:temporary"], folders: [],
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => ({ ok: true, status: 200, json: async () => current })));
    renderCenter();
    await screen.findByText("Temporary");
    fireEvent.change(screen.getByLabelText("App purpose"), { target: { value: "custom:temporary" } });
    expect(screen.queryByText("Weather")).toBeNull();
    current = { ...current, items: [state.items[2]], root: ["app:weather"] };
    fireEvent(window, new Event("app-store-refresh"));
    await waitFor(() => expect(screen.queryByRole("option", { name: "custom:temporary" })).toBeNull());
    expect((screen.getByLabelText("App purpose") as HTMLSelectElement).value).toBe("all");
    expect(screen.getByRole("button", { name: "Open Weather" })).toBeDefined();
  });
});
