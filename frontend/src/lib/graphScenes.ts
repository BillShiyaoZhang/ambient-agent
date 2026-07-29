import type { WidgetSchemaProposal } from "./widgetDesign";
import {
  GRAPH_DATASET_VERSION,
  normalizeGraphDataset,
  stableGraphId,
  type GraphDataset,
  type GraphEdge,
  type GraphNode,
  type GraphStatus,
} from "./graphVisualization";

export interface WorkflowDescriptorNode {
  id: string;
  label: string;
  kind: "phase" | "wait" | "terminal";
  responsibility: string;
  sourceFile: string;
  symbol: string;
}

export interface WorkflowDescriptorEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  kind: "transition" | "condition" | "wait" | "rework" | "terminal";
}

export interface WorkflowDescriptor {
  /** Version of this serializable visualization descriptor. */
  version: 1;
  /** DurableAgentWorkflow.VERSION represented by the descriptor. */
  workflowVersion: 2;
  id: "durable-agent";
  nodes: WorkflowDescriptorNode[];
  edges: WorkflowDescriptorEdge[];
}

const WORKFLOW_SOURCE_FILE = "backend/agent/durable_workflow.py";
const GITHUB_SOURCE_ROOT =
  "https://github.com/BillShiyaoZhang/ambient-agent/blob/main";

function phase(
  id: string,
  label: string,
  responsibility: string,
  kind: WorkflowDescriptorNode["kind"] = "phase",
  sourceFile = WORKFLOW_SOURCE_FILE,
  symbol?: string,
): WorkflowDescriptorNode {
  return {
    id,
    label,
    kind,
    responsibility,
    sourceFile,
    symbol: symbol ?? `DurableAgentWorkflow._phase_${id}`,
  };
}

function transition(
  source: string,
  target: string,
  label: string,
  kind: WorkflowDescriptorEdge["kind"] = "transition",
): WorkflowDescriptorEdge {
  return {
    id: `${source}:${target}:${stableGraphId("condition", label)}`,
    source,
    target,
    label,
    kind,
  };
}

/**
 * Explicit, reviewable descriptor for durable workflow v2. Keeping this
 * separate from the rendering component makes phase additions visible in
 * tests and prevents UI code from guessing transitions from live events.
 */
export const DURABLE_WORKFLOW_DESCRIPTOR: WorkflowDescriptor = {
  version: 1,
  workflowVersion: 2,
  id: "durable-agent",
  nodes: [
    phase("route", "Route intent", "Classify the request and select a durable subflow."),
    phase("clarify", "Request clarification", "Persist a clarification response and finish the request."),
    phase("converse", "Converse", "Run the conversational tool loop and persist its response."),
    phase("graph_query", "Query graph", "Execute a read-only context graph query and format the result."),
    phase("graph_preflight", "Graph preflight", "Validate and preview graph mutation actions."),
    phase("wait_graph_approval", "Graph approval", "Wait for explicit approval of graph mutations.", "wait"),
    phase("graph_commit", "Commit graph mutation", "Apply the approved mutation as an idempotent durable effect."),
    phase("multi_preflight", "Saga preflight", "Validate every step before beginning a multi-intent saga."),
    phase("multi_dispatch", "Dispatch saga step", "Dispatch the next planned intent or finish the saga."),
    phase("plan", "Development plan", "Generate or revise the App development plan."),
    phase("wait_plan", "Plan approval", "Wait for approval or refinement of the development plan.", "wait"),
    phase("align_schema", "Align schema", "Create a schema and capability proposal from the approved plan."),
    phase("wait_schema", "Schema approval", "Wait for an editable schema and capability approval.", "wait"),
    phase("stage_code", "Generate staged App", "Generate or repair App code in an isolated staging directory."),
    phase("verify", "Verify App", "Verify staged code against the approved Runtime Contract."),
    phase("wait_override", "Repair decision", "Wait for a mandatory code, schema, or plan repair choice.", "wait"),
    phase("promote", "Publish App", "Atomically publish the verified App and approved schema effects."),
    phase(
      "done",
      "Complete",
      "The durable Run completed successfully and persisted its final checkpoint.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
    phase(
      "failed",
      "Failed",
      "The durable Run stopped with a preserved non-retryable error.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
    phase(
      "needs_attention",
      "Needs attention",
      "An effect may have committed and requires explicit reconciliation.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
    phase(
      "cancelled",
      "Cancelled",
      "The durable Run was safely cancelled without an unknown effect.",
      "terminal",
      "backend/run_service.py",
      "RunStore.commit_step",
    ),
  ],
  edges: [
    transition("route", "clarify", "clarification intent", "condition"),
    transition("route", "converse", "conversation intent", "condition"),
    transition("route", "graph_query", "graph query intent", "condition"),
    transition("route", "graph_preflight", "graph mutation intent", "condition"),
    transition("route", "plan", "widget create / modify", "condition"),
    transition("route", "multi_preflight", "multi-intent / plan-and-act", "condition"),
    transition("graph_preflight", "wait_graph_approval", "preview ready", "wait"),
    transition("wait_graph_approval", "graph_commit", "approved", "condition"),
    transition("multi_preflight", "multi_dispatch", "preflight passed"),
    transition("multi_dispatch", "graph_query", "next graph query", "condition"),
    transition("multi_dispatch", "graph_preflight", "next graph mutation", "condition"),
    transition("multi_dispatch", "plan", "next widget action", "condition"),
    transition("graph_query", "multi_dispatch", "return to saga", "transition"),
    transition("graph_commit", "multi_dispatch", "return to saga", "transition"),
    transition("plan", "wait_plan", "proposal ready", "wait"),
    transition("wait_plan", "align_schema", "approved", "condition"),
    transition("wait_plan", "wait_plan", "refine", "rework"),
    transition("wait_plan", "failed", "denied", "terminal"),
    transition("align_schema", "wait_schema", "proposal ready", "wait"),
    transition("wait_schema", "stage_code", "approved", "condition"),
    transition("wait_schema", "wait_schema", "refine / fix dependencies", "rework"),
    transition("wait_schema", "plan", "rework plan", "rework"),
    transition("wait_schema", "failed", "denied", "terminal"),
    transition("stage_code", "verify", "draft staged"),
    transition("verify", "promote", "verification clean", "condition"),
    transition("verify", "wait_override", "findings require repair", "wait"),
    transition("wait_override", "stage_code", "rework code", "rework"),
    transition("wait_override", "align_schema", "rework schema", "rework"),
    transition("wait_override", "plan", "rework plan", "rework"),
    transition("wait_override", "wait_override", "bypass rejected", "rework"),
    transition("wait_override", "failed", "denied", "terminal"),
    transition("wait_graph_approval", "failed", "denied", "terminal"),
    transition("clarify", "done", "response persisted", "terminal"),
    transition("converse", "done", "response persisted", "terminal"),
    transition("graph_query", "done", "query returned", "terminal"),
    transition("graph_commit", "done", "mutation committed", "terminal"),
    transition("promote", "done", "artifact published", "terminal"),
    transition("promote", "multi_dispatch", "return to saga", "transition"),
    transition("multi_dispatch", "done", "all saga steps complete", "terminal"),
    transition("needs_attention", "failed", "effect reconciled", "terminal"),
  ],
};

const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

const asString = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value.trim() : undefined;

const asFiniteNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

const asRecords = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value)
    ? value.map(asRecord).filter((item): item is Record<string, unknown> => item !== null)
    : [];

const schemaNodeId = (category: "reused" | "new", id: string, index: number): string =>
  `schema:${category}:${encodeURIComponent(id || "unnamed")}:${index}`;

export function schemaProposalToGraph(
  proposal: WidgetSchemaProposal,
  dependencyErrors: string[] = [],
): GraphDataset {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  const entityNodes = new Map<string, string[]>();
  const registerEntity = (entityId: string, nodeId: string): void => {
    const ids = entityNodes.get(entityId) ?? [];
    ids.push(nodeId);
    entityNodes.set(entityId, ids);
  };

  proposal.reused_schemas.forEach((schema, index) => {
    const entityId = schema.id.trim();
    const nodeId = schemaNodeId("reused", entityId, index);
    registerEntity(entityId, nodeId);
    nodes.push({
      id: nodeId,
      label: entityId || "Unnamed reused entity",
      kind: "schema-reused",
      status: entityId ? "existing" : "error",
      summary: schema.reason || "Reuse a canonical ontology entity.",
      details: {
        "Entity ID": entityId,
        "Extended properties": schema.extended_properties,
        "Data scope": schema.data_scope ?? "user_context",
      },
      badges: ["Reused"],
    });
  });

  proposal.new_schemas.forEach((schema, index) => {
    const entityId = schema.id.trim();
    const nodeId = schemaNodeId("new", entityId, index);
    registerEntity(entityId, nodeId);
    nodes.push({
      id: nodeId,
      label: schema.name.trim() || entityId || "Unnamed new entity",
      kind: "schema-new",
      status: entityId ? "default" : "error",
      summary: schema.description || "New user-context ontology entity.",
      details: {
        "Entity ID": entityId,
        Properties: schema.properties,
        "Ontology IRI": schema.ontology_iri,
        "Equivalent IRIs": schema.equivalent_to,
        "Data scope": schema.data_scope,
      },
      badges: ["New"],
    });
  });

  for (const [entityId, ids] of entityNodes) {
    if (entityId && ids.length > 1) {
      nodes.forEach((node) => {
        if (ids.includes(node.id)) {
          node.status = "error";
          node.badges = [...(node.badges ?? []), "Duplicate ID"];
        }
      });
    }
  }

  const parentNodeById = new Map<string, string>();
  proposal.new_schemas.forEach((schema, index) => {
    const parentId = schema.subclass_of.trim();
    if (!parentId) return;
    let targetId = entityNodes.get(parentId)?.[0];
    if (!targetId) {
      targetId = parentNodeById.get(parentId);
      if (!targetId) {
        targetId = `schema:parent:${encodeURIComponent(parentId)}`;
        parentNodeById.set(parentId, targetId);
        nodes.push({
          id: targetId,
          label: parentId,
          kind: "schema-parent",
          status: parentId === "Thing" ? "existing" : "unknown",
          summary: parentId === "Thing"
            ? "Canonical ontology root."
            : "Referenced parent outside this proposal; backend validation remains authoritative.",
          badges: [parentId === "Thing" ? "Parent" : "Parent · verify"],
        });
      }
    }
    edges.push({
      id: `schema-parent-edge:${index}:${encodeURIComponent(parentId)}`,
      source: schemaNodeId("new", schema.id.trim(), index),
      target: targetId,
      label: "subclass_of",
      kind: "inheritance",
    });
  });

  const proposalNodeId = "schema:proposal";
  nodes.push({
    id: proposalNodeId,
    label: "Widget schema proposal",
    kind: "schema-proposal",
    summary: "The editable proposal remains the source of truth.",
  });
  proposal.capabilities.forEach((capability, index) => {
    const capabilityId = capability.id.trim();
    const isGraphGrant = capabilityId === "graph.query" || capabilityId === "graph.mutate";
    const nodeId = `capability:${index}:${encodeURIComponent(capabilityId || "unnamed")}`;
    nodes.push({
      id: nodeId,
      label: capabilityId || "Unnamed capability",
      kind: isGraphGrant ? "graph-grant" : "capability",
      status: capabilityId ? "default" : "error",
      summary: isGraphGrant ? "Entity-scoped Graph grant." : "Requested App capability.",
      details: { Scope: capability.scope },
      badges: [isGraphGrant ? "Graph grant" : "Capability"],
    });

    const rawEntities = Array.isArray(capability.scope.entities)
      ? capability.scope.entities.filter((value): value is string =>
        typeof value === "string" && value.trim().length > 0)
      : [];
    if (isGraphGrant && rawEntities.length > 0) {
      for (const entityId of [...new Set(rawEntities.map((value) => value.trim()))].sort()) {
        let targets = entityNodes.get(entityId);
        if (!targets || targets.length === 0) {
          const missingNodeId = `schema:missing:${encodeURIComponent(entityId)}`;
          if (!nodes.some((node) => node.id === missingNodeId)) {
            nodes.push({
              id: missingNodeId,
              label: entityId,
              kind: "validation-error",
              status: "error",
              summary: "This Graph grant references an entity outside the proposal.",
              badges: ["Dangling grant"],
            });
          }
          targets = [missingNodeId];
        }
        for (const target of targets) {
          edges.push({
            id: `grant:${index}:${encodeURIComponent(entityId)}:${target}`,
            source: nodeId,
            target,
            label: capabilityId,
            kind: "capability-scope",
            status: target.startsWith("schema:missing:") ? "error" : "default",
          });
        }
      }
    } else {
      edges.push({
        id: `capability-scope:${index}`,
        source: nodeId,
        target: proposalNodeId,
        label: "capability scope",
        kind: "capability-scope",
      });
    }
  });

  dependencyErrors.forEach((message, index) => {
    const normalizedMessage = message.trim();
    if (!normalizedMessage) return;
    const errorNodeId = `${stableGraphId("schema-validation", normalizedMessage)}:${index}`;
    nodes.push({
      id: errorNodeId,
      label: "Dependency error",
      kind: "validation-error",
      status: "error",
      summary: normalizedMessage,
      details: { Diagnostic: normalizedMessage },
      badges: ["Blocks approval"],
    });
    edges.push({
      id: `validation:${index}`,
      source: errorNodeId,
      target: proposalNodeId,
      label: "blocks approval",
      kind: "validation",
      status: "error",
    });
  });

  return normalizeGraphDataset({
    version: GRAPH_DATASET_VERSION,
    title: "Schema and capability proposal",
    description: "A live projection of the editable approval proposal.",
    nodes,
    edges,
    metadata: {
      title: "Schema and capability proposal",
      description: "Edit the form to rebuild this graph; the graph never grants permission.",
      truncated: false,
    },
  });
}

interface WorkflowOverlay {
  status?: GraphStatus;
  attempt?: number;
  summary?: string;
  result?: unknown;
  error?: unknown;
  eventType?: string;
  sequence?: number;
  current?: boolean;
  checkpoint?: boolean;
}

function phaseIdFromRecord(value: Record<string, unknown>): string | undefined {
  return asString(value.step_key)
    ?? asString(value.step_id)
    ?? asString(value.phase)
    ?? asString(value.id);
}

function normalizeRunStatus(value: unknown): GraphStatus | undefined {
  const status = asString(value)?.toLocaleLowerCase();
  if (!status) return undefined;
  if (["success", "succeeded", "completed", "committed", "passed"].includes(status)) return "succeeded";
  if (["failed", "failure", "error"].includes(status)) return "failed";
  if (["running", "started", "in_progress", "active"].includes(status)) return "running";
  if (status === "needs_attention") return "needs_attention";
  if (["waiting", "waiting_user", "paused", "blocked"].includes(status)) return "waiting";
  if (["cancelled", "canceled", "cancel_requested"].includes(status)) return "cancelled";
  if (["queued", "pending", "idle", "not_started"].includes(status)) return "not_started";
  return status;
}

function eventStatus(event: Record<string, unknown>): GraphStatus | undefined {
  const payload = asRecord(event.payload);
  const outcome = asRecord(payload?.outcome);
  const explicit = normalizeRunStatus(event.status ?? payload?.status ?? payload?.to);
  if (explicit) return explicit;
  const type = asString(event.type)?.toLocaleLowerCase() ?? "";
  if (type === "step_committed") {
    const outcomeKind = asString(outcome?.kind)?.toLocaleLowerCase();
    if (outcomeKind === "continue" || outcomeKind === "succeeded") return "succeeded";
    if (outcomeKind === "wait") return "waiting";
    if (outcomeKind === "failed") return "failed";
    if (outcomeKind === "cancelled" || outcomeKind === "canceled") return "cancelled";
  }
  if (type.includes("failed") || type.includes("error")) return "failed";
  if (type.includes("started") || type.includes("running")) return "running";
  if (type.includes("waiting") || type.includes("attention")) return "waiting";
  if (type.includes("committed") || type.includes("completed") || type.includes("succeeded")) return "succeeded";
  if (type.includes("cancel")) return "cancelled";
  return undefined;
}

function errorFromRecord(value: Record<string, unknown>): unknown {
  const payload = asRecord(value.payload);
  return value.error ?? payload?.error ?? payload?.message;
}

function addBadge(badges: string[], badge: string): void {
  if (!badges.includes(badge)) badges.push(badge);
}

export function workflowToGraph(runValue?: unknown): GraphDataset {
  const run = asRecord(runValue);
  const overlays = new Map<string, WorkflowOverlay>();
  const knownPhases = new Set(DURABLE_WORKFLOW_DESCRIPTOR.nodes.map((node) => node.id));
  const discoveredPhases = new Set<string>();

  const checkpoint = asRecord(run?.checkpoint);
  const checkpointState = asRecord(checkpoint?.state);
  const state = asRecord(run?.state);
  const currentPhase =
    asString(state?.phase)
    ?? asString(checkpointState?.phase)
    ?? asString(checkpoint?.phase);
  const checkpointPhase =
    asString(checkpoint?.last_step)
    ?? asString(checkpoint?.step_id);

  if (checkpointPhase) {
    discoveredPhases.add(checkpointPhase);
    overlays.set(checkpointPhase, {
      status: "succeeded",
      attempt: asFiniteNumber(checkpoint?.attempt),
      checkpoint: true,
    });
  }

  for (const step of asRecords(run?.steps)) {
    const id = phaseIdFromRecord(step);
    if (!id) continue;
    discoveredPhases.add(id);
    const prior = overlays.get(id) ?? {};
    const attempt = asFiniteNumber(step.attempt);
    if (
      prior.attempt !== undefined
      && attempt !== undefined
      && attempt < prior.attempt
    ) continue;
    overlays.set(id, {
      ...prior,
      status: normalizeRunStatus(step.status) ?? prior.status,
      attempt: attempt ?? prior.attempt,
      summary: asString(step.summary) ?? prior.summary,
      result: step.result ?? step.output ?? prior.result,
      error: step.error ?? prior.error,
    });
  }

  const events = asRecords(run?.events)
    .sort((left, right) =>
      (asFiniteNumber(left.sequence) ?? 0) - (asFiniteNumber(right.sequence) ?? 0));
  for (const event of events) {
    const id = phaseIdFromRecord(event);
    if (!id) continue;
    discoveredPhases.add(id);
    const prior = overlays.get(id) ?? {};
    const sequence = asFiniteNumber(event.sequence) ?? 0;
    if (prior.sequence !== undefined && sequence < prior.sequence) continue;
    const payload = asRecord(event.payload);
    overlays.set(id, {
      ...prior,
      status: eventStatus(event) ?? prior.status,
      attempt: asFiniteNumber(event.attempt) ?? prior.attempt,
      summary: asString(payload?.summary) ?? asString(event.summary) ?? prior.summary,
      result: payload?.result ?? event.result ?? prior.result,
      error: errorFromRecord(event) ?? prior.error,
      eventType: asString(event.type) ?? prior.eventType,
      sequence,
    });
  }

  if (currentPhase) {
    discoveredPhases.add(currentPhase);
    const prior = overlays.get(currentPhase) ?? {};
    overlays.set(currentPhase, {
      ...prior,
      status: normalizeRunStatus(run?.status) ?? prior.status ?? "running",
      current: true,
      error: run?.error ?? prior.error,
    });
  }

  const descriptorNodes: GraphNode[] = DURABLE_WORKFLOW_DESCRIPTOR.nodes.map((node) => {
    const overlay = overlays.get(node.id);
    let status: GraphStatus = overlay?.status ?? "not_started";
    if (node.id === "done" && normalizeRunStatus(run?.status) === "succeeded") status = "succeeded";
    if (node.id === "failed" && normalizeRunStatus(run?.status) === "failed") status = "failed";
    if (node.id === "needs_attention" && asString(run?.status) === "needs_attention") {
      status = "needs_attention";
    }
    if (node.id === "cancelled" && normalizeRunStatus(run?.status) === "cancelled") status = "cancelled";
    const badges: string[] = [];
    if (node.kind === "wait") addBadge(badges, "Wait");
    if (node.kind === "terminal") addBadge(badges, "Terminal");
    if (overlay?.attempt !== undefined) addBadge(badges, `Attempt ${overlay.attempt}`);
    if (overlay?.checkpoint) addBadge(badges, "Checkpoint");
    if (overlay?.current) addBadge(badges, "Current phase");
    return {
      id: `workflow:${node.id}`,
      label: node.label,
      kind: node.kind === "terminal" ? "workflow-terminal" : `workflow-${node.kind}`,
      status,
      summary: overlay?.summary ?? node.responsibility,
      details: {
        Responsibility: node.responsibility,
        "Source file": node.sourceFile,
        Symbol: node.symbol,
        "Execution status": status,
        ...(overlay?.eventType ? { "Latest event": overlay.eventType } : {}),
        ...(overlay?.result !== undefined ? { Result: overlay.result } : {}),
        ...(overlay?.error !== undefined ? { Error: overlay.error } : {}),
      },
      ...(badges.length > 0 ? { badges } : {}),
      action: { label: "Open source", href: `${GITHUB_SOURCE_ROOT}/${node.sourceFile}` },
    };
  });

  const unknownNodes: GraphNode[] = [...discoveredPhases]
    .filter((id) => !knownPhases.has(id))
    .sort()
    .map((id) => {
      const overlay = overlays.get(id);
      const badges = ["Unknown phase"];
      if (overlay?.attempt !== undefined) badges.push(`Attempt ${overlay.attempt}`);
      if (overlay?.checkpoint) badges.push("Checkpoint");
      if (overlay?.current) badges.push("Current phase");
      return {
        id: `workflow:${id}`,
        label: id.replaceAll("_", " "),
        kind: "workflow-unknown",
        status: overlay?.status ?? "unknown",
        summary: "This phase is not present in visualization descriptor v1.",
        details: {
          "Phase ID": id,
          "Execution status": overlay?.status ?? "unknown",
          ...(overlay?.eventType ? { "Latest event": overlay.eventType } : {}),
          ...(overlay?.result !== undefined ? { Result: overlay.result } : {}),
          ...(overlay?.error !== undefined ? { Error: overlay.error } : {}),
        },
        badges,
      };
    });

  const descriptorEdges: GraphEdge[] = DURABLE_WORKFLOW_DESCRIPTOR.edges.map((edge) => ({
    id: `workflow-edge:${edge.id}`,
    source: `workflow:${edge.source}`,
    target: `workflow:${edge.target}`,
    label: edge.label,
    kind: edge.kind,
    status: edge.kind === "rework" ? "warning" : "default",
    details: { Condition: edge.label },
  }));

  if (unknownNodes.length > 0) {
    const source = currentPhase && knownPhases.has(currentPhase)
      ? `workflow:${currentPhase}`
      : "workflow:route";
    for (const node of unknownNodes) {
      if (node.id === source) continue;
      descriptorEdges.push({
        id: `workflow-edge:unknown:${node.id}`,
        source,
        target: node.id,
        label: "unknown transition",
        kind: "condition",
        status: "warning",
      });
    }
  }

  const runError = run?.error;
  if (runError !== undefined && !currentPhase) {
    const failed = descriptorNodes.find((node) => node.id === "workflow:failed");
    if (failed) {
      failed.status = "failed";
      failed.details = { ...(failed.details ?? {}), Error: runError };
    }
  }
  const result = run?.result;
  if (result !== undefined) {
    const complete = descriptorNodes.find((node) => node.id === "workflow:done");
    if (complete) complete.details = { ...(complete.details ?? {}), Result: result };
  }
  const workflowType = asString(run?.workflow_type);
  const workflowVersion =
    asFiniteNumber(run?.workflow_version)
    ?? asFiniteNumber(state?.workflow_version)
    ?? DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion;
  const workflowVersionMismatch =
    Boolean(run) && workflowVersion !== DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion;

  return normalizeGraphDataset({
    version: GRAPH_DATASET_VERSION,
    title: run ? "Agent workflow run" : "Agent workflow design",
    description: run
      ? "Durable workflow descriptor with the retained Run evidence overlaid."
      : "Versioned design for the durable Agent workflow.",
    nodes: [...descriptorNodes, ...unknownNodes],
    edges: descriptorEdges,
    metadata: {
      title: run ? "Agent workflow run" : "Agent workflow design",
      description: run
        ? `Workflow ${workflowType ?? "unknown"} v${workflowVersion} · descriptor v${DURABLE_WORKFLOW_DESCRIPTOR.version} for workflow v${DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion}`
        : `Durable workflow v${DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion}`,
      truncated: false,
      blind_spots: run
        ? ["The retained event window or an older Run version may be incomplete; missing events do not prove a phase did not execute."]
        : [],
      limitations: workflowVersionMismatch
        ? [
            `Run workflow version ${workflowVersion} differs from descriptor workflow version ${DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion}; topology and phase coverage may be inaccurate.`,
          ]
        : [],
      workflow_type: workflowType,
      workflow_version: workflowVersion,
      descriptor_version: DURABLE_WORKFLOW_DESCRIPTOR.version,
      descriptor_workflow_version: DURABLE_WORKFLOW_DESCRIPTOR.workflowVersion,
      workflow_version_mismatch: workflowVersionMismatch,
    },
  });
}

const SAFE_DATA_MAP_DETAIL_KEYS = new Set([
  "category",
  "data_category",
  "data_categories",
  "stage",
  "target",
  "target_type",
  "source_category",
  "target_category",
  "provider",
  "provider_category",
  "capability",
  "capability_category",
  "capability_categories",
  "app_id",
  "semantic",
  "semantics",
  "reason",
  "count",
  "first_seen",
  "last_seen",
  "evidence_count",
  "declared_app_count",
  "instrumented",
  "coverage",
  "blind_spot",
]);

function safeDataMapDetails(value: unknown): Record<string, unknown> | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const entries = Object.entries(record)
    .filter(([key]) => SAFE_DATA_MAP_DETAIL_KEYS.has(key.toLocaleLowerCase()));
  return entries.length > 0 ? Object.fromEntries(entries) : undefined;
}

/**
 * Privacy maps are a deliberately narrow projection. The frontend repeats the
 * backend allow-list boundary so raw prompt/response/tool/provider payload
 * fields cannot accidentally enter the shared details panel.
 */
export function dataMapToGraph(value: unknown): GraphDataset {
  const root = asRecord(value) ?? {};
  const rawMetadata = asRecord(root.metadata) ?? {};
  const nodes = asRecords(root.nodes).map((node) => ({
    id: node.id,
    label: node.label,
    kind: node.kind,
    status: node.status,
    summary: node.summary,
    badges: node.badges,
    ...(safeDataMapDetails(node.details) ? { details: safeDataMapDetails(node.details) } : {}),
  }));
  const edges = asRecords(root.edges).map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    kind: edge.kind,
    status: edge.status,
    ...(safeDataMapDetails(edge.details) ? { details: safeDataMapDetails(edge.details) } : {}),
  }));
  return normalizeGraphDataset({
    version: root.version,
    title: root.title,
    description: root.description,
    nodes,
    edges,
    metadata: {
      title: rawMetadata.title ?? root.title,
      description: rawMetadata.description ?? root.description,
      counts: rawMetadata.counts,
      total_nodes: rawMetadata.total_nodes,
      returned_nodes: rawMetadata.returned_nodes,
      total_edges: rawMetadata.total_edges,
      returned_edges: rawMetadata.returned_edges,
      truncated: rawMetadata.truncated,
      time_window: rawMetadata.time_window,
      coverage: rawMetadata.coverage,
      blind_spots: rawMetadata.blind_spots,
      limitations: rawMetadata.limitations,
      semantics: rawMetadata.semantics,
      evidence_count: rawMetadata.evidence_count,
      declared_app_count: rawMetadata.declared_app_count,
      generated_at: rawMetadata.generated_at,
    },
  });
}
