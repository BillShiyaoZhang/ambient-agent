import { afterEach, describe, expect, it, vi } from "vitest";
import {
  PrivacyDataMapContractError,
  PrivacyDataMapRequestError,
  loadPrivacyDataMap,
} from "../../frontend/src/services/privacyDataMap";

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
    {
      id: "platform:ambient-agent",
      kind: "platform",
      label: "Ambient Agent",
    },
    {
      id: `model_target:${"a".repeat(64)}`,
      kind: "recorded_model_target",
      label: "OpenAI · gpt-example",
      location: "unknown",
    },
    {
      id: "app:morning-planner",
      kind: "app",
      label: "morning-planner",
    },
    {
      id: "schema:Task",
      kind: "schema",
      label: "Task",
    },
  ],
  observed_flows: [
    {
      id: `flow:${"b".repeat(64)}`,
      evidence: "observed",
      source_node_id: "platform:ambient-agent",
      destination_node_id: `model_target:${"a".repeat(64)}`,
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

function digest(index: number): string {
  return index.toString(16).padStart(64, "0");
}

function stubResponse(payload: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function maximumValidResponse() {
  const observedAt = "2026-07-20T08:00:00Z";
  const stages = ["chat", "route", "plan", "mutation"] as const;
  const modelTargets = Array.from({ length: 512 }, (_, index) => ({
    id: `model_target:${digest(index)}`,
    kind: "recorded_model_target",
    label: `Provider ${index} 路 Model ${index}`,
    location: "unknown",
  }));
  const apps = Array.from({ length: 2_048 }, (_, index) => {
    const appId = `app-${index}`;
    return {
      id: `app:${appId}`,
      kind: "app",
      label: appId,
    };
  });
  const schemas = Array.from({ length: 4_096 }, (_, index) => {
    const schemaId = `Schema${index}`;
    return {
      id: `schema:${schemaId}`,
      kind: "schema",
      label: schemaId,
    };
  });
  const observedFlows = modelTargets.flatMap((node, modelIndex) =>
    stages.map((stage, stageIndex) => ({
      id: `flow:${digest(10_000 + modelIndex * stages.length + stageIndex)}`,
      evidence: "observed",
      source_node_id: "platform:ambient-agent",
      destination_node_id: node.id,
      stage,
      count: 1,
      first_observed_at: observedAt,
      last_observed_at: observedAt,
    }))
  );
  const declaredAssociations = apps.flatMap((app, appIndex) =>
    Array.from({ length: 4 }, (_, offset) => {
      const schema = schemas[(appIndex * 4 + offset) % schemas.length];
      return {
        id: `declaration:${digest(100_000 + appIndex * 4 + offset)}`,
        evidence: "declared",
        app_node_id: app.id,
        schema_node_id: schema.id,
      };
    })
  );

  return {
    ...validResponse,
    scope: {
      kind: "workspace",
      observed_window: {
        from: observedAt,
        to: observedAt,
      },
    },
    source_health: {
      ...validResponse.source_health,
      valid_audit_records: observedFlows.length,
    },
    nodes: [validResponse.nodes[0], ...modelTargets, ...apps, ...schemas],
    observed_flows: observedFlows,
    declared_associations: declaredAssociations,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Privacy Data Map client", () => {
  it("requests the V1 endpoint with only an Accept header and returns the strict contract", async () => {
    const fetchMock = stubResponse(validResponse);

    await expect(loadPrivacyDataMap("http://localhost:8000")).resolves.toEqual(validResponse);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/privacy-data-map", {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: undefined,
    });
  });

  it("forwards cancellation without adding request metadata", async () => {
    const fetchMock = stubResponse(validResponse);
    const controller = new AbortController();

    await loadPrivacyDataMap("http://localhost:8000/", controller.signal);

    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/privacy-data-map", {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
  });

  it("rejects unknown fields instead of retaining raw payload data", async () => {
    stubResponse({
      ...validResponse,
      prompt: "raw-canary-that-must-not-enter-the-ui",
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects a response declared larger than the V1 browser budget before parsing it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(validResponse), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": String(16 * 1024 * 1024 + 1),
        },
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects a streamed response that exceeds the V1 browser budget without Content-Length", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(16 * 1024 * 1024 + 1));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects malformed Content-Length and invalid UTF-8 as contract failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(validResponse), {
          status: 200,
          headers: { "Content-Length": "not-a-decimal-length" },
        })
      )
    );
    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(Uint8Array.of(0xff), { status: 200 }))
    );
    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects contract arrays that exceed the V1 projection cardinality limits", async () => {
    stubResponse({
      ...validResponse,
      warnings: Array.from({ length: 8 }, (_, index) => ({
        code: "malformed_json_records",
        count: index + 1,
      })),
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects more than 512 recorded model targets even when the total node limit is respected", async () => {
    const modelTargets = Array.from({ length: 513 }, (_, index) => ({
      id: `model_target:${digest(index)}`,
      kind: "recorded_model_target",
      label: `Provider ${index} · Model ${index}`,
      location: "unknown",
    }));
    const observedFlows = modelTargets.map((node, index) => ({
      id: `flow:${digest(index + 10_000)}`,
      evidence: "observed",
      source_node_id: "platform:ambient-agent",
      destination_node_id: node.id,
      stage: "chat",
      count: 1,
      first_observed_at: "2026-07-20T08:00:00Z",
      last_observed_at: "2026-07-20T08:30:00Z",
    }));
    stubResponse({
      ...validResponse,
      source_health: {
        ...validResponse.source_health,
        valid_audit_records: modelTargets.length,
      },
      nodes: [
        validResponse.nodes[0],
        ...modelTargets,
        validResponse.nodes[2],
        validResponse.nodes[3],
      ],
      observed_flows: observedFlows,
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects more than 2048 App nodes even when the total node limit is respected", async () => {
    const appNodes = [
      validResponse.nodes[2],
      ...Array.from({ length: 2_048 }, (_, index) => {
        const appId = `app-${index}`;
        return {
          id: `app:${appId}`,
          kind: "app",
          label: appId,
        };
      }),
    ];
    stubResponse({
      ...validResponse,
      nodes: [
        validResponse.nodes[0],
        validResponse.nodes[1],
        ...appNodes,
        validResponse.nodes[3],
      ],
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects more than 4096 schema nodes even when the total node limit is respected", async () => {
    const schemaNodes = Array.from({ length: 4_097 }, (_, index) => {
      const schemaId = `Schema${index}`;
      return {
        id: `schema:${schemaId}`,
        kind: "schema",
        label: schemaId,
      };
    });
    const declaredAssociations = schemaNodes.map((node, index) => ({
      id: `declaration:${digest(index + 20_000)}`,
      evidence: "declared",
      app_node_id: "app:morning-planner",
      schema_node_id: node.id,
    }));
    stubResponse({
      ...validResponse,
      nodes: [
        validResponse.nodes[0],
        validResponse.nodes[1],
        validResponse.nodes[2],
        ...schemaNodes,
      ],
      declared_associations: declaredAssociations,
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("accepts a maximum-cardinality V1 contract within the browser response budget", async () => {
    const payload = maximumValidResponse();
    const encodedBytes = new TextEncoder().encode(JSON.stringify(payload)).byteLength;
    stubResponse(payload);

    await expect(loadPrivacyDataMap("http://localhost:8000")).resolves.toEqual(payload);
    expect(encodedBytes).toBeLessThanOrEqual(16 * 1024 * 1024);
    expect(payload.nodes).toHaveLength(1 + 512 + 2_048 + 4_096);
    expect(payload.observed_flows).toHaveLength(2_048);
    expect(payload.declared_associations).toHaveLength(8_192);
  });

  it.each([
    ["403 BMP code points", "a".repeat(403)],
    ["403 non-BMP code points", "😀".repeat(403)],
  ])("accepts a canonical recorded-model label at the %s boundary", async (_case, label) => {
    const payload = {
      ...validResponse,
      nodes: validResponse.nodes.map((node) =>
        node.kind === "recorded_model_target" ? { ...node, label } : node
      ),
    };
    stubResponse(payload);

    await expect(loadPrivacyDataMap("http://localhost:8000")).resolves.toEqual(payload);
  });

  it.each([
    ["404 BMP code points", "a".repeat(404)],
    ["404 non-BMP code points", "😀".repeat(404)],
    ["non-NFKC text", "Model \u212B"],
    ["leading whitespace", " Model"],
    ["trailing whitespace", "Model "],
    ["a control character", "Model\u0000Name"],
    ["a format character", "Model\u200BName"],
    ["a private-use character", "Model\uE000Name"],
    ["a line separator", "Model\u2028Name"],
    ["a paragraph separator", "Model\u2029Name"],
  ])("rejects a recorded-model label containing %s", async (_case, label) => {
    stubResponse({
      ...validResponse,
      nodes: validResponse.nodes.map((node) =>
        node.kind === "recorded_model_target" ? { ...node, label } : node
      ),
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects malformed nested items and dangling topology references", async () => {
    stubResponse({
      ...validResponse,
      nodes: validResponse.nodes.map((node) =>
        node.kind === "recorded_model_target" ? { ...node, location: "remote" } : node
      ),
      observed_flows: [
        {
          ...validResponse.observed_flows[0],
          destination_node_id: "model_target:missing",
        },
      ],
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects duplicate identities rather than merging conflicting topology", async () => {
    stubResponse({
      ...validResponse,
      nodes: [...validResponse.nodes, validResponse.nodes[1]],
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("surfaces a typed stable backend error without exposing arbitrary response details", async () => {
    stubResponse(
      {
        contract_version: 1,
        error: {
          code: "privacy_map_resource_limit_exceeded",
          message: "Privacy Map is temporarily unavailable.",
        },
      },
      503
    );

    const error = await loadPrivacyDataMap("http://localhost:8000").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(PrivacyDataMapRequestError);
    expect(error).toMatchObject({
      status: 503,
      code: "privacy_map_resource_limit_exceeded",
      message: "Privacy Map is temporarily unavailable.",
    });
  });

  it("accepts the backend invalid-request error as part of the V1 error contract", async () => {
    stubResponse(
      {
        contract_version: 1,
        error: {
          code: "privacy_map_invalid_request",
          message: "Privacy Map is temporarily unavailable.",
        },
      },
      400
    );

    const error = await loadPrivacyDataMap("http://localhost:8000").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(PrivacyDataMapRequestError);
    expect(error).toMatchObject({
      status: 400,
      code: "privacy_map_invalid_request",
    });
  });

  it("rejects the retired access-denied error as outside the V1 contract", async () => {
    stubResponse(
      {
        contract_version: 1,
        error: {
          code: "privacy_map_access_denied",
          message: "Privacy Map is temporarily unavailable.",
        },
      },
      403
    );

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it.each([
    ["an impossible calendar date", "2026-02-31T08:30:00Z"],
    ["a non-canonical zero fraction", "2026-07-20T08:30:00.0Z"],
    ["a non-canonical trailing fractional zero", "2026-07-20T08:30:00.120000Z"],
  ])("rejects %s in UTC timestamp fields", async (_case, timestamp) => {
    stubResponse({
      ...validResponse,
      generated_at: timestamp,
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it.each([
    {
      name: "an aggregate count that disagrees with source health",
      payload: {
        ...validResponse,
        source_health: { ...validResponse.source_health, valid_audit_records: 1 },
      },
    },
    {
      name: "observed flows without an observed window",
      payload: {
        ...validResponse,
        scope: { kind: "workspace", observed_window: null },
      },
    },
    {
      name: "an observed window that does not exactly cover the flows",
      payload: {
        ...validResponse,
        scope: {
          kind: "workspace",
          observed_window: {
            from: "2026-07-20T07:59:59Z",
            to: "2026-07-20T08:30:00Z",
          },
        },
      },
    },
    {
      name: "an unreferenced recorded model target",
      payload: {
        ...validResponse,
        nodes: [
          ...validResponse.nodes,
          {
            id: `model_target:${"d".repeat(64)}`,
            kind: "recorded_model_target",
            label: "Unreferenced · model",
            location: "unknown",
          },
        ],
      },
    },
    {
      name: "an unreferenced schema node",
      payload: {
        ...validResponse,
        nodes: [
          ...validResponse.nodes,
          {
            id: "schema:Event",
            kind: "schema",
            label: "Event",
          },
        ],
      },
    },
  ])("rejects $name", async ({ payload }) => {
    stubResponse(payload);

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("rejects a non-null observed window when there are no observed records", async () => {
    stubResponse({
      ...validResponse,
      source_health: { ...validResponse.source_health, valid_audit_records: 0 },
      nodes: validResponse.nodes.filter((node) => node.kind !== "recorded_model_target"),
      observed_flows: [],
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it.each([
    "Morning-Planner",
    " morning-planner",
    `morning-${"a".repeat(57)}`,
    "con",
  ])("rejects an App node outside the Manifest V1 identity contract: %s", async (appId) => {
    stubResponse({
      ...validResponse,
      nodes: validResponse.nodes.map((node) =>
        node.kind === "app"
          ? {
              ...node,
              id: `app:${appId}`,
              label: appId,
            }
          : node
      ),
      declared_associations: validResponse.declared_associations.map((association) => ({
        ...association,
        app_node_id: `app:${appId}`,
      })),
    });

    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });

  it("treats malformed success and error payloads as contract failures", async () => {
    stubResponse({ contract_version: 2 });
    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );

    stubResponse({ detail: "D:\\private\\audit_logs.jsonl" }, 500);
    await expect(loadPrivacyDataMap("http://localhost:8000")).rejects.toBeInstanceOf(
      PrivacyDataMapContractError
    );
  });
});
