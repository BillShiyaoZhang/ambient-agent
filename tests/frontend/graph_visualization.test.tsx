import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});
vi.stubGlobal("DOMMatrixReadOnly", class {
  m22 = 1;
});

import { GraphExplorer } from "../../frontend/src/components/graph/GraphExplorer";
import {
  deterministicGraphLayout,
  normalizeGraphDataset,
  normalizeGraphExplorerPayload,
  type GraphDataset,
} from "../../frontend/src/lib/graphVisualization";
import {
  DURABLE_WORKFLOW_DESCRIPTOR,
  dataMapToGraph,
  schemaProposalToGraph,
  workflowToGraph,
} from "../../frontend/src/lib/graphScenes";

const explorationDataset: GraphDataset = {
  version: 1,
  nodes: [
    {
      id: "alpha",
      label: "Alpha person",
      kind: "person",
      status: "succeeded",
      summary: "Owns the launch",
      details: { team: "Core" },
    },
    {
      id: "beta",
      label: "Beta company",
      kind: "company",
      status: "running",
      summary: "Launch partner",
    },
    {
      id: "gamma",
      label: "Gamma person",
      kind: "person",
      status: "not_started",
      summary: "Not connected",
    },
  ],
  edges: [
    {
      id: "alpha-beta",
      source: "alpha",
      target: "beta",
      label: "works with",
      kind: "relationship",
      details: { since: 2024 },
    },
  ],
  metadata: {
    title: "Exploration fixture",
    description: "A searchable graph",
    truncated: true,
    totalNodes: 20,
    returnedNodes: 3,
  },
};

describe("GraphExplorer", () => {
  it("renders coverage metadata as readable localized fields instead of JSON", () => {
    render(
      <GraphExplorer
        language="zh"
        dataset={{
          ...explorationDataset,
          metadata: {
            ...explorationDataset.metadata,
            coverage: {
              "证据数": 2,
              "插桩范围": ["保留的 LLM 调用元数据", "当前 App Manifest 声明"],
            },
            semantics: {
              "已观察": "来自审计窗口中的聚合证据。",
              "未知": "插桩覆盖之外的可能数据流。",
            },
          },
        }}
      />,
    );

    const coverage = screen.getByRole("region", { name: "图谱覆盖范围" });
    expect(within(coverage).getByText("证据数")).toBeDefined();
    expect(within(coverage).getByText("保留的 LLM 调用元数据")).toBeDefined();
    expect(within(coverage).getByText("已观察")).toBeDefined();
    expect(coverage.textContent).not.toContain('{"证据数"');
  });

  it("pans the viewport when the primary pointer drags the blank canvas", async () => {
    const { container } = render(<GraphExplorer dataset={explorationDataset} />);

    const pane = container.querySelector<HTMLElement>(".react-flow__pane");
    const viewport = container.querySelector<HTMLElement>(".react-flow__viewport");
    expect(pane).not.toBeNull();
    expect(viewport).not.toBeNull();
    expect(pane?.classList.contains("draggable")).toBe(true);
    expect(pane?.classList.contains("selection")).toBe(false);

    const eventView = pane?.ownerDocument.defaultView;
    expect(eventView).not.toBeNull();
    const initialTransform = viewport?.style.transform;
    const mouseEvent = (type: string, init: MouseEventInit) => {
      const event = new MouseEvent(type, {
        bubbles: true,
        ...init,
      });
      Object.defineProperty(event, "view", { value: eventView });
      return event;
    };
    fireEvent(pane as HTMLElement, mouseEvent("mousedown", {
      button: 0,
      buttons: 1,
      clientX: 80,
      clientY: 80,
    }));
    fireEvent(eventView as Window, mouseEvent("mousemove", {
      buttons: 1,
      clientX: 140,
      clientY: 120,
    }));
    fireEvent(eventView as Window, mouseEvent("mouseup", {
      button: 0,
      buttons: 0,
      clientX: 140,
      clientY: 120,
    }));
    expect(viewport?.style.transform).not.toBe(initialTransform);
    // d3-zoom briefly suppresses the click immediately following a drag.
    // Let that one-shot listener clear so this test cannot affect the next one.
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
  });

  it("uses an accessible fullscreen fallback and exits it with Escape", async () => {
    const { container } = render(
      <div className="system-dialog">
        <GraphExplorer dataset={explorationDataset} />
      </div>,
    );

    const explorer = screen.getByRole("region", { name: "Interactive graph explorer" });
    const fullscreenHost = container.querySelector(".system-dialog");
    Object.defineProperty(explorer, "requestFullscreen", {
      configurable: true,
      value: undefined,
    });
    fireEvent.click(screen.getByRole("button", { name: "Enter fullscreen" }));

    await waitFor(() => {
      expect(explorer.classList.contains("is-fullscreen-fallback")).toBe(true);
    });
    expect(fullscreenHost?.classList.contains("has-graph-fullscreen-fallback")).toBe(true);
    expect(explorer.getAttribute("data-fullscreen")).toBe("true");
    expect(screen.getByRole("button", { name: "Exit fullscreen" })).toBeDefined();
    expect(screen.getByRole("status", { name: "Fullscreen status" }).textContent)
      .toContain("Fullscreen mode");

    const focusable = [...explorer.querySelectorAll<HTMLElement>(
      "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), "
      + "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    )];
    expect(focusable.length).toBeGreaterThan(1);
    const firstFocusable = focusable[0];
    const lastFocusable = focusable[focusable.length - 1];
    lastFocusable.focus();
    fireEvent.keyDown(lastFocusable, { key: "Tab" });
    expect(document.activeElement).toBe(firstFocusable);
    firstFocusable.focus();
    fireEvent.keyDown(firstFocusable, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(lastFocusable);

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(explorer.classList.contains("is-fullscreen-fallback")).toBe(false);
    });
    expect(fullscreenHost?.classList.contains("has-graph-fullscreen-fallback")).toBe(false);
    expect(screen.getByRole("button", { name: "Enter fullscreen" })).toBeDefined();
  });

  it("tracks native fullscreenchange events and exits through the control", async () => {
    let fullscreenElement: Element | null = null;
    const exitFullscreen = vi.fn(async () => {
      fullscreenElement = null;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      get: () => fullscreenElement,
    });
    Object.defineProperty(document, "exitFullscreen", {
      configurable: true,
      value: exitFullscreen,
    });

    const { unmount } = render(<GraphExplorer dataset={explorationDataset} />);
    const explorer = screen.getByRole("region", { name: "Interactive graph explorer" });
    const requestFullscreen = vi.fn(async () => {
      fullscreenElement = explorer;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    Object.defineProperty(explorer, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });

    fireEvent.click(screen.getByRole("button", { name: "Enter fullscreen" }));
    await waitFor(() => expect(requestFullscreen).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "Exit fullscreen" })).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Exit fullscreen" }));
    await waitFor(() => expect(exitFullscreen).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "Enter fullscreen" })).toBeDefined();

    unmount();
    delete (document as Document & { fullscreenElement?: Element | null }).fullscreenElement;
    delete (document as Document & { exitFullscreen?: () => Promise<void> }).exitFullscreen;
  });

  it("renders shared controls, states, and accessibility text in Chinese and switches live", () => {
    const localizedDataset: GraphDataset = {
      ...explorationDataset,
      nodes: explorationDataset.nodes.map((node) => (
        node.id === "gamma" ? { ...node, status: "abstract" } : node
      )),
      metadata: {
        ...explorationDataset.metadata,
        coverage: ["fixture coverage"],
      },
    };
    const { rerender } = render(
      <GraphExplorer dataset={localizedDataset} language="zh" />,
    );

    expect(screen.getByRole("searchbox", { name: "搜索图谱" })).toBeDefined();
    expect(screen.getByRole("button", { name: "水平布局" })).toBeDefined();
    expect(screen.getByRole("button", { name: "进入全屏" })).toBeDefined();
    expect(screen.getByRole("complementary", { name: "图谱详情" })).toBeDefined();
    expect(screen.getByRole("status").textContent).toContain("当前显示 20 个节点中的 3 个");
    expect(screen.getByLabelText(/Alpha person, person, 成功/)).toBeDefined();
    expect(screen.getByLabelText(/Gamma person, person, 抽象/)).toBeDefined();

    fireEvent.click(screen.getByLabelText(/Alpha person, person, 成功/));
    const details = screen.getByRole("complementary", { name: "图谱详情" });
    expect(within(details).getByRole("button", { name: "聚焦一跳邻居" })).toBeDefined();
    expect(within(details).getByText("覆盖范围与限制")).toBeDefined();

    rerender(<GraphExplorer dataset={localizedDataset} language="en" />);

    expect(screen.getByRole("searchbox", { name: "Search graph" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Horizontal layout" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Enter fullscreen" })).toBeDefined();
    expect(screen.getByRole("complementary", { name: "Graph details" })).toBeDefined();
    expect(screen.getByLabelText(/Alpha person, person, Succeeded/)).toBeDefined();
    expect(screen.getByLabelText(/Gamma person, person, Abstract/)).toBeDefined();
  });

  it("searches node content and filters by kind", () => {
    render(<GraphExplorer dataset={explorationDataset} />);

    const search = screen.getByRole("searchbox", { name: "Search graph" });
    fireEvent.change(search, { target: { value: "partner" } });
    expect(screen.getByLabelText(/Beta company/)).toBeDefined();
    expect(screen.queryByLabelText(/Alpha person/)).toBeNull();

    fireEvent.change(search, { target: { value: "" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "person" }));
    expect(screen.queryByLabelText(/Alpha person/)).toBeNull();
    expect(screen.getByLabelText(/Beta company/)).toBeDefined();
  });

  it("switches layout, inspects selections, focuses one-hop, and restores the overview", () => {
    render(<GraphExplorer dataset={explorationDataset} />);

    fireEvent.click(screen.getByRole("button", { name: "Vertical layout" }));
    expect(screen.getByRole("button", { name: "Vertical layout" }).getAttribute("aria-pressed")).toBe("true");

    const search = screen.getByRole("searchbox", { name: "Search graph" });
    fireEvent.click(screen.getByLabelText(/Alpha person/));
    const details = screen.getByRole("complementary", { name: "Graph details" });
    expect(within(details).getByRole("heading", { name: "Alpha person" })).toBeDefined();
    expect(within(details).getByText("Core")).toBeDefined();

    fireEvent.click(within(details).getByRole("button", { name: "Focus one-hop neighbors" }));
    expect(screen.getByLabelText(/Beta company/)).toBeDefined();
    expect(screen.queryByLabelText(/Gamma person/)).toBeNull();

    fireEvent.change(search, { target: { value: "alpha" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "company" }));
    expect(screen.queryByLabelText(/Beta company/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect((search as HTMLInputElement).value).toBe("");
    expect((screen.getByRole("checkbox", { name: "person" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "company" }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByLabelText(/Beta company/)).toBeDefined();
    expect(screen.getByLabelText(/Gamma person/)).toBeDefined();

    fireEvent.change(screen.getByRole("combobox", { name: "Inspect relationship" }), {
      target: { value: "alpha-beta" },
    });
    expect(within(details).getByRole("heading", { name: "works with" })).toBeDefined();
    expect(within(details).getByText("2024")).toBeDefined();
    expect(screen.getByRole("status").textContent).toContain("showing 3 of 20 nodes");
  });

  it("keeps user-adjusted node coordinates while inspecting nodes and edges", () => {
    render(<GraphExplorer dataset={explorationDataset} />);

    fireEvent.click(screen.getByLabelText(/Alpha person/));
    const selectedAlpha = screen.getByLabelText(/Alpha person/);
    const originalTransform = selectedAlpha.style.transform;

    fireEvent.keyDown(selectedAlpha, { key: "ArrowRight", code: "ArrowRight" });
    const movedTransform = screen.getByLabelText(/Alpha person/).style.transform;
    expect(movedTransform).not.toBe(originalTransform);

    fireEvent.change(screen.getByRole("combobox", { name: "Inspect relationship" }), {
      target: { value: "alpha-beta" },
    });
    expect(screen.getByLabelText(/Alpha person/).style.transform).toBe(movedTransform);

    fireEvent.click(screen.getByLabelText(/Beta company/));
    expect(screen.getByLabelText(/Alpha person/).style.transform).toBe(movedTransform);

    fireEvent.click(screen.getByRole("button", { name: "Reset view" }));
    expect(screen.getByLabelText(/Alpha person/).style.transform).toBe(originalTransform);
  });

  it("preserves selection, one-hop focus, and kind filters across live dataset refreshes", () => {
    const { rerender } = render(<GraphExplorer dataset={explorationDataset} />);

    fireEvent.click(screen.getByLabelText(/Alpha person/));
    const details = screen.getByRole("complementary", { name: "Graph details" });
    fireEvent.click(within(details).getByRole("button", { name: "Focus one-hop neighbors" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "company" }));
    const alpha = screen.getByLabelText(/Alpha person/);
    fireEvent.keyDown(alpha, { key: "ArrowRight", code: "ArrowRight" });
    const movedTransform = screen.getByLabelText(/Alpha person/).style.transform;

    rerender(
      <GraphExplorer
        dataset={{
          ...explorationDataset,
          nodes: explorationDataset.nodes.map((node) => (
            node.id === "alpha" ? { ...node, summary: "Live status refreshed" } : { ...node }
          )),
          edges: explorationDataset.edges.map((edge) => ({ ...edge })),
        }}
      />,
    );

    expect(within(details).getByRole("heading", { name: "Alpha person" })).toBeDefined();
    expect(within(details).getByText("Live status refreshed")).toBeDefined();
    expect((screen.getByRole("checkbox", { name: "company" }) as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByLabelText(/Gamma person/)).toBeNull();
    expect(screen.getByLabelText(/Alpha person/).style.transform).toBe(movedTransform);
  });

  it("renders original and filtered empty states", () => {
    const { rerender } = render(
      <GraphExplorer
        dataset={{
          version: 1,
          nodes: [],
          edges: [],
          metadata: { title: "Nothing here" },
        }}
      />,
    );
    expect(screen.getByText("No graph data")).toBeDefined();

    rerender(<GraphExplorer dataset={explorationDataset} />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Search graph" }), {
      target: { value: "does-not-exist" },
    });
    expect(screen.getByText("No nodes match the current search and filters")).toBeDefined();
  });

  it("reports detail-value truncation separately from topology truncation", () => {
    const dataset = normalizeGraphDataset({
      ...explorationDataset,
      metadata: {
        title: "Bounded details",
        truncated: false,
        detail_truncation: {
          truncated: true,
          values_truncated: 2,
        },
      },
    });

    render(<GraphExplorer dataset={dataset} />);

    expect(screen.getByText(/Some node or relationship details were shortened/).textContent)
      .toContain("2 values");
    expect(screen.queryByText(/Snapshot truncated: showing/)).toBeNull();
  });

  it("reports relationship counts when only the edge limit truncates topology", () => {
    render(
      <GraphExplorer
        dataset={{
          ...explorationDataset,
          metadata: {
            ...explorationDataset.metadata,
            totalNodes: 3,
            returnedNodes: 3,
            totalEdges: 1_001,
            returnedEdges: 1_000,
            truncated: true,
          },
        }}
      />,
    );

    expect(screen.getByRole("status").textContent)
      .toContain("showing 3 of 3 nodes and 1000 of 1001 relationships");
  });
});

describe("graph visualization adapters", () => {
  it("normalizes the ontology and knowledge-graph API payload", () => {
    const payload = normalizeGraphExplorerPayload({
      version: 1,
      generated_at: "2026-07-29T00:00:00Z",
      ontology: {
        title: "Ontology",
        nodes: [
          { id: "ontology:Task", label: "Task", kind: "ontology" },
          { id: "ontology:Thing", label: "Thing", kind: "ontology" },
        ],
        edges: [
          {
            id: "task-parent",
            source: "ontology:Task",
            target: "ontology:Thing",
            label: "subclass of",
            kind: "inheritance",
          },
        ],
        metadata: { total_nodes: 2, returned_nodes: 2, truncated: false },
      },
      knowledge_graph: {
        title: "Knowledge graph",
        nodes: [{ id: "record:1", label: "Ship release", kind: "record" }],
        edges: [],
        metadata: { total_nodes: 10, returned_nodes: 1, truncated: true },
      },
    });

    expect(payload.generatedAt).toBe("2026-07-29T00:00:00Z");
    expect(payload.ontology.nodes.map((node) => node.id)).toEqual([
      "ontology:Task",
      "ontology:Thing",
    ]);
    expect(payload.knowledgeGraph.metadata).toMatchObject({
      totalNodes: 10,
      returnedNodes: 1,
      truncated: true,
    });
  });

  it("preserves significant whitespace in node and edge identity fields", () => {
    const dataset = normalizeGraphDataset({
      nodes: [
        { id: "x", label: "Plain", kind: " record" },
        { id: " x", label: "Prefixed", kind: "record" },
      ],
      edges: [
        {
          id: " relationship",
          source: "x",
          target: " x",
          label: "related",
          kind: " relationship",
        },
      ],
    });

    expect(dataset.nodes.map((node) => node.id)).toEqual([" x", "x"]);
    expect(dataset.nodes.find((node) => node.id === "x")?.kind).toBe(" record");
    expect(dataset.edges).toMatchObject([
      {
        id: " relationship",
        source: "x",
        target: " x",
        kind: " relationship",
      },
    ]);
  });

  it("projects a schema proposal, grants, parents, and validator errors", () => {
    const graph = schemaProposalToGraph(
      {
        reused_schemas: [
          {
            id: "Person",
            reason: "Canonical entity",
            extended_properties: {},
            data_scope: "user_context",
          },
        ],
        new_schemas: [
          {
            id: "Release",
            name: "Release",
            description: "A software release",
            properties: { title: "string" },
            subclass_of: "WorkItem",
            ontology_iri: "urn:ambient:ontology:Release",
            equivalent_to: [],
            data_scope: "user_context",
          },
        ],
        capabilities: [
          { id: "graph.query", scope: { entities: ["Person", "Missing"] } },
          { id: "network.fetch", scope: { domains: ["example.com"] } },
        ],
      },
      ["graph.query references entities not present in the schema proposal: Missing"],
    );

    expect(graph.nodes.some((node) => node.kind === "schema-reused" && node.label === "Person")).toBe(true);
    expect(graph.nodes.some((node) => node.kind === "schema-new" && node.label === "Release")).toBe(true);
    expect(graph.nodes.some((node) =>
      node.kind === "schema-parent"
      && node.label === "WorkItem"
      && node.status === "unknown",
    )).toBe(true);
    expect(graph.nodes.some((node) => node.kind === "validation-error" && node.status === "error")).toBe(true);
    expect(graph.edges.map((edge) => edge.label)).toEqual(
      expect.arrayContaining(["subclass_of", "graph.query", "capability scope"]),
    );
  });

  it("localizes schema adapter copy without changing source values or graph identity", () => {
    const proposal = {
      reused_schemas: [
        {
          id: "",
          reason: "",
          extended_properties: {},
          data_scope: "user_context" as const,
        },
      ],
      new_schemas: [
        {
          id: "Release",
          name: "",
          description: "",
          properties: { title: "string" },
          subclass_of: "WorkItem",
          ontology_iri: "urn:ambient:ontology:Release",
          equivalent_to: [],
          data_scope: "user_context" as const,
        },
      ],
      capabilities: [
        { id: "graph.query", scope: { entities: ["Missing"] } },
      ],
    };
    const diagnostic = "后端诊断 business-value-42";
    const english = schemaProposalToGraph(proposal, [diagnostic]);
    const chinese = schemaProposalToGraph(proposal, [diagnostic], "zh");

    expect(chinese.metadata.title).toBe("Schema 与能力提案");
    expect(chinese.nodes.find((node) => node.kind === "schema-reused")).toMatchObject({
      label: "未命名复用实体",
      summary: "复用规范本体实体。",
      badges: ["复用"],
      details: expect.objectContaining({ "实体 ID": "" }),
    });
    expect(chinese.nodes.find((node) => node.id.startsWith("schema:parent:"))).toMatchObject({
      label: "WorkItem",
      summary: "此父实体不在当前提案中；仍以后端校验为准。",
      badges: ["父实体 · 待验证"],
    });
    expect(chinese.nodes.find((node) => node.kind === "schema-new")).toMatchObject({
      label: "Release",
      summary: "新的用户上下文本体实体。",
      badges: ["新建"],
      details: expect.objectContaining({
        "实体 ID": "Release",
        "属性": { title: "string" },
      }),
    });
    expect(chinese.nodes.find((node) => node.kind === "validation-error")).toMatchObject({
      label: "依赖错误",
      summary: diagnostic,
      badges: ["阻止批准"],
      details: { "诊断": diagnostic },
    });
    expect(chinese.nodes.find((node) => node.kind === "graph-grant")).toMatchObject({
      label: "graph.query",
      summary: "限定实体范围的 Graph 授权。",
      badges: ["Graph 授权"],
    });
    expect(chinese.nodes.find((node) => node.id.startsWith("schema:missing:"))).toMatchObject({
      label: "Missing",
      badges: ["悬空授权"],
    });
    expect(chinese.edges.map((edge) => edge.label)).toEqual(
      expect.arrayContaining(["subclass_of", "graph.query", "阻止批准"]),
    );
    expect(chinese.nodes.map((node) => node.id)).toEqual(english.nodes.map((node) => node.id));
    expect(chinese.edges.map((edge) => edge.id)).toEqual(english.edges.map((edge) => edge.id));
    expect(JSON.stringify(chinese)).toContain(diagnostic);
    expect(JSON.stringify(chinese)).toContain("urn:ambient:ontology:Release");
  });

  it("uses an explicit versioned workflow descriptor and overlays run/checkpoint/error state", () => {
    expect(DURABLE_WORKFLOW_DESCRIPTOR.version).toBe(1);
    expect(DURABLE_WORKFLOW_DESCRIPTOR.nodes.some((node) => node.id === "wait_schema")).toBe(true);
    expect(DURABLE_WORKFLOW_DESCRIPTOR.edges.some((edge) => edge.kind === "rework")).toBe(true);

    const graph = workflowToGraph({
      id: "run-1",
      workflow_type: "widget_create",
      workflow_version: 3,
      status: "failed",
      state: { phase: "future_phase" },
      checkpoint: { last_step: "wait_schema", state: { phase: "future_phase" } },
      steps: [
        { step_key: "plan", status: "succeeded", attempt: 1 },
        { step_key: "wait_schema", status: "waiting_user", attempt: 2 },
      ],
      events: [
        {
          sequence: 2,
          step_id: "future_phase",
          type: "step_failed",
          attempt: 3,
          payload: { status: "failed", error: "Future phase failed" },
        },
      ],
      error: { type: "WorkflowError", message: "Future phase failed" },
    });

    expect(graph.nodes.find((node) => node.id === "workflow:plan")?.status).toBe("succeeded");
    expect(graph.nodes.find((node) => node.id === "workflow:wait_schema")).toMatchObject({
      status: "waiting",
      badges: expect.arrayContaining(["Attempt 2"]),
    });
    expect(graph.nodes.find((node) => node.id === "workflow:future_phase")).toMatchObject({
      kind: "workflow-unknown",
      status: "failed",
    });
    expect(JSON.stringify(graph)).toContain("Future phase failed");
    expect(graph.metadata.blindSpots).toEqual(expect.arrayContaining([expect.stringMatching(/event window/i)]));
    expect(graph.metadata).toMatchObject({
      workflowType: "widget_create",
      workflowVersion: 3,
      descriptorVersion: 1,
      descriptorWorkflowVersion: 2,
      workflowVersionMismatch: true,
    });
    expect(graph.metadata.limitations).toEqual(
      expect.arrayContaining([expect.stringMatching(/version 3.*version 2/i)]),
    );
  });

  it("localizes workflow adapter copy while retaining Run evidence and stable ids", () => {
    const run = {
      workflow_type: "customer_workflow",
      workflow_version: 3,
      status: "failed",
      state: { phase: "wait_schema" },
      checkpoint: { last_step: "wait_schema", attempt: 2 },
      steps: [
        {
          step_key: "wait_schema",
          status: "waiting_user",
          attempt: 2,
          summary: "业务摘要 keep verbatim",
          result: { provider: "自定义提供商" },
          error: "业务错误 keep verbatim",
        },
      ],
      events: [
        {
          sequence: 4,
          step_id: "wait_schema",
          type: "custom_event_name",
          attempt: 2,
          payload: {},
        },
      ],
    };
    const english = workflowToGraph(run);
    const chinese = workflowToGraph(run, "zh");
    const waitSchema = chinese.nodes.find((node) => node.id === "workflow:wait_schema");

    expect(chinese.metadata.title).toBe("Agent 工作流运行");
    expect(chinese.nodes.find((node) => node.id === "workflow:route")?.label).toBe("路由意图");
    expect(waitSchema).toMatchObject({
      label: "Schema 批准",
      summary: "业务摘要 keep verbatim",
      badges: expect.arrayContaining(["等待", "第 2 次尝试", "检查点", "当前阶段"]),
      action: expect.objectContaining({ label: "打开源码" }),
      details: expect.objectContaining({
        "职责": expect.any(String),
        "源文件": "backend/agent/durable_workflow.py",
        "执行状态": "失败",
        "最新事件": "custom_event_name",
        "结果": { provider: "自定义提供商" },
        "错误": "业务错误 keep verbatim",
      }),
    });
    expect(chinese.edges.map((edge) => edge.label)).toEqual(
      expect.arrayContaining(["澄清意图", "批准", "返工计划"]),
    );
    expect(chinese.metadata.blindSpots).toEqual([
      "保留的事件窗口或旧版 Run 可能不完整；缺少事件不能证明某个阶段未执行。",
    ]);
    expect(chinese.metadata.limitations?.[0]).toContain("Run 工作流版本 3");
    expect(chinese.metadata.limitations?.[0]).toContain("描述符工作流版本 2");
    expect(chinese.metadata.description).toContain("customer_workflow");
    expect(chinese.nodes.map((node) => node.id)).toEqual(english.nodes.map((node) => node.id));
    expect(chinese.edges.map((edge) => edge.id)).toEqual(english.edges.map((edge) => edge.id));

    expect(workflowToGraph({}, "zh").metadata.description).toContain("未知");
  });

  it("maps canonical run-service step_committed outcomes to visualization statuses", () => {
    const graph = workflowToGraph({
      workflow_type: "widget_create",
      events: [
        {
          sequence: 11,
          step_id: "plan",
          attempt: 1,
          type: "step_committed",
          payload: {
            step_key: "plan",
            outcome: { kind: "continue", next_phase: "wait_plan" },
          },
        },
        {
          sequence: 12,
          step_id: "wait_plan",
          attempt: 1,
          type: "step_committed",
          payload: {
            step_key: "wait_plan",
            outcome: { kind: "wait", interaction_id: "plan-approval" },
          },
        },
        {
          sequence: 13,
          step_id: "verify",
          attempt: 2,
          type: "step_committed",
          payload: {
            step_key: "verify",
            outcome: { kind: "failed", error_code: "verification_failed" },
          },
        },
        {
          sequence: 14,
          step_id: "stage_code",
          attempt: 2,
          type: "step_committed",
          payload: {
            step_key: "stage_code",
            outcome: { kind: "cancelled" },
          },
        },
      ],
    });

    expect(graph.nodes.find((node) => node.id === "workflow:plan")?.status).toBe("succeeded");
    expect(graph.nodes.find((node) => node.id === "workflow:wait_plan")?.status).toBe("waiting");
    expect(graph.nodes.find((node) => node.id === "workflow:verify")?.status).toBe("failed");
    expect(graph.nodes.find((node) => node.id === "workflow:stage_code")?.status).toBe("cancelled");
  });

  it("keeps needs-attention distinct from an ordinary user wait", () => {
    const graph = workflowToGraph({
      workflow_type: "widget_create",
      workflow_version: 2,
      status: "needs_attention",
      state: { phase: "verify", workflow_version: 2 },
    });

    expect(graph.nodes.find((node) => node.id === "workflow:verify")?.status).toBe("needs_attention");
    expect(graph.nodes.find((node) => node.id === "workflow:needs_attention")?.status).toBe("needs_attention");
  });

  it("normalizes data-map metadata while dropping forbidden raw payload fields", () => {
    const graph = dataMapToGraph({
      version: 1,
      title: "Privacy data map",
      nodes: [
        {
          id: "local",
          label: "Local category",
          kind: "source",
          status: "observed",
          details: {
            category: "user_context",
            count: 2,
            prompt: "secret prompt",
            tool_arguments: { token: "secret" },
            scope: {
              sources: {
                private: {
                  base_url: "https://secret-scope.example",
                },
              },
            },
          },
        },
      ],
      edges: [],
      metadata: {
        evidence_count: 2,
        coverage: ["LLM audit metadata"],
        blind_spots: ["Uninstrumented local flows"],
      },
    });

    expect(graph.metadata).toMatchObject({
      evidenceCount: 2,
      coverage: ["LLM audit metadata"],
      blindSpots: ["Uninstrumented local flows"],
    });
    expect(JSON.stringify(graph)).not.toContain("secret prompt");
    expect(JSON.stringify(graph)).not.toContain("tool_arguments");
    expect(JSON.stringify(graph)).not.toContain("secret-scope");
    expect(JSON.stringify(graph)).not.toContain("base_url");
    expect(JSON.stringify(graph)).not.toContain('"scope"');
  });

  it("localizes known privacy-map copy and keeps provider, App, and category values verbatim", () => {
    const payload = {
      version: 1,
      title: "Privacy data map",
      description: "Observed, declared, and unknown data-flow metadata without raw payloads.",
      nodes: [
        {
          id: "data-source:local-runtime-context",
          label: "Local runtime context",
          kind: "data_source",
          status: "existing",
          summary: "Local context category; raw values are intentionally omitted.",
          details: { source_category: "local_runtime_context" },
          badges: ["local"],
        },
        {
          id: "provider:azure-tokyo",
          label: "Azure OpenAI 东京",
          kind: "provider",
          status: "observed",
          summary: "Provider observed in retained Audit metadata.",
          details: {
            semantics: "observed",
            target_category: "provider:Azure OpenAI 东京",
          },
          badges: ["observed"],
        },
        {
          id: "provider:custom-summary",
          label: "Custom Provider",
          kind: "provider",
          status: "observed",
          summary: "客户定义的 provider 摘要",
          badges: ["observed"],
        },
        {
          id: "app:weather",
          label: "Weather 工具",
          kind: "app",
          status: "declared",
          summary: "Current App Manifest declaration.",
          details: { semantics: "declared", app_id: "weather" },
          badges: ["declared"],
        },
        {
          id: "unknown:uninstrumented",
          label: "Uninstrumented flow",
          kind: "unknown",
          status: "unknown",
          summary: "A possible flow without retained Audit instrumentation.",
          details: {
            semantics: "unknown",
            reason: "A possible flow without retained Audit instrumentation.",
          },
          badges: ["unknown"],
        },
      ],
      edges: [
        {
          id: "observed:route:azure-tokyo",
          source: "data-source:local-runtime-context",
          target: "provider:azure-tokyo",
          label: "route → Azure OpenAI 东京",
          kind: "data_flow",
          status: "observed",
          details: { stage: "route", provider: "Azure OpenAI 东京" },
        },
        {
          id: "declared-capability:weather:network-fetch",
          source: "app:weather",
          target: "provider:azure-tokyo",
          label: "declared capability",
          kind: "data_flow",
          status: "declared",
        },
        {
          id: "unknown:uninstrumented",
          source: "data-source:local-runtime-context",
          target: "unknown:uninstrumented",
          label: "coverage unknown",
          kind: "data_flow",
          status: "unknown",
          details: {
            semantics: "unknown",
            reason: "A possible flow without retained Audit instrumentation.",
          },
        },
      ],
      metadata: {
        coverage: {
          evidence_count: 2,
          declared_app_count: 1,
          observed_flow_count: 1,
          declared_flow_count: 1,
          observed_stages: ["route-business-stage"],
          instrumentation: [
            "retained_llm_provider_call_metadata",
            "current_app_manifest_declarations",
          ],
        },
        semantics: {
          observed: "Aggregated evidence in the retained Audit metadata window.",
          declared: "Potential flow inferred from a current App Manifest only.",
          unknown: "Possible flow outside retention or instrumentation coverage.",
        },
        blind_spots: [
          "Data flows outside the retained Audit Log window are not observable.",
          "Local or third-party operations without Audit instrumentation remain unknown.",
          "Manifest declarations describe potential access, not runtime grants or observed use.",
        ],
        limitations: [
          "This map is a request-time projection and does not extend Audit Log retention.",
          "Observed flows are aggregated only from retained provider, stage, and timestamp metadata.",
          "The map is not an authorization, compliance, or proof-of-absence decision.",
        ],
      },
    };
    const english = dataMapToGraph(payload);
    const chinese = dataMapToGraph(payload, "zh");

    expect(chinese.metadata).toMatchObject({
      title: "隐私数据地图",
      description: "不含原始载荷的已观察、已声明及未知数据流元数据。",
      blindSpots: [
        "无法观察审计日志保留窗口之外的数据流。",
        "缺少审计插桩的本地或第三方操作仍属于未知范围。",
        "Manifest 声明描述潜在访问，并不代表运行时授权或已观察到的使用。",
      ],
      limitations: [
        "此地图是请求时投影，不会延长审计日志的保留期限。",
        "已观察的数据流仅由保留的 provider、stage 和时间戳元数据聚合。",
        "此地图不作授权、合规或不存在数据流的证明。",
      ],
      coverage: {
        "证据数": 2,
        "已声明 App 数": 1,
        "已观察数据流数": 1,
        "已声明数据流数": 1,
        "已观察阶段": ["route-business-stage"],
        "插桩范围": ["保留的 LLM provider 调用元数据", "当前 App Manifest 声明"],
      },
      semantics: {
        "已观察": "保留的审计元数据窗口中的聚合证据。",
        "已声明": "仅从当前 App Manifest 推断的潜在数据流。",
        "未知": "保留或插桩覆盖之外的可能数据流。",
      },
    });
    expect(chinese.nodes.find((node) => node.id === "data-source:local-runtime-context"))
      .toMatchObject({
        label: "本地运行时上下文",
        summary: "本地上下文类别；有意省略原始值。",
        badges: ["本地"],
        details: { "来源类别": "local_runtime_context" },
      });
    expect(chinese.nodes.find((node) => node.id === "provider:azure-tokyo")).toMatchObject({
      label: "Azure OpenAI 东京",
      summary: "在保留的审计元数据中观察到的 provider。",
      badges: ["已观察"],
      details: expect.objectContaining({ "语义": "已观察" }),
    });
    expect(chinese.nodes.find((node) => node.id === "provider:custom-summary")?.summary)
      .toBe("客户定义的 provider 摘要");
    expect(chinese.nodes.find((node) => node.id === "app:weather")).toMatchObject({
      label: "Weather 工具",
      summary: "当前 App Manifest 声明。",
      badges: ["已声明"],
    });
    expect(chinese.nodes.find((node) => node.id === "unknown:uninstrumented")).toMatchObject({
      label: "未插桩的数据流",
      summary: "可能存在但没有保留审计插桩的数据流。",
      badges: ["未知"],
    });
    expect(chinese.edges.find((edge) => edge.id.startsWith("observed:"))?.label)
      .toBe("route → Azure OpenAI 东京");
    expect(chinese.edges.find((edge) => edge.id.startsWith("declared-capability:"))?.label)
      .toBe("已声明能力");
    expect(chinese.edges.find((edge) => edge.id === "unknown:uninstrumented"))
      .toMatchObject({
        label: "覆盖未知",
        details: {
          "语义": "未知",
          "原因": "可能存在但没有保留审计插桩的数据流。",
        },
      });
    expect(chinese.nodes.map((node) => node.id)).toEqual(english.nodes.map((node) => node.id));
    expect(chinese.edges.map((edge) => edge.id)).toEqual(english.edges.map((edge) => edge.id));
  });

  it("lays out the same graph deterministically regardless of input order", () => {
    const first = deterministicGraphLayout(explorationDataset, "LR");
    const second = deterministicGraphLayout(
      {
        ...explorationDataset,
        nodes: [...explorationDataset.nodes].reverse(),
        edges: [...explorationDataset.edges].reverse(),
      },
      "LR",
    );

    const positions = (dataset: GraphDataset) =>
      Object.fromEntries(dataset.nodes.map((node) => [node.id, node.position]));
    expect(positions(first)).toEqual(positions(second));
    for (const node of first.nodes) {
      expect(Number.isFinite(node.position?.x)).toBe(true);
      expect(Number.isFinite(node.position?.y)).toBe(true);
    }
    expect(positions(deterministicGraphLayout(explorationDataset, "TB"))).not.toEqual(positions(first));
  });

  it("spreads the cyclic durable workflow across stable, bounded layers", () => {
    const workflow = workflowToGraph();
    const horizontal = deterministicGraphLayout(workflow, "LR");
    const vertical = deterministicGraphLayout(workflow, "TB");
    const reordered = deterministicGraphLayout(
      {
        ...workflow,
        nodes: [...workflow.nodes].reverse(),
        edges: [...workflow.edges].reverse(),
      },
      "LR",
    );
    const positions = (dataset: GraphDataset) =>
      Object.fromEntries(dataset.nodes.map((node) => [node.id, node.position]));
    const axisValues = (dataset: GraphDataset, axis: "x" | "y") =>
      dataset.nodes.map((node) => node.position?.[axis] ?? 0);
    const span = (values: number[]) => Math.max(...values) - Math.min(...values);
    const horizontalPositions = positions(horizontal);

    expect(positions(reordered)).toEqual(horizontalPositions);
    expect(new Set(axisValues(horizontal, "x")).size).toBeGreaterThanOrEqual(5);
    expect(new Set(axisValues(vertical, "y")).size).toBeGreaterThanOrEqual(5);
    expect(horizontalPositions["workflow:route"]?.x).toBeLessThan(
      horizontalPositions["workflow:wait_schema"]?.x ?? 0,
    );
    expect(horizontalPositions["workflow:wait_schema"]?.x).toBeLessThan(
      horizontalPositions["workflow:promote"]?.x ?? 0,
    );
    expect(span(axisValues(horizontal, "x"))).toBeGreaterThan(800);
    expect(span(axisValues(horizontal, "x"))).toBeLessThan(5_000);
    expect(span(axisValues(vertical, "y"))).toBeGreaterThan(500);
    expect(span(axisValues(vertical, "y"))).toBeLessThan(5_000);
  });

  it("chooses stable roots for rootless cyclic components without dropping edges", () => {
    const cyclic: GraphDataset = {
      version: 1,
      nodes: [
        { id: "root", label: "Root", kind: "phase" },
        { id: "reachable-a", label: "Reachable A", kind: "phase" },
        { id: "reachable-b", label: "Reachable B", kind: "phase" },
        { id: "cycle-a", label: "Cycle A", kind: "phase" },
        { id: "cycle-b", label: "Cycle B", kind: "phase" },
      ],
      edges: [
        { id: "root-a", source: "root", target: "reachable-a", kind: "transition" },
        { id: "a-b", source: "reachable-a", target: "reachable-b", kind: "transition" },
        { id: "b-a", source: "reachable-b", target: "reachable-a", kind: "rework" },
        { id: "cycle-a-b", source: "cycle-a", target: "cycle-b", kind: "transition" },
        { id: "cycle-b-a", source: "cycle-b", target: "cycle-a", kind: "rework" },
      ],
      metadata: { title: "Cyclic components" },
    };
    const laidOut = deterministicGraphLayout(cyclic, "LR");
    const byId = Object.fromEntries(laidOut.nodes.map((node) => [node.id, node.position]));

    expect(byId["reachable-a"]?.x).toBeGreaterThan(byId.root?.x ?? 0);
    expect(byId["reachable-b"]?.x).toBeGreaterThan(byId["reachable-a"]?.x ?? 0);
    expect(byId["cycle-b"]?.x).toBeGreaterThan(byId["cycle-a"]?.x ?? 0);
    expect((byId["reachable-a"]?.x ?? 0) - (byId.root?.x ?? 0)).toBe(286);
    expect((byId["cycle-b"]?.x ?? 0) - (byId["cycle-a"]?.x ?? 0)).toBe(286);
    expect(laidOut.edges).toEqual(cyclic.edges);
  });

  it("keeps every node in a broad topology layer on the same primary axis", () => {
    const broadStar: GraphDataset = {
      version: 1,
      nodes: [
        { id: "root", label: "Root", kind: "ontology" },
        ...Array.from({ length: 12 }, (_, index) => ({
          id: `child:${index.toString().padStart(2, "0")}`,
          label: `Child ${index}`,
          kind: "ontology",
        })),
      ],
      edges: Array.from({ length: 12 }, (_, index) => ({
        id: `child-root:${index}`,
        source: `child:${index.toString().padStart(2, "0")}`,
        target: "root",
        kind: "inheritance",
      })),
      metadata: { title: "Broad ontology layer" },
    };

    const horizontal = deterministicGraphLayout(broadStar, "LR");
    const vertical = deterministicGraphLayout(broadStar, "TB");
    const byId = Object.fromEntries(horizontal.nodes.map((node) => [node.id, node.position]));
    const horizontalChildren = horizontal.nodes
      .filter((node) => node.id.startsWith("child:"))
      .map((node) => node.position ?? { x: 0, y: 0 });
    const verticalChildren = vertical.nodes
      .filter((node) => node.id.startsWith("child:"))
      .map((node) => node.position ?? { x: 0, y: 0 });

    expect(new Set(horizontalChildren.map(({ x }) => x))).toEqual(new Set([0]));
    expect(new Set(verticalChildren.map(({ y }) => y))).toEqual(new Set([0]));
    const verticalCrossPositions = verticalChildren
      .map(({ x }) => x)
      .sort((left, right) => left - right);
    expect(
      verticalCrossPositions
        .slice(1)
        .every((position, index) => position - verticalCrossPositions[index] >= 210),
    ).toBe(true);
    expect(byId.root?.x).toBeGreaterThan(horizontalChildren[0].x);
    expect(
      Math.max(...horizontal.nodes.map((node) => node.position?.y ?? 0))
      - Math.min(...horizontal.nodes.map((node) => node.position?.y ?? 0)),
    ).toBeLessThan(5_000);
  });

  it("places weakly connected components and isolated nodes in separate blocks", () => {
    const disconnected: GraphDataset = {
      version: 1,
      nodes: [
        { id: "a-root", label: "A root", kind: "phase" },
        { id: "a-child", label: "A child", kind: "phase" },
        { id: "c-child", label: "C child", kind: "phase" },
        { id: "b-root", label: "B root", kind: "phase" },
        { id: "b-child", label: "B child", kind: "phase" },
        { id: "d-child", label: "D child", kind: "phase" },
        { id: "isolated", label: "Isolated", kind: "phase" },
      ],
      edges: [
        { id: "a-a", source: "a-root", target: "a-child", kind: "transition" },
        { id: "a-c", source: "a-root", target: "c-child", kind: "transition" },
        { id: "b-b", source: "b-root", target: "b-child", kind: "transition" },
        { id: "b-d", source: "b-root", target: "d-child", kind: "transition" },
      ],
      metadata: { title: "Disconnected components" },
    };
    const components = [
      ["a-root", "a-child", "c-child"],
      ["b-root", "b-child", "d-child"],
      ["isolated"],
    ];
    const boundsFor = (dataset: GraphDataset, ids: string[]) => {
      const positions = dataset.nodes
        .filter((node) => ids.includes(node.id))
        .map((node) => node.position ?? { x: 0, y: 0 });
      return {
        minX: Math.min(...positions.map(({ x }) => x)),
        maxX: Math.max(...positions.map(({ x }) => x)),
        minY: Math.min(...positions.map(({ y }) => y)),
        maxY: Math.max(...positions.map(({ y }) => y)),
      };
    };
    const blocksAreSeparate = (
      left: ReturnType<typeof boundsFor>,
      right: ReturnType<typeof boundsFor>,
    ) =>
      left.maxX + 210 < right.minX
      || right.maxX + 210 < left.minX
      || left.maxY + 120 < right.minY
      || right.maxY + 120 < left.minY;

    for (const direction of ["LR", "TB"] as const) {
      const laidOut = deterministicGraphLayout(disconnected, direction);
      const reversed = deterministicGraphLayout(
        {
          ...disconnected,
          nodes: [...disconnected.nodes].reverse(),
          edges: [...disconnected.edges].reverse(),
        },
        direction,
      );
      const positions = (dataset: GraphDataset) =>
        Object.fromEntries(dataset.nodes.map((node) => [node.id, node.position]));
      const blocks = components.map((ids) => boundsFor(laidOut, ids));

      expect(positions(reversed)).toEqual(positions(laidOut));
      for (let left = 0; left < blocks.length; left += 1) {
        for (let right = left + 1; right < blocks.length; right += 1) {
          expect(blocksAreSeparate(blocks[left], blocks[right])).toBe(true);
        }
      }
    }
  });

  it("lays out large edgeless snapshots as bounded deterministic grids", () => {
    const edgeless: GraphDataset = {
      version: 1,
      nodes: Array.from({ length: 250 }, (_, index) => ({
        id: `isolated:${index.toString().padStart(3, "0")}`,
        label: `Isolated ${index}`,
        kind: "record",
      })),
      edges: [],
      metadata: { title: "Large edgeless snapshot" },
    };
    const horizontal = deterministicGraphLayout(edgeless, "LR");
    const vertical = deterministicGraphLayout(edgeless, "TB");
    const reversed = deterministicGraphLayout(
      { ...edgeless, nodes: [...edgeless.nodes].reverse() },
      "LR",
    );
    const positions = (dataset: GraphDataset) =>
      Object.fromEntries(dataset.nodes.map((node) => [node.id, node.position]));
    const axisValues = (dataset: GraphDataset, axis: "x" | "y") =>
      dataset.nodes.map((node) => node.position?.[axis] ?? 0);
    const span = (values: number[]) => Math.max(...values) - Math.min(...values);

    expect(positions(reversed)).toEqual(positions(horizontal));
    expect(new Set(axisValues(horizontal, "x")).size).toBeGreaterThan(1);
    expect(new Set(axisValues(horizontal, "y")).size).toBeGreaterThan(1);
    expect(new Set(axisValues(vertical, "x")).size).toBeGreaterThan(1);
    expect(new Set(axisValues(vertical, "y")).size).toBeGreaterThan(1);
    expect(span(axisValues(horizontal, "x"))).toBeLessThan(5_000);
    expect(span(axisValues(horizontal, "y"))).toBeLessThan(5_000);
    expect(span(axisValues(vertical, "x"))).toBeLessThan(5_000);
    expect(span(axisValues(vertical, "y"))).toBeLessThan(5_000);
    expect(positions(vertical)).not.toEqual(positions(horizontal));
  });
});
