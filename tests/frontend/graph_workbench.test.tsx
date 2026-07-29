import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { GraphWorkbench } from "../../frontend/src/components/graph/GraphWorkbench";

const ontology = {
  version: 1,
  title: "Ontology",
  description: "Canonical ontology",
  nodes: [
    { id: "ontology:Thing", label: "Thing", kind: "ontology", status: "existing" },
    { id: "ontology:Task", label: "Task", kind: "ontology", status: "existing" },
  ],
  edges: [
    {
      id: "ontology:Task:subclass",
      source: "ontology:Task",
      target: "ontology:Thing",
      label: "subclass of",
      kind: "inheritance",
    },
  ],
  metadata: { total_nodes: 2, returned_nodes: 2, truncated: false },
};

const knowledgeGraph = {
  version: 1,
  title: "Knowledge graph",
  description: "User context",
  nodes: [{ id: "record:task-1", label: "Prepare release", kind: "record", status: "existing" }],
  edges: [],
  metadata: { total_nodes: 1, returned_nodes: 1, truncated: false },
};

const dataMap = {
  version: 1,
  title: "Privacy data map",
  description: "No raw payload",
  nodes: [
    { id: "observed", label: "Observed model exchange", kind: "observed", status: "observed" },
    { id: "declared", label: "Declared Graph access", kind: "declared", status: "declared" },
    { id: "unknown", label: "Unknown / uninstrumented", kind: "unknown", status: "unknown" },
  ],
  edges: [],
  metadata: {
    evidence_count: 2,
    coverage: ["LLM audit metadata"],
    blind_spots: ["Uninstrumented local flows"],
  },
};

describe("GraphWorkbench", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(
          url.endsWith("/api/data-map")
            ? dataMap
            : { version: 1, generated_at: "2026-07-29T00:00:00Z", ontology, knowledge_graph: knowledgeGraph },
        ),
      });
    }));
  });

  it("switches among ontology, knowledge, workflow, and privacy scenes", async () => {
    render(<GraphWorkbench open language="en" onClose={() => {}} />);

    expect(await screen.findByText("Canonical ontology")).toBeDefined();
    const ontologyTab = screen.getByRole("tab", { name: "Ontology" });
    const knowledgeTab = screen.getByRole("tab", { name: "Knowledge graph" });
    expect(ontologyTab.getAttribute("tabindex")).toBe("0");
    expect(knowledgeTab.getAttribute("tabindex")).toBe("-1");
    expect(ontologyTab.getAttribute("aria-controls")).toBe("graph-workbench-panel");
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(
      "graph-workbench-tab-ontology",
    );

    fireEvent.keyDown(ontologyTab, { key: "ArrowRight" });
    expect(await screen.findByText("Prepare release")).toBeDefined();
    expect(document.activeElement).toBe(knowledgeTab);
    expect(knowledgeTab.getAttribute("tabindex")).toBe("0");
    fireEvent.change(screen.getByRole("searchbox", { name: "Search graph" }), {
      target: { value: "prepare" },
    });

    fireEvent.click(screen.getByRole("tab", { name: "Agent workflow" }));
    expect(await screen.findByText("Route intent")).toBeDefined();
    expect((screen.getByRole("searchbox", { name: "Search graph" }) as HTMLInputElement).value)
      .toBe("");
    expect(screen.getByRole("button", { name: "Refresh current graph" }).hasAttribute("disabled")).toBe(true);

    fireEvent.click(screen.getByRole("tab", { name: "Privacy map" }));
    expect(await screen.findByText("Unknown / uninstrumented")).toBeDefined();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/api\/data-map$/)));
  });
});
