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
    const view = render(<GraphWorkbench open language="en" onClose={() => {}} />);

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

    view.rerender(<GraphWorkbench open language="zh" onClose={() => {}} />);
    expect(screen.getByRole("tab", { name: "隐私数据图" })).toBeDefined();
    expect(screen.getByRole("searchbox", { name: "搜索图谱" })).toBeDefined();
    expect(screen.getByText("隐私数据地图")).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("tab", { name: "本体" }));
    expect(await screen.findByText("规范本体实体及其子类关系。")).toBeDefined();
    fireEvent.click(screen.getByRole("tab", { name: "知识图谱" }));
    expect(await screen.findByText("用户 ContextRecord 及其有向关系的有界快照。")).toBeDefined();
    expect(screen.getByText("Prepare release")).toBeDefined();
    fireEvent.click(screen.getByRole("tab", { name: "Agent 工作流" }));
    expect(await screen.findByText("路由意图")).toBeDefined();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("localizes graph snapshot request errors without refetching on a language change", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 503,
    } as Response);

    const view = render(<GraphWorkbench open language="en" onClose={() => {}} />);

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Graph explorer request failed (503)",
    );
    expect(fetch).toHaveBeenCalledTimes(1);

    view.rerender(<GraphWorkbench open language="zh" onClose={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("图谱探索请求失败 (503)");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("localizes privacy-map request errors after the active language changes", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/data-map")) {
        return Promise.resolve({ ok: false, status: 502 } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          version: 1,
          generated_at: "2026-07-29T00:00:00Z",
          ontology,
          knowledge_graph: knowledgeGraph,
        }),
      } as Response);
    });

    const view = render(<GraphWorkbench open language="en" onClose={() => {}} />);
    await screen.findByText("Canonical ontology");
    fireEvent.click(screen.getByRole("tab", { name: "Privacy map" }));

    expect((await screen.findByRole("alert")).textContent).toContain(
      "Data map request failed (502)",
    );
    view.rerender(<GraphWorkbench open language="zh" onClose={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("数据地图请求失败 (502)");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("exits graph fullscreen with Escape without closing the workbench", async () => {
    const onClose = vi.fn();
    render(<GraphWorkbench open language="en" onClose={onClose} />);

    await screen.findByText("Canonical ontology");
    const explorer = screen.getByRole("region", { name: "Ontology" });
    Object.defineProperty(explorer, "requestFullscreen", {
      configurable: true,
      value: undefined,
    });
    fireEvent.click(screen.getByRole("button", { name: "Enter fullscreen" }));
    await waitFor(() => expect(explorer.getAttribute("data-fullscreen")).toBe("true"));

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(explorer.getAttribute("data-fullscreen")).toBe("false"));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "Graph Explorer" })).toBeDefined();
  });
});
