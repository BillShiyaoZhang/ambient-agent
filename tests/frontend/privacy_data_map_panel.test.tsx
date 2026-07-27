import React from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrivacyDataMapPanel } from "../../frontend/src/components/PrivacyDataMapPanel";
import {
  formatPrivacyDataMapRange,
  formatPrivacyDataMapSelectionAnnouncement,
  formatPrivacyDataMapUpdated,
  formatPrivacyDataMapWarning,
  getPrivacyDataMapMessages,
} from "../../frontend/src/services/i18n";

const privacyDataMapCss = readFileSync(
  resolve(process.cwd(), "src/components/PrivacyDataMapPanel.css"),
  "utf8"
);
const frontendCss = Array.from(
  new Set([
    readFileSync(resolve(process.cwd(), "src/index.css"), "utf8"),
    privacyDataMapCss,
  ])
).join("\n");

const modelNodeId = `model_target:${"a".repeat(64)}`;

const validResponse = {
  contract_version: 1,
  generated_at: "2026-07-20T08:30:00Z",
  scope: {
    kind: "workspace",
    observed_window: {
      from: "2026-07-20T08:00:00Z",
      to: "2026-07-20T08:30:00Z",
    },
  },
  source_health: {
    status: "healthy",
    valid_audit_records: 2,
    malformed_json_records: 0,
    structurally_invalid_audit_records: 0,
    projection_ineligible_audit_records: 0,
    oversized_audit_lines: 0,
    invalid_app_declarations: 0,
    unsafe_schema_ids: 0,
    missing_schema_references: 0,
  },
  coverage: {
    status: "partial",
    channels: [
      { id: "llm", observation: "partial" },
      { id: "mcp", observation: "not_instrumented" },
      { id: "http_agent", observation: "not_instrumented" },
      { id: "coding_agent_acp", observation: "not_instrumented" },
      { id: "provider_management", observation: "not_instrumented" },
      { id: "isolated_widget_runtime", observation: "not_instrumented" },
    ],
  },
  nodes: [
    { id: "platform:ambient-agent", kind: "platform", label: "Ambient Agent" },
    {
      id: modelNodeId,
      kind: "recorded_model_target",
      label: "OpenAI · gpt-example",
      location: "unknown",
    },
    { id: "app:morning-planner", kind: "app", label: "morning-planner" },
    { id: "schema:Task", kind: "schema", label: "Task" },
  ],
  observed_flows: [
    {
      id: `flow:${"b".repeat(64)}`,
      evidence: "observed",
      source_node_id: "platform:ambient-agent",
      destination_node_id: modelNodeId,
      stage: "chat",
      count: 2,
      first_observed_at: "2026-07-20T08:00:00Z",
      last_observed_at: "2026-07-20T08:30:00Z",
    },
  ],
  declared_associations: [
    {
      id: `declaration:${"c".repeat(64)}`,
      evidence: "declared",
      app_node_id: "app:morning-planner",
      schema_node_id: "schema:Task",
    },
  ],
  warnings: [],
};

function response(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
}

function largeValidResponse(itemCount = 30) {
  const observedAt = "2026-07-20T08:00:00Z";
  const modelNodes = Array.from({ length: itemCount }, (_, index) => ({
    id: `model_target:${index.toString(16).padStart(64, "0")}`,
    kind: "recorded_model_target",
    label: `Provider ${index} · model-${index}`,
    location: "unknown",
  }));
  const appNodes = Array.from({ length: itemCount }, (_, index) => ({
    id: `app:app-${index}`,
    kind: "app",
    label: `app-${index}`,
  }));
  const schemaNodes = Array.from({ length: itemCount }, (_, index) => ({
    id: `schema:Schema${index}`,
    kind: "schema",
    label: `Schema${index}`,
  }));
  const observedFlows = modelNodes.map((node, index) => ({
    id: `flow:${(index + 1_000).toString(16).padStart(64, "0")}`,
    evidence: "observed",
    source_node_id: "platform:ambient-agent",
    destination_node_id: node.id,
    stage: "chat",
    count: 1,
    first_observed_at: observedAt,
    last_observed_at: observedAt,
  }));
  const declaredAssociations = appNodes.map((app, index) => ({
    id: `declaration:${(index + 2_000).toString(16).padStart(64, "0")}`,
    evidence: "declared",
    app_node_id: app.id,
    schema_node_id: schemaNodes[index].id,
  }));

  return {
    ...validResponse,
    scope: {
      kind: "workspace",
      observed_window: { from: observedAt, to: observedAt },
    },
    source_health: {
      ...validResponse.source_health,
      valid_audit_records: itemCount,
    },
    nodes: [
      { id: "platform:ambient-agent", kind: "platform", label: "Ambient Agent" },
      ...modelNodes,
      ...appNodes,
      ...schemaNodes,
    ],
    observed_flows: observedFlows,
    declared_associations: declaredAssociations,
  };
}

function expectCoverageChannel(
  coverage: HTMLElement,
  label: string,
  observation: string
) {
  const row = within(coverage).getByText(label).closest("li");
  if (!row) throw new Error(`Coverage row was not found for ${label}.`);
  expect(row.textContent).toContain(observation);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PrivacyDataMapPanel", () => {
  it("provides Privacy Map copy through the shared i18n service", () => {
    const english = getPrivacyDataMapMessages("en");
    const chinese = getPrivacyDataMapMessages("zh");

    expect(english.copy.title).toBe("Privacy Map");
    expect(chinese.copy.title).toBe("隐私数据地图");
    expect(english.copy.unknown).toBe("Unknown");
    expect(english.copy.blindSpotsTitle).toBe("Blind spots");
    expect(chinese.copy.unknown).toBe("未知");
    expect(chinese.copy.blindSpotsTitle).toBe("盲区");
    expect(chinese.coverageChannels.llm).toBe("LLM 调用");
    expect(english.stages.mutation).toBe("Graph mutation");
    expect(english.coverageObservations.not_instrumented).toBe("not instrumented");
    expect(formatPrivacyDataMapWarning("malformed_json_records", 1, "en")).toBe(
      "1 malformed audit record was skipped."
    );
    expect(formatPrivacyDataMapRange(25, 30, 30, "en")).toBe("Showing 25–30 of 30.");
    expect(formatPrivacyDataMapRange(25, 30, 30, "zh")).toBe("显示第 25–30 项，共 30 项。");
    expect(formatPrivacyDataMapUpdated(4, 1, 2, "en")).toBe(
      "Privacy map updated: 4 nodes, 1 observed transfer, 2 declared associations."
    );
    expect(formatPrivacyDataMapUpdated(4, 1, 2, "zh")).toBe(
      "隐私数据地图已更新：4 个节点、1 条已观测传输、2 条声明关联。"
    );
    expect(formatPrivacyDataMapSelectionAnnouncement("Task", "en")).toBe(
      "Selected Task. Details updated."
    );
    expect(formatPrivacyDataMapSelectionAnnouncement("Task", "zh")).toBe(
      "已选择 Task，详情已更新。"
    );
  });

  it("applies the mobile target-size and safe-area contract across the full-screen breakpoint", () => {
    expect(privacyDataMapCss).toMatch(/@media\s*\(max-width:\s*719px\)/);
    expect(privacyDataMapCss).toMatch(
      /@media\s*\(max-width:\s*719px\)[\s\S]*?\.privacy-map-panel\s*\{[\s\S]*?width:\s*100%/
    );
    expect(privacyDataMapCss).toMatch(
      /\.privacy-map-header-actions \.system-icon-button[\s\S]*?min-width:\s*44px[\s\S]*?min-height:\s*44px/
    );
    expect(privacyDataMapCss).toMatch(
      /\.privacy-map-header\s*\{[\s\S]*?safe-area-inset-top[\s\S]*?safe-area-inset-right[\s\S]*?safe-area-inset-left/
    );
    expect(privacyDataMapCss).toMatch(
      /\.privacy-map-body\s*\{[\s\S]*?safe-area-inset-right[\s\S]*?safe-area-inset-bottom[\s\S]*?safe-area-inset-left/
    );
    expect(privacyDataMapCss).toMatch(
      /@media\s*\(max-width:\s*420px\)[\s\S]*?grid-template-columns:\s*1fr/
    );
    expect(privacyDataMapCss).toMatch(
      /@media\s*\(max-width:\s*420px\)[\s\S]*?\.privacy-map-coverage li[\s\S]*?min-height:\s*44px/
    );
    expect(privacyDataMapCss).not.toMatch(/font-size:\s*(?:[0-9]|1[01])px/);
  });

  it("uses semantic theme tokens and disables map animation for reduced motion", () => {
    expect(privacyDataMapCss).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
    const referencedTokens = [
      ...privacyDataMapCss.matchAll(/var\((--[a-z0-9-]+)/gi),
    ].map((match) => match[1]);
    const declaredTokens = new Set(
      [...frontendCss.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((match) => match[1])
    );
    expect(
      [...new Set(referencedTokens)].filter((token) => !declaredTokens.has(token))
    ).toEqual([]);
    expect(privacyDataMapCss).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.is-spinning[\s\S]*?animation:\s*none/
    );
  });

  it("preserves the panel structure while loading and prevents duplicate refreshes", () => {
    const fetchMock = vi.fn(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect(screen.getByRole("heading", { name: "Privacy Map" })).toBeDefined();
    expect(screen.getByRole("status").textContent).toContain("Loading privacy map");
    const sourceHealth = screen.getByText("Source health").closest("article");
    if (!sourceHealth) throw new Error("Source health summary was not found.");
    expect(sourceHealth.textContent).toContain("Checking");
    expect(sourceHealth.textContent).not.toContain("Healthy");
    const refresh = screen.getByRole("button", { name: "Refresh privacy map" });
    expect(refresh.hasAttribute("disabled")).toBe(true);
    fireEvent.click(refresh);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows scope, evidence semantics, declarations, and every coverage blind spot", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(validResponse)));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect((await screen.findAllByText("OpenAI · gpt-example")).length).toBeGreaterThan(0);
    expect(screen.getByText("Observed transfer")).toBeDefined();
    expect(screen.getByText("2 recorded events")).toBeDefined();
    expect(screen.getByText("Declared association")).toBeDefined();
    expect(screen.getAllByText("morning-planner").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Task").length).toBeGreaterThan(0);
    expect(screen.getByText("Workspace")).toBeDefined();
    expect(
      screen.getByText(
        "Privacy map updated: 4 nodes, 1 observed transfer, 1 declared association."
      )
    ).toBeDefined();

    expect(screen.getByRole("region", { name: "Observed transfers" })).toBeDefined();
    expect(screen.getByRole("region", { name: "Declared associations" })).toBeDefined();

    const blindSpots = screen.getByRole("region", { name: "Blind spots" });
    expect(within(blindSpots).getByText("Unknown")).toBeDefined();
    expect(within(blindSpots).getByText("Partial coverage")).toBeDefined();
    expect(
      within(blindSpots).getByText(
        "This map is not a complete record of every data transfer. An unknown path does not mean no transfer occurred."
      )
    ).toBeDefined();
    expectCoverageChannel(blindSpots, "LLM calls", "partially observed");
    expectCoverageChannel(blindSpots, "MCP", "not instrumented");
    expectCoverageChannel(blindSpots, "HTTP Agent", "not instrumented");
    expectCoverageChannel(blindSpots, "Coding Agent / ACP", "not instrumented");
    expectCoverageChannel(blindSpots, "Provider management", "not instrumented");
    expectCoverageChannel(blindSpots, "Isolated Widget Runtime", "not instrumented");
  });

  it("derives the visual and semantic maps from the same selectable contract items", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(validResponse)));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    const visualMap = await screen.findByRole("region", { name: "Visual map" });
    const semanticMap = screen.getByRole("region", { name: "Semantic map" });

    expect(
      within(visualMap).getByRole("button", { name: "Select node Ambient Agent" })
    ).toBeDefined();
    expect(
      within(semanticMap).getByRole("button", { name: "Select node Ambient Agent" })
    ).toBeDefined();
    expect(
      within(visualMap).getByRole("button", {
        name: "Select observed transfer Ambient Agent to OpenAI · gpt-example",
      })
    ).toBeDefined();
    expect(
      within(semanticMap).getByRole("button", {
        name: "Select observed transfer Ambient Agent to OpenAI · gpt-example",
      })
    ).toBeDefined();
    expect(
      within(visualMap).getByRole("button", {
        name: "Select declared association morning-planner and Task",
      })
    ).toBeDefined();
    expect(
      within(semanticMap).getByRole("button", {
        name: "Select declared association morning-planner and Task",
      })
    ).toBeDefined();
  });

  it("keeps a large valid map responsive through bounded visual overview and semantic pages", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(largeValidResponse())));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    const visualMap = await screen.findByRole("region", { name: "Visual map" });
    const semanticMap = screen.getByRole("region", { name: "Semantic map" });
    expect(
      within(visualMap).getByText(
        "Large maps use a bounded visual overview. Browse every contract item in the semantic map."
      )
    ).toBeDefined();
    const visualSelectionControls = within(visualMap).getAllByRole("button", { name: /^Select / });
    const semanticSelectionControls = within(semanticMap).getAllByRole("button", {
      name: /^Select /,
    });
    expect(visualSelectionControls.length).toBeLessThanOrEqual(48);
    expect(semanticSelectionControls.length).toBeLessThanOrEqual(72);
    expect(visualSelectionControls.length + semanticSelectionControls.length).toBeLessThanOrEqual(
      120
    );

    const observed = within(semanticMap).getByRole("region", { name: "Observed transfers" });
    expect(within(observed).getByText("Showing 1–24 of 30.")).toBeDefined();
    fireEvent.click(
      within(observed).getByRole("button", { name: "Next page: Observed transfers" })
    );
    expect(within(observed).getByText("Showing 25–30 of 30.")).toBeDefined();
    expect(
      within(observed).getByRole("button", {
        name: "Select observed transfer Ambient Agent to Provider 29 · model-29",
      })
    ).toBeDefined();

    const declared = within(semanticMap).getByRole("region", { name: "Declared associations" });
    fireEvent.click(
      within(declared).getByRole("button", { name: "Next page: Declared associations" })
    );
    expect(
      within(declared).getByRole("button", {
        name: "Select declared association app-29 and Schema29",
      })
    ).toBeDefined();

    const inventory = within(semanticMap).getByRole("region", { name: "Map inventory" });
    const nextInventory = within(inventory).getByRole("button", {
      name: "Next page: Map inventory",
    });
    fireEvent.click(nextInventory);
    fireEvent.click(nextInventory);
    fireEvent.click(nextInventory);
    expect(within(inventory).getByText("Showing 73–91 of 91.")).toBeDefined();
    expect(
      within(inventory).getByRole("button", { name: "Select node Schema29" })
    ).toBeDefined();
  });

  it("keeps pagination controls focusable at page boundaries and announces the current range", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(largeValidResponse())));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    const semanticMap = await screen.findByRole("region", { name: "Semantic map" });
    const observed = within(semanticMap).getByRole("region", { name: "Observed transfers" });
    const previous = within(observed).getByRole("button", {
      name: "Previous page: Observed transfers",
    });
    const next = within(observed).getByRole("button", {
      name: "Next page: Observed transfers",
    });
    const range = within(observed).getByText("Showing 1–24 of 30.");

    expect(previous.hasAttribute("disabled")).toBe(false);
    expect(previous.getAttribute("aria-disabled")).toBe("true");
    expect(next.getAttribute("aria-disabled")).toBe("false");
    expect(range.getAttribute("aria-live")).toBe("polite");
    expect(range.getAttribute("aria-atomic")).toBe("true");

    next.focus();
    fireEvent.click(next);

    expect(document.activeElement).toBe(next);
    expect(previous.getAttribute("aria-disabled")).toBe("false");
    expect(next.getAttribute("aria-disabled")).toBe("true");
    expect(range.textContent).toBe("Showing 25–30 of 30.");

    fireEvent.click(next);
    expect(range.textContent).toBe("Showing 25–30 of 30.");
  });

  it("preserves selection details while paging and restores its selected state when revisited", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(largeValidResponse())));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    const semanticMap = await screen.findByRole("region", { name: "Semantic map" });
    const observed = within(semanticMap).getByRole("region", { name: "Observed transfers" });
    const selectedName = "Select observed transfer Ambient Agent to Provider 0 · model-0";
    const firstPageControl = within(observed).getByRole("button", { name: selectedName });
    fireEvent.click(firstPageControl);
    expect(firstPageControl.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(
      within(observed).getByRole("button", { name: "Next page: Observed transfers" })
    );
    expect(within(observed).queryByRole("button", { name: selectedName })).toBeNull();
    const details = screen.getByRole("region", { name: "Selection details" });
    expect(within(details).getByText("Ambient Agent → Provider 0 · model-0")).toBeDefined();

    fireEvent.click(
      within(observed).getByRole("button", { name: "Previous page: Observed transfers" })
    );
    expect(
      within(observed).getByRole("button", { name: selectedName }).getAttribute("aria-pressed")
    ).toBe("true");
  });

  it("uses deliberate separators instead of leaking malformed Cyrillic glyphs into map labels", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(validResponse)));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect(
      (await screen.findAllByText("Recorded model target · Location unknown")).length
    ).toBeGreaterThan(0);
    const observedControl = screen.getAllByRole("button", {
      name: "Select observed transfer Ambient Agent to OpenAI · gpt-example",
    })[0];
    fireEvent.click(observedControl);
    expect(
      within(screen.getByRole("region", { name: "Selection details" })).getByText(
        "Ambient Agent → OpenAI · gpt-example"
      )
    ).toBeDefined();

    const declaredControl = screen.getAllByRole("button", {
      name: "Select declared association morning-planner and Task",
    })[0];
    fireEvent.click(declaredControl);
    expect(
      within(screen.getByRole("region", { name: "Selection details" })).getByText(
        "morning-planner · Task"
      )
    ).toBeDefined();
    expect(document.body.textContent).not.toMatch(/[\u0400-\u04ff]/);
  });

  it("synchronizes node selection and details between visual and semantic views", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(validResponse)));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    const nodeControls = await screen.findAllByRole("button", {
      name: "Select node OpenAI · gpt-example",
    });
    expect(nodeControls).toHaveLength(2);
    expect(
      nodeControls.every(
        (control) => control.getAttribute("aria-controls") === "privacy-map-selection-details"
      )
    ).toBe(true);
    nodeControls[0].focus();
    fireEvent.keyDown(nodeControls[0], { key: "Enter" });
    fireEvent.click(nodeControls[0]);

    expect(nodeControls.every((control) => control.getAttribute("aria-pressed") === "true")).toBe(
      true
    );
    const details = screen.getByRole("region", { name: "Selection details" });
    expect(details.id).toBe("privacy-map-selection-details");
    expect(within(details).getByText("OpenAI · gpt-example")).toBeDefined();
    expect(within(details).getByText("Recorded model target")).toBeDefined();
    expect(within(details).getByText("Location unknown")).toBeDefined();
    const selectionAnnouncement = screen.getByText(
      "Selected OpenAI · gpt-example. Details updated."
    );
    expect(selectionAnnouncement.closest('[role="status"]')).not.toBeNull();
    expect(selectionAnnouncement.closest('[aria-live="polite"]')).not.toBeNull();
  });

  it("synchronizes relationship selection without changing evidence meaning", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(validResponse)));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    const observedControls = await screen.findAllByRole("button", {
      name: "Select observed transfer Ambient Agent to OpenAI · gpt-example",
    });
    fireEvent.click(observedControls[0]);
    expect(
      observedControls.every((control) => control.getAttribute("aria-pressed") === "true")
    ).toBe(true);

    const details = screen.getByRole("region", { name: "Selection details" });
    expect(within(details).getByText("Observed")).toBeDefined();
    expect(within(details).getByText("Chat")).toBeDefined();
    expect(within(details).getByText("2 recorded events")).toBeDefined();

    const declaredControls = screen.getAllByRole("button", {
      name: "Select declared association morning-planner and Task",
    });
    fireEvent.click(declaredControls[1]);
    expect(
      declaredControls.every((control) => control.getAttribute("aria-pressed") === "true")
    ).toBe(true);
    expect(observedControls.every((control) => control.getAttribute("aria-pressed") === "false")).toBe(
      true
    );
    expect(within(details).getByText("Declared")).toBeDefined();
    expect(
      within(details).getByText(
        "Manifest relationships describe intent, not runtime behavior or permission."
      )
    ).toBeDefined();
  });

  it("does not render uninstrumented coverage channels as visual flow edges", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(validResponse)));

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    const visualMap = await screen.findByRole("region", { name: "Visual map" });
    expect(within(visualMap).queryByRole("button", { name: /MCP/i })).toBeNull();
    expect(within(visualMap).queryByRole("button", { name: /HTTP Agent/i })).toBeNull();
    expect(within(visualMap).queryByRole("button", { name: /Coding Agent|ACP/i })).toBeNull();
    expect(within(visualMap).queryByRole("button", { name: /Provider management/i })).toBeNull();
    expect(
      within(visualMap).queryByRole("button", { name: /Isolated Widget Runtime/i })
    ).toBeNull();
  });

  it("renders the same evidence states through the shared Chinese catalog", async () => {
    vi.stubGlobal("fetch", vi.fn(() => response(validResponse)));

    render(
      <PrivacyDataMapPanel
        open
        language="zh"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect(await screen.findByRole("heading", { name: "隐私数据地图" })).toBeDefined();
    expect(screen.getByText("已观测传输")).toBeDefined();
    expect(screen.getAllByText("声明关联").length).toBeGreaterThan(0);
    const blindSpots = screen.getByRole("region", { name: "盲区" });
    expect(within(blindSpots).getByText("未知")).toBeDefined();
    expect(within(blindSpots).getByText("当前仅为部分覆盖")).toBeDefined();
    expectCoverageChannel(blindSpots, "LLM 调用", "部分已观测");
    expectCoverageChannel(blindSpots, "MCP", "尚未接入观测");
    expectCoverageChannel(blindSpots, "HTTP Agent", "尚未接入观测");
    expectCoverageChannel(blindSpots, "统一 Coding Agent / ACP", "尚未接入观测");
    expectCoverageChannel(blindSpots, "Provider 管理", "尚未接入观测");
    expectCoverageChannel(blindSpots, "隔离 Widget Runtime", "尚未接入观测");
  });

  it("keeps declarations and unknown coverage visible when no observed flow exists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        response({
          ...validResponse,
          scope: { kind: "workspace", observed_window: null },
          source_health: { ...validResponse.source_health, valid_audit_records: 0 },
          nodes: validResponse.nodes.filter((node) => node.kind !== "recorded_model_target"),
          observed_flows: [],
        })
      )
    );

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect(
      await screen.findByText("No observed transfers are available for this workspace yet.")
    ).toBeDefined();
    expect(screen.getByText("Declared association")).toBeDefined();
    const blindSpots = screen.getByRole("region", { name: "Blind spots" });
    expect(within(blindSpots).getByText("Unknown")).toBeDefined();
    expectCoverageChannel(blindSpots, "MCP", "not instrumented");
  });

  it("keeps valid results while reporting degraded source health without raw details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        response({
          ...validResponse,
          source_health: {
            ...validResponse.source_health,
            status: "degraded",
            malformed_json_records: 1,
          },
          warnings: [{ code: "malformed_json_records", count: 1 }],
        })
      )
    );

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect((await screen.findAllByText("OpenAI · gpt-example")).length).toBeGreaterThan(0);
    expect(screen.getByText("Source data is degraded")).toBeDefined();
    expect(screen.getByText("1 malformed audit record was skipped.")).toBeDefined();
  });

  it("rejects degraded payloads that try to smuggle raw warning details into the UI", async () => {
    const rawCanary = "private-prompt-must-never-appear";
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        response({
          ...validResponse,
          source_health: {
            ...validResponse.source_health,
            status: "degraded",
            malformed_json_records: 1,
          },
          warnings: [
            {
              code: "malformed_json_records",
              count: 1,
              raw_detail: rawCanary,
            },
          ],
        })
      )
    );

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect(await screen.findByText("Privacy Map is temporarily unavailable.")).toBeDefined();
    expect(screen.queryAllByText("OpenAI · gpt-example")).toHaveLength(0);
    expect(document.body.textContent).not.toContain(rawCanary);
  });

  it("clears stale data during refresh, reports a stable error, and retries", async () => {
    let rejectRefresh: ((reason: Error) => void) | undefined;
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => response(validResponse))
      .mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            rejectRefresh = reject;
          })
      )
      .mockImplementationOnce(() => response(validResponse));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect((await screen.findAllByText("OpenAI · gpt-example")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Refresh privacy map" }));
    expect(screen.queryAllByText("OpenAI · gpt-example")).toHaveLength(0);
    expect(screen.getByRole("status").textContent).toContain("Loading privacy map");

    rejectRefresh?.(new Error("D:\\private\\audit.jsonl"));
    expect(await screen.findByText("Privacy Map is temporarily unavailable.")).toBeDefined();
    expect(document.body.textContent).not.toContain("audit.jsonl");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect((await screen.findAllByText("OpenAI · gpt-example")).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("renders no guessed topology when the API response is malformed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        response({
          ...validResponse,
          prompt: "raw-canary-that-must-not-enter-the-ui",
        })
      )
    );

    render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect(await screen.findByText("Privacy Map is temporarily unavailable.")).toBeDefined();
    expect(screen.queryAllByText("OpenAI · gpt-example")).toHaveLength(0);
    expect(document.body.textContent).not.toContain("raw-canary");
  });

  it("aborts an in-flight refresh and clears in-memory results when closed", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => response(validResponse))
      .mockImplementationOnce(() => new Promise(() => {}))
      .mockImplementationOnce(() => new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const view = render(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    expect((await screen.findAllByText("OpenAI · gpt-example")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Refresh privacy map" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const refreshSignal = fetchMock.mock.calls[1][1]?.signal as AbortSignal;
    expect(refreshSignal.aborted).toBe(false);

    view.rerender(
      <PrivacyDataMapPanel
        open={false}
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );
    expect(refreshSignal.aborted).toBe(true);

    view.rerender(
      <PrivacyDataMapPanel
        open
        language="en"
        apiBase="http://localhost:8000"
        onClose={() => {}}
      />
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(screen.queryAllByText("OpenAI · gpt-example")).toHaveLength(0);
    const reopenedSignal = fetchMock.mock.calls[2][1]?.signal as AbortSignal;
    expect(reopenedSignal.aborted).toBe(false);
  });
});
