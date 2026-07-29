import React from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

    expect(byId.root?.x).toBe(0);
    expect(byId["reachable-a"]?.x).toBeGreaterThan(byId.root?.x ?? 0);
    expect(byId["reachable-b"]?.x).toBeGreaterThan(byId["reachable-a"]?.x ?? 0);
    expect(byId["cycle-a"]?.x).toBe(0);
    expect(byId["cycle-b"]?.x).toBeGreaterThan(byId["cycle-a"]?.x ?? 0);
    expect(laidOut.edges).toEqual(cyclic.edges);
  });

  it("wraps broad connected layers so default fit keeps nodes readable", () => {
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
    const byId = Object.fromEntries(horizontal.nodes.map((node) => [node.id, node.position]));
    const childPositions = horizontal.nodes
      .filter((node) => node.id.startsWith("child:"))
      .map((node) => node.position ?? { x: 0, y: 0 });

    expect(new Set(childPositions.map(({ x }) => x)).size).toBe(3);
    expect(Math.max(...childPositions.map(({ y }) => y))
      - Math.min(...childPositions.map(({ y }) => y))).toBeLessThanOrEqual(592);
    expect(byId.root?.x).toBeGreaterThan(Math.max(...childPositions.map(({ x }) => x)));
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
