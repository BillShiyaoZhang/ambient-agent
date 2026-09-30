import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppCenter } from "../../frontend/src/components/AppCenter";

vi.mock("../../frontend/src/services/websocket", () => ({ default: { sendMessage: vi.fn() } }));

function setupAction(property: Record<string, unknown>, required = true) {
  const state = {
    version: 1, revision: 1, root: ["mcp:audit:tool"], folders: [],
    items: [{
      catalog_id: "mcp:audit:tool", kind: "mcp", title: "Audit Tool", description: "Local audit fixture",
      version: "1", provider: "Audit", tags: [], ui_app_id: null, status: "ready", launch_mode: "actions",
      actions: [{ id: "execute", title: "Execute", input_schema: { type: "object", required: required ? ["value"] : [], properties: { value: { title: "Value", ...property } } } }],
    }],
  };
  const requests: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/api/runs")) {
      requests.push(JSON.parse(String(init?.body)));
      return { ok: true, json: async () => ({ id: "audit-run" }) } as Response;
    }
    return { ok: true, json: async () => state } as Response;
  }));
  render(<AppCenter mode="home" isOpen language="en" onClose={() => {}} pinnedWidgetIds={[]} onPinWidget={() => {}} onUnpinWidget={() => {}} onRunFullscreen={() => {}} />);
  return requests;
}

describe("App Center JSON-schema argument probes", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("submits an array field as an array rather than its text representation", async () => {
    const requests = setupAction({ type: "array", items: { type: "integer" } });
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.change(screen.getByRole("textbox", { name: "Value *" }), { target: { value: "[1,2]" } });
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ input: { value: [1, 2] } });
  });

  it("preserves the boolean type of a selected enum value", async () => {
    const requests = setupAction({ type: "boolean", enum: [true, false] });
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.change(screen.getByRole("combobox", { name: "Value *" }), { target: { value: "true" } });
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ input: { value: true } });
  });

  it("omits an untouched optional number instead of submitting invalid null", async () => {
    const requests = setupAction({ type: "number" }, false);
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ input: {} });
    expect((requests[0] as { input: Record<string, unknown> }).input).not.toHaveProperty("value");
  });

  it("parses nested object JSON while preserving explicit defaults", async () => {
    const requests = setupAction({ type: "object", required: ["title"], properties: { title: { type: "string" }, count: { type: "integer" } } });
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.change(screen.getByRole("textbox", { name: "Value *" }), { target: { value: '{"title":"Ready","count":2}' } });
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ input: { value: { title: "Ready", count: 2 } } });
  });

  it.each([
    [{ type: "array", items: { type: "integer" } }, '["wrong"]', /Value.*integer/i],
    [{ type: "array", items: { type: "integer" } }, '[', /Value.*JSON/i],
    [{ type: "object", required: ["title"], properties: { title: { type: "string" } } }, '{}', /Value.*title.*required/i],
    [{ type: "integer" }, '1.5', /Value.*integer/i],
  ])("validates structured and numeric arguments before starting a Run (%s)", async (property, input, error) => {
    const requests = setupAction(property);
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.change(screen.getByLabelText("Value *"), { target: { value: input } });
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    expect(await screen.findByText(error)).toBeDefined();
    expect(requests).toHaveLength(0);
  });

  it.each([
    [{ type: "number", default: 0 }, 0],
    [{ type: ["number", "null"], default: null }, null],
    [{ type: "boolean", default: false }, false],
    [{ type: "object", default: { title: "Default" } }, { title: "Default" }],
  ])("keeps explicit defaults rather than omitting them (%s)", async (schema, value) => {
    const requests = setupAction(schema, false);
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ input: { value } });
  });

  it("accepts true in a nullable boolean field", async () => {
    const requests = setupAction({ type: ["boolean", "null"] });
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.change(screen.getByRole("textbox", { name: "Value *" }), { target: { value: "true" } });
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ input: { value: true } });
  });

  it("distinguishes numeric and string enum choices with identical labels", async () => {
    const requests = setupAction({ enum: [1, "1"] });
    fireEvent.click(await screen.findByText("Audit Tool"));
    fireEvent.change(screen.getByRole("combobox", { name: "Value *" }), { target: { value: '"1"' } });
    fireEvent.click(screen.getByRole("button", { name: "Run in background" }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ input: { value: "1" } });
  });
});
