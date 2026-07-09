import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { SandboxWidget } from "../../frontend/src/components/SandboxWidget";
import { Widget } from "../../frontend/src/components/DashboardCanvas";
import React from "react";

describe("SandboxWidget with ambient SDK Injection", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("should inject ambient SDK and allow fetching model data", async () => {
    const mockData = { items: ["buy milk", "feed cat"] };
    
    // Mock the fetch request to /api/apps/todo-sdk/data
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => mockData,
    });

    const mockWidget: Widget = {
      id: "todo-sdk",
      title: "Todo SDK Test",
      html: '<div id="output" data-testid="output">Loading...</div>',
      css: "",
      js: `
        ambient.model.get().then(data => {
          root.querySelector("#output").textContent = data.items.join(", ");
        });
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    // Wait for the async model.get() to resolve and update DOM
    await waitFor(() => {
      const output = screen.getByTestId("output");
      expect(output.textContent).toBe("buy milk, feed cat");
    });

    expect(global.fetch).toHaveBeenCalledWith("http://localhost:8000/api/apps/todo-sdk/data");
  });

  it("should allow saving model data via ambient.model.set", async () => {
    // Mock GET (initially empty) and POST
    (global.fetch as any).mockImplementation((url: string, options?: any) => {
      if (options && options.method === "POST") {
        expect(url).toBe("http://localhost:8000/api/apps/todo-sdk/data");
        expect(JSON.parse(options.body)).toEqual({ items: ["done"] });
        return Promise.resolve({ ok: true, json: async () => ({ status: "ok" }) });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    const mockWidget: Widget = {
      id: "todo-sdk",
      title: "Todo SDK Test",
      html: '<button id="btn" data-testid="btn">Save</button>',
      css: "",
      js: `
        root.querySelector("#btn").addEventListener("click", () => {
          ambient.model.set({ items: ["done"] });
        });
      `,
    };

    render(<SandboxWidget widget={mockWidget} />);

    const btn = screen.getByTestId("btn");
    btn.click();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "http://localhost:8000/api/apps/todo-sdk/data",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
        })
      );
    });
  });
});
