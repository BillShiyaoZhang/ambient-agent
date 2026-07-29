import { describe, expect, it, vi } from "vitest";

import {
  MAX_WIDGET_STORAGE_ENTRIES,
  MAX_WIDGET_STORAGE_KEY_LENGTH,
  MAX_WIDGET_STORAGE_TOTAL_BYTES,
  MAX_WIDGET_STORAGE_VALUE_BYTES,
  WidgetStorageBroker,
  type WidgetStorageAdapter,
} from "../../frontend/src/services/widgetStorage";


class MemoryStorageAdapter implements WidgetStorageAdapter {
  readonly values = new Map<string, Map<string, unknown>>();
  readonly calls: Array<{ operation: string; widgetId: string; key?: string }> = [];

  private namespace(widgetId: string) {
    let values = this.values.get(widgetId);
    if (!values) {
      values = new Map();
      this.values.set(widgetId, values);
    }
    return values;
  }

  async get(widgetId: string, key: string) {
    this.calls.push({ operation: "get", widgetId, key });
    return this.namespace(widgetId).get(key) ?? null;
  }

  async usage(widgetId: string, key: string) {
    const values = this.namespace(widgetId);
    const size = (value: unknown) =>
      new TextEncoder().encode(JSON.stringify(value)).byteLength;
    return {
      entries: values.size,
      totalBytes: [...values.values()].reduce(
        (total, value) => total + size(value),
        0,
      ),
      existingBytes: values.has(key) ? size(values.get(key)) : null,
    };
  }

  async set(widgetId: string, key: string, value: unknown) {
    this.calls.push({ operation: "set", widgetId, key });
    this.namespace(widgetId).set(key, value);
  }

  async delete(widgetId: string, key: string) {
    this.calls.push({ operation: "delete", widgetId, key });
    this.namespace(widgetId).delete(key);
  }

  async clear(widgetId: string) {
    this.calls.push({ operation: "clear", widgetId });
    this.namespace(widgetId).clear();
  }

  async list(widgetId: string) {
    this.calls.push({ operation: "list", widgetId });
    return [...this.namespace(widgetId).keys()].sort();
  }
}


describe("WidgetStorageBroker", () => {
  it("always uses its host-bound widget identity", async () => {
    const adapter = new MemoryStorageAdapter();
    const notes = new WidgetStorageBroker("notes", adapter);
    const weather = new WidgetStorageBroker("weather", adapter);

    expect(await notes.handle({
      type: "storage_request",
      request_id: "set-1",
      operation: "set",
      key: "draft",
      value: { text: "hello" },
      widget_id: "weather",
    })).toEqual({
      type: "storage_response",
      request_id: "set-1",
      result: { status: "ok" },
    });
    expect(await weather.handle({
      type: "storage_request",
      request_id: "get-1",
      operation: "get",
      key: "draft",
      widget_id: "notes",
    })).toEqual({
      type: "storage_response",
      request_id: "get-1",
      result: null,
    });
    expect(adapter.calls.map(({ widgetId }) => widgetId)).toEqual(["notes", "weather"]);
  });

  it("supports get, set, delete, clear, and sorted list responses", async () => {
    const adapter = new MemoryStorageAdapter();
    const broker = new WidgetStorageBroker("notes", adapter);
    await broker.handle({
      type: "storage_request",
      request_id: "set-b",
      operation: "set",
      key: "b",
      value: [1, true, null],
    });
    await broker.handle({
      type: "storage_request",
      request_id: "set-a",
      operation: "set",
      key: "a",
      value: { nested: "value" },
    });

    expect(await broker.handle({
      type: "storage_request",
      request_id: "list",
      operation: "list",
    })).toEqual({
      type: "storage_response",
      request_id: "list",
      result: ["a", "b"],
    });
    expect(await broker.handle({
      type: "storage_request",
      request_id: "get",
      operation: "get",
      key: "a",
    })).toEqual({
      type: "storage_response",
      request_id: "get",
      result: { nested: "value" },
    });
    expect((await broker.handle({
      type: "storage_request",
      request_id: "delete",
      operation: "delete",
      key: "a",
    })).result).toEqual({ status: "ok" });
    expect((await broker.handle({
      type: "storage_request",
      request_id: "clear",
      operation: "clear",
    })).result).toEqual({ status: "ok" });
  });

  it("rejects invalid keys and values before touching IndexedDB", async () => {
    const adapter = new MemoryStorageAdapter();
    const broker = new WidgetStorageBroker("notes", adapter);
    const circular: Record<string, unknown> = {};
    circular.self = circular;

    for (const [requestId, key, value] of [
      ["empty", "", "value"],
      ["long-key", "x".repeat(MAX_WIDGET_STORAGE_KEY_LENGTH + 1), "value"],
      ["undefined", "key", undefined],
      ["non-finite", "key", Number.POSITIVE_INFINITY],
      ["circular", "key", circular],
      ["too-large", "key", "x".repeat(MAX_WIDGET_STORAGE_VALUE_BYTES + 1)],
    ] as const) {
      const response = await broker.handle({
        type: "storage_request",
        request_id: requestId,
        operation: "set",
        key,
        value,
      });
      expect(response.error?.code).toBe("storage_invalid_request");
    }
    expect(adapter.calls).toEqual([]);
  });

  it("returns a bounded error response when the adapter fails", async () => {
    const adapter = new MemoryStorageAdapter();
    vi.spyOn(adapter, "get").mockRejectedValue(new Error("disk unavailable"));
    const broker = new WidgetStorageBroker("notes", adapter);

    expect(await broker.handle({
      type: "storage_request",
      request_id: "get",
      operation: "get",
      key: "draft",
    })).toEqual({
      type: "storage_response",
      request_id: "get",
      error: {
        code: "storage_unavailable",
        message: "disk unavailable",
      },
    });
  });

  it("enforces per-widget entry and aggregate byte budgets", async () => {
    const adapter = new MemoryStorageAdapter();
    const values = new Map<string, unknown>();
    for (let index = 0; index < MAX_WIDGET_STORAGE_ENTRIES; index += 1) {
      values.set(`key-${index}`, "value");
    }
    adapter.values.set("notes", values);
    const broker = new WidgetStorageBroker("notes", adapter);

    const entryLimit = await broker.handle({
      type: "storage_request",
      request_id: "entry-limit",
      operation: "set",
      key: "one-too-many",
      value: "value",
    });
    expect(entryLimit.error?.code).toBe("storage_quota_exceeded");

    vi.spyOn(adapter, "usage").mockResolvedValue({
      entries: 1,
      totalBytes: MAX_WIDGET_STORAGE_TOTAL_BYTES,
      existingBytes: null,
    });
    const byteLimit = await broker.handle({
      type: "storage_request",
      request_id: "byte-limit",
      operation: "set",
      key: "new",
      value: "value",
    });
    expect(byteLimit.error?.code).toBe("storage_quota_exceeded");
  });
});
