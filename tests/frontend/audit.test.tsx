import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuditLogPanel, AuditLogEntry } from "../../frontend/src/components/AuditLogPanel";
import React, { act } from "react";

// Mock fetch globally
const mockLogs: AuditLogEntry[] = [
  {
    id: 1,
    timestamp: "2026-07-07T12:00:00.000Z",
    provider: "ollama",
    model: "llama3",
    prompt: "Show me weather",
    response: "Beijing weather widget XML"
  }
];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

describe("AuditLogPanel Component", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockLogs),
      })
    ));
  });

  it("should fetch and render audit logs correctly", async () => {
    render(<AuditLogPanel isOpen={true} language="en" onClose={() => {}} />);
    
    // Expect loading state or title
    expect(screen.getByText("Data Transmission Audit Log")).toBeDefined();
    
    // Wait for the mock log entry to be displayed
    await waitFor(() => {
      expect(screen.getByText(/llama3/)).toBeDefined();
      expect(screen.getByText(/Show me weather/)).toBeDefined();
    });

    const disclosure = screen.getByRole("button", { name: /llama3.*Show me weather/i });
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
  });

  it("surfaces an audit request failure and lets the user retry", async () => {
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("backend offline"))
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockLogs),
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<AuditLogPanel isOpen language="en" onClose={() => {}} />);

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Audit log request failed: backend offline",
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/llama3/)).toBeDefined();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(errorLog).toHaveBeenCalledOnce();
    errorLog.mockRestore();
  });

  it("keeps the redacted privacy map separate from raw audit payloads", async () => {
    const dataMap = {
      version: 1,
      title: "Privacy data map",
      description: "Derived metadata only",
      nodes: [
        { id: "local", label: "Local workspace", kind: "source", status: "observed" },
        { id: "provider", label: "LLM provider", kind: "destination", status: "observed" },
        { id: "unknown", label: "Unknown / uninstrumented", kind: "coverage", status: "unknown" },
      ],
      edges: [
        { id: "flow", source: "local", target: "provider", label: "model exchange", kind: "observed" },
      ],
      metadata: {
        evidence_count: 1,
        coverage: ["LLM audit metadata"],
        blind_spots: ["Widget-local storage"],
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(url.endsWith("/api/data-map") ? dataMap : mockLogs),
      });
    }));

    render(<AuditLogPanel isOpen language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Data map" }));

    expect(await screen.findByText("Unknown / uninstrumented")).toBeDefined();
    expect(screen.getByRole("searchbox", { name: "Search graph" })).toBeDefined();
    expect(screen.queryByText("Show me weather")).toBeNull();
    expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/api\/data-map$/));
  });

  it("reprojects the retained privacy map in Chinese without refetching business data", async () => {
    const dataMap = {
      version: 1,
      title: "Privacy data map",
      nodes: [
        {
          id: "data-source:local-runtime-context",
          label: "Local runtime context",
          kind: "data_source",
          status: "existing",
          summary: "Local context category; raw values are intentionally omitted.",
          badges: ["local"],
        },
        {
          id: "provider:custom",
          label: "业务 Provider 原文",
          kind: "provider",
          status: "observed",
          summary: "Provider observed in retained Audit metadata.",
          badges: ["observed"],
        },
      ],
      edges: [],
      metadata: {},
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(url.endsWith("/api/data-map") ? dataMap : mockLogs),
      });
    }));

    const view = render(<AuditLogPanel isOpen language="en" onClose={() => {}} />);

    fireEvent.click(screen.getByRole("button", { name: "Data map" }));
    expect(await screen.findByText("Local runtime context")).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(2);

    view.rerender(<AuditLogPanel isOpen language="zh" onClose={() => {}} />);

    expect(screen.getByRole("heading", { name: "数据传输审计日志" })).toBeDefined();
    expect(await screen.findByText("本地运行时上下文")).toBeDefined();
    expect(screen.getByText("业务 Provider 原文")).toBeDefined();
    expect(screen.getByRole("searchbox", { name: "搜索图谱" })).toBeDefined();
    expect(screen.getByRole("button", { name: "刷新数据地图" })).toBeDefined();
    expect(screen.getAllByRole("button", { name: "关闭审计日志" })).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("localizes a data-map request error when the language changes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      return Promise.resolve(url.endsWith("/api/data-map")
        ? { ok: false, status: 429 }
        : { ok: true, json: () => Promise.resolve(mockLogs) });
    }));

    const view = render(<AuditLogPanel isOpen language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Data map" }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Data map request failed (429)",
    );

    view.rerender(<AuditLogPanel isOpen language="zh" onClose={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("数据地图请求失败 (429)");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("re-derives the map after reopen and ignores an older in-flight snapshot", async () => {
    const firstMap = deferred<Record<string, unknown>>();
    const secondMap = deferred<Record<string, unknown>>();
    let mapCalls = 0;
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (!url.endsWith("/api/data-map")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockLogs) });
      }
      mapCalls += 1;
      const payload = mapCalls === 1 ? firstMap.promise : secondMap.promise;
      return Promise.resolve({ ok: true, json: () => payload });
    }));
    const dataset = (label: string) => ({
      version: 1,
      title: "Privacy data map",
      nodes: [{ id: label, label, kind: "coverage", status: "observed" }],
      edges: [],
      metadata: {},
    });

    const { rerender } = render(<AuditLogPanel isOpen language="en" onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Data map" }));
    await waitFor(() => expect(mapCalls).toBe(1));

    rerender(<AuditLogPanel isOpen={false} language="en" onClose={() => {}} />);
    rerender(<AuditLogPanel isOpen language="en" onClose={() => {}} />);
    await waitFor(() => expect(mapCalls).toBe(2));

    await act(async () => {
      secondMap.resolve(dataset("Fresh snapshot"));
      await secondMap.promise;
    });
    expect(await screen.findByText("Fresh snapshot")).toBeDefined();

    await act(async () => {
      firstMap.resolve(dataset("Stale snapshot"));
      await firstMap.promise;
    });
    expect(screen.queryByText("Stale snapshot")).toBeNull();
    expect(screen.getByText("Fresh snapshot")).toBeDefined();
  });
});
