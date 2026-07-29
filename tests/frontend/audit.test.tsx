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
    render(<AuditLogPanel isOpen={true} onClose={() => {}} />);
    
    // Expect loading state or title
    expect(screen.getByText("Data Transmission Audit Log")).toBeDefined();
    
    // Wait for the mock log entry to be displayed
    await waitFor(() => {
      expect(screen.getByText(/llama3/)).toBeDefined();
      expect(screen.getByText(/Show me weather/)).toBeDefined();
    });
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

    render(<AuditLogPanel isOpen onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Data map" }));

    expect(await screen.findByText("Unknown / uninstrumented")).toBeDefined();
    expect(screen.queryByText("Show me weather")).toBeNull();
    expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/api\/data-map$/));
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

    const { rerender } = render(<AuditLogPanel isOpen onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Data map" }));
    await waitFor(() => expect(mapCalls).toBe(1));

    rerender(<AuditLogPanel isOpen={false} onClose={() => {}} />);
    rerender(<AuditLogPanel isOpen onClose={() => {}} />);
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
