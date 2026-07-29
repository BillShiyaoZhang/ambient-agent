export const GRAPH_DATASET_VERSION = 1 as const;

export type GraphLayoutDirection = "LR" | "TB";

export type GraphStatus =
  | "default"
  | "existing"
  | "not_started"
  | "running"
  | "waiting"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "warning"
  | "error"
  | "observed"
  | "declared"
  | "unknown"
  | (string & {});

export interface GraphPosition {
  x: number;
  y: number;
}

export interface GraphAction {
  label: string;
  href: string;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: string;
  status?: GraphStatus;
  summary?: string;
  details?: Record<string, unknown>;
  badges?: string[];
  action?: GraphAction;
  position?: GraphPosition;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  kind: string;
  status?: GraphStatus;
  details?: Record<string, unknown>;
}

export interface GraphCount {
  total: number;
  returned: number;
}

export interface GraphCounts {
  nodes?: GraphCount;
  edges?: GraphCount;
}

export interface GraphTimeWindow {
  start?: string;
  end?: string;
  [key: string]: unknown;
}

export interface GraphMetadata {
  title: string;
  description?: string;
  counts?: GraphCounts;
  totalNodes?: number;
  returnedNodes?: number;
  totalEdges?: number;
  returnedEdges?: number;
  truncated?: boolean;
  timeWindow?: GraphTimeWindow;
  coverage?: string[] | Record<string, unknown>;
  blindSpots?: string[];
  limitations?: string[];
  semantics?: Record<string, unknown>;
  evidenceCount?: number;
  declaredAppCount?: number;
  generatedAt?: string;
  error?: string;
  workflowType?: string;
  workflowVersion?: number;
  descriptorVersion?: number;
  descriptorWorkflowVersion?: number;
  workflowVersionMismatch?: boolean;
  detailTruncation?: Record<string, unknown>;
  [key: string]: unknown;
}

/**
 * The common, read-only visualization contract. Business data is projected
 * into this shape before it reaches React Flow; canvas positions are session
 * presentation state only.
 */
export interface GraphDataset {
  version: number;
  title?: string;
  description?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  metadata: GraphMetadata;
}

export interface GraphExplorerPayload {
  version: number;
  generatedAt?: string;
  ontology: GraphDataset;
  knowledgeGraph: GraphDataset;
}

export interface GraphFilter {
  query?: string;
  kinds?: Iterable<string>;
  focusNodeId?: string | null;
}

const asRecord = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

const asString = (value: unknown): string | undefined =>
  typeof value === "string" && value.trim() ? value.trim() : undefined;

const asIdentityString = (value: unknown): string | undefined =>
  typeof value === "string" && value.length > 0 ? value : undefined;

const asFiniteNumber = (value: unknown): number | undefined =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

const asBoolean = (value: unknown): boolean | undefined =>
  typeof value === "boolean" ? value : undefined;

const asStringArray = (value: unknown): string[] | undefined => {
  if (!Array.isArray(value)) return undefined;
  return value
    .filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    .map((item) => item.trim());
};

const asRecordArray = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value)
    ? value.map(asRecord).filter((item): item is Record<string, unknown> => item !== null)
    : [];

function stableJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    try {
      return JSON.stringify(value) ?? String(value);
    } catch {
      return String(value);
    }
  }
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableJson(record[key])}`).join(",")}}`;
}

export function stableGraphId(prefix: string, value: unknown): string {
  const input = stableJson(value);
  let hash = 2166136261;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${prefix}:${(hash >>> 0).toString(36)}`;
}

function normalizePosition(value: unknown): GraphPosition | undefined {
  const record = asRecord(value);
  const x = asFiniteNumber(record?.x);
  const y = asFiniteNumber(record?.y);
  return x === undefined || y === undefined ? undefined : { x, y };
}

function normalizeNode(value: Record<string, unknown>, index: number): GraphNode | null {
  const id = asIdentityString(value.id);
  if (!id) return null;
  const details = asRecord(value.details);
  const action = asRecord(value.action);
  const actionLabel = asString(action?.label);
  const actionHref = asString(action?.href);
  return {
    id,
    label: asString(value.label) ?? id,
    kind: asIdentityString(value.kind) ?? "unknown",
    ...(asString(value.status) ? { status: asString(value.status) as GraphStatus } : {}),
    ...(asString(value.summary) ? { summary: asString(value.summary) } : {}),
    ...(details ? { details: { ...details } } : {}),
    ...(asStringArray(value.badges) ? { badges: asStringArray(value.badges) } : {}),
    ...(actionLabel && actionHref ? { action: { label: actionLabel, href: actionHref } } : {}),
    ...(normalizePosition(value.position) ? { position: normalizePosition(value.position) } : {}),
    // Keep normalization deterministic if a caller accidentally supplies an
    // empty/invalid array item before this node.
    ...(!Number.isSafeInteger(index) ? {} : {}),
  };
}

function normalizeEdge(value: Record<string, unknown>, index: number): GraphEdge | null {
  const source = asIdentityString(value.source);
  const target = asIdentityString(value.target);
  if (!source || !target) return null;
  const label = asString(value.label);
  const kind = asIdentityString(value.kind) ?? "relationship";
  const details = asRecord(value.details);
  return {
    id: asIdentityString(value.id) ?? stableGraphId("edge", { source, target, label, kind, index }),
    source,
    target,
    ...(label ? { label } : {}),
    kind,
    ...(asString(value.status) ? { status: asString(value.status) as GraphStatus } : {}),
    ...(details ? { details: { ...details } } : {}),
  };
}

function normalizeCount(value: unknown): GraphCount | undefined {
  const record = asRecord(value);
  const total = asFiniteNumber(record?.total);
  const returned = asFiniteNumber(record?.returned);
  return total === undefined && returned === undefined
    ? undefined
    : { total: total ?? returned ?? 0, returned: returned ?? total ?? 0 };
}

function normalizeCoverage(value: unknown): string[] | Record<string, unknown> | undefined {
  const strings = asStringArray(value);
  if (strings) return strings;
  const record = asRecord(value);
  return record ? { ...record } : undefined;
}

function normalizeTimeWindow(value: unknown): GraphTimeWindow | undefined {
  const record = asRecord(value);
  if (!record) return undefined;
  const start = asString(record.start) ?? asString(record.first_seen) ?? asString(record.from);
  const end = asString(record.end) ?? asString(record.last_seen) ?? asString(record.to);
  return {
    ...record,
    ...(start ? { start } : {}),
    ...(end ? { end } : {}),
  };
}

function normalizeMetadata(
  raw: Record<string, unknown> | null,
  root: Record<string, unknown>,
  nodeCount: number,
  edgeCount: number,
): GraphMetadata {
  const metadata = raw ?? {};
  const rawCounts = asRecord(metadata.counts);
  const nodeCounts = normalizeCount(rawCounts?.nodes);
  const edgeCounts = normalizeCount(rawCounts?.edges);
  const title = asString(metadata.title) ?? asString(root.title) ?? "Graph";
  const description = asString(metadata.description) ?? asString(root.description);
  const totalNodes =
    asFiniteNumber(metadata.totalNodes)
    ?? asFiniteNumber(metadata.total_nodes)
    ?? nodeCounts?.total
    ?? nodeCount;
  const returnedNodes =
    asFiniteNumber(metadata.returnedNodes)
    ?? asFiniteNumber(metadata.returned_nodes)
    ?? nodeCounts?.returned
    ?? nodeCount;
  const totalEdges =
    asFiniteNumber(metadata.totalEdges)
    ?? asFiniteNumber(metadata.total_edges)
    ?? edgeCounts?.total
    ?? edgeCount;
  const returnedEdges =
    asFiniteNumber(metadata.returnedEdges)
    ?? asFiniteNumber(metadata.returned_edges)
    ?? edgeCounts?.returned
    ?? edgeCount;
  const blindSpots =
    asStringArray(metadata.blindSpots)
    ?? asStringArray(metadata.blind_spots);
  const limitations = asStringArray(metadata.limitations);
  const semantics = asRecord(metadata.semantics);
  const generatedAt =
    asString(metadata.generatedAt)
    ?? asString(metadata.generated_at)
    ?? asString(root.generatedAt)
    ?? asString(root.generated_at);
  const timeWindow =
    normalizeTimeWindow(metadata.timeWindow)
    ?? normalizeTimeWindow(metadata.time_window);
  const evidenceCount =
    asFiniteNumber(metadata.evidenceCount)
    ?? asFiniteNumber(metadata.evidence_count);
  const declaredAppCount =
    asFiniteNumber(metadata.declaredAppCount)
    ?? asFiniteNumber(metadata.declared_app_count);
  const workflowType =
    asString(metadata.workflowType)
    ?? asString(metadata.workflow_type);
  const workflowVersion =
    asFiniteNumber(metadata.workflowVersion)
    ?? asFiniteNumber(metadata.workflow_version);
  const descriptorVersion =
    asFiniteNumber(metadata.descriptorVersion)
    ?? asFiniteNumber(metadata.descriptor_version);
  const descriptorWorkflowVersion =
    asFiniteNumber(metadata.descriptorWorkflowVersion)
    ?? asFiniteNumber(metadata.descriptor_workflow_version);
  const workflowVersionMismatch =
    asBoolean(metadata.workflowVersionMismatch)
    ?? asBoolean(metadata.workflow_version_mismatch);
  const detailTruncation =
    asRecord(metadata.detailTruncation)
    ?? asRecord(metadata.detail_truncation);
  return {
    title,
    ...(description ? { description } : {}),
    counts: {
      nodes: nodeCounts ?? { total: totalNodes, returned: returnedNodes },
      edges: edgeCounts ?? { total: totalEdges, returned: returnedEdges },
    },
    totalNodes,
    returnedNodes,
    totalEdges,
    returnedEdges,
    ...(asBoolean(metadata.truncated) !== undefined
      ? { truncated: asBoolean(metadata.truncated) }
      : { truncated: returnedNodes < totalNodes || returnedEdges < totalEdges }),
    ...(timeWindow ? { timeWindow } : {}),
    ...(normalizeCoverage(metadata.coverage) ? { coverage: normalizeCoverage(metadata.coverage) } : {}),
    ...(blindSpots ? { blindSpots } : {}),
    ...(limitations ? { limitations } : {}),
    ...(semantics ? { semantics: { ...semantics } } : {}),
    ...(evidenceCount !== undefined ? { evidenceCount } : {}),
    ...(declaredAppCount !== undefined ? { declaredAppCount } : {}),
    ...(generatedAt ? { generatedAt } : {}),
    ...(asString(metadata.error) ? { error: asString(metadata.error) } : {}),
    ...(workflowType ? { workflowType } : {}),
    ...(workflowVersion !== undefined ? { workflowVersion } : {}),
    ...(descriptorVersion !== undefined ? { descriptorVersion } : {}),
    ...(descriptorWorkflowVersion !== undefined ? { descriptorWorkflowVersion } : {}),
    ...(workflowVersionMismatch !== undefined ? { workflowVersionMismatch } : {}),
    ...(detailTruncation ? { detailTruncation: { ...detailTruncation } } : {}),
  };
}

export function normalizeGraphDataset(value: unknown, fallbackTitle = "Graph"): GraphDataset {
  const root = asRecord(value) ?? {};
  const nodes = asRecordArray(root.nodes)
    .map(normalizeNode)
    .filter((node): node is GraphNode => node !== null)
    .sort((left, right) => left.id.localeCompare(right.id));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = asRecordArray(root.edges)
    .map(normalizeEdge)
    .filter((edge): edge is GraphEdge =>
      edge !== null && nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .sort((left, right) => left.id.localeCompare(right.id));
  const normalizedRoot = asString(root.title) ? root : { ...root, title: fallbackTitle };
  const metadata = normalizeMetadata(asRecord(root.metadata), normalizedRoot, nodes.length, edges.length);
  return {
    version: asFiniteNumber(root.version) ?? GRAPH_DATASET_VERSION,
    title: asString(root.title) ?? metadata.title,
    ...(asString(root.description) || metadata.description
      ? { description: asString(root.description) ?? metadata.description }
      : {}),
    nodes,
    edges,
    metadata,
  };
}

export function normalizeGraphExplorerPayload(value: unknown): GraphExplorerPayload {
  const root = asRecord(value) ?? {};
  const generatedAt = asString(root.generatedAt) ?? asString(root.generated_at);
  return {
    version: asFiniteNumber(root.version) ?? GRAPH_DATASET_VERSION,
    ...(generatedAt ? { generatedAt } : {}),
    ontology: normalizeGraphDataset(root.ontology, "Ontology"),
    knowledgeGraph: normalizeGraphDataset(
      root.knowledgeGraph ?? root.knowledge_graph,
      "Knowledge graph",
    ),
  };
}

function searchableNodeText(node: GraphNode): string {
  return [
    node.id,
    node.label,
    node.kind,
    node.status ?? "",
    node.summary ?? "",
    node.badges?.join(" ") ?? "",
    stableJson(node.details ?? {}),
  ].join(" ").toLocaleLowerCase();
}

export function filterGraphDataset(dataset: GraphDataset, filter: GraphFilter = {}): GraphDataset {
  const query = filter.query?.trim().toLocaleLowerCase() ?? "";
  const enabledKinds = filter.kinds === undefined ? null : new Set(filter.kinds);
  let nodes = dataset.nodes.filter((node) =>
    (enabledKinds === null || enabledKinds.has(node.kind))
    && (!query || searchableNodeText(node).includes(query)));
  const visibleIds = new Set(nodes.map((node) => node.id));
  let edges = dataset.edges.filter((edge) =>
    visibleIds.has(edge.source) && visibleIds.has(edge.target));

  if (filter.focusNodeId && visibleIds.has(filter.focusNodeId)) {
    const focusedIds = new Set([filter.focusNodeId]);
    for (const edge of edges) {
      if (edge.source === filter.focusNodeId) focusedIds.add(edge.target);
      if (edge.target === filter.focusNodeId) focusedIds.add(edge.source);
    }
    nodes = nodes.filter((node) => focusedIds.has(node.id));
    edges = edges.filter((edge) =>
      focusedIds.has(edge.source) && focusedIds.has(edge.target));
  }
  return { ...dataset, nodes, edges };
}

function stableSourceComponentRoots(
  ids: string[],
  outgoing: Map<string, string[]>,
): string[] {
  const idSet = new Set(ids);
  const indexes = new Map<string, number>();
  const lowLinks = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const components: string[][] = [];
  let nextIndex = 0;

  const visit = (id: string): void => {
    indexes.set(id, nextIndex);
    lowLinks.set(id, nextIndex);
    nextIndex += 1;
    stack.push(id);
    onStack.add(id);

    for (const target of outgoing.get(id) ?? []) {
      if (!idSet.has(target)) continue;
      if (!indexes.has(target)) {
        visit(target);
        lowLinks.set(id, Math.min(lowLinks.get(id) ?? 0, lowLinks.get(target) ?? 0));
      } else if (onStack.has(target)) {
        lowLinks.set(id, Math.min(lowLinks.get(id) ?? 0, indexes.get(target) ?? 0));
      }
    }

    if (lowLinks.get(id) !== indexes.get(id)) return;
    const component: string[] = [];
    while (stack.length > 0) {
      const member = stack.pop();
      if (!member) break;
      onStack.delete(member);
      component.push(member);
      if (member === id) break;
    }
    component.sort((left, right) => left.localeCompare(right));
    components.push(component);
  };

  for (const id of ids) {
    if (!indexes.has(id)) visit(id);
  }

  const componentById = new Map<string, number>();
  components.forEach((component, componentIndex) => {
    for (const id of component) componentById.set(id, componentIndex);
  });
  const componentIndegrees = components.map(() => 0);
  const componentEdges = new Set<string>();
  for (const source of ids) {
    const sourceComponent = componentById.get(source);
    for (const target of outgoing.get(source) ?? []) {
      if (!idSet.has(target)) continue;
      const targetComponent = componentById.get(target);
      if (
        sourceComponent === undefined
        || targetComponent === undefined
        || sourceComponent === targetComponent
      ) continue;
      const edgeKey = `${sourceComponent}:${targetComponent}`;
      if (componentEdges.has(edgeKey)) continue;
      componentEdges.add(edgeKey);
      componentIndegrees[targetComponent] += 1;
    }
  }

  return components
    .filter((_, componentIndex) => componentIndegrees[componentIndex] === 0)
    .map((component) => component[0])
    .sort((left, right) => left.localeCompare(right));
}

/**
 * A small deterministic layered layout. It deliberately has no DOM or storage
 * dependency, so API snapshots and tests produce stable coordinates. Ranks are
 * shortest-path distances from stable roots, which keeps cycles distributed
 * across useful layers without dropping their back edges.
 */
export function deterministicGraphLayout(
  dataset: GraphDataset,
  direction: GraphLayoutDirection = "LR",
): GraphDataset {
  const ids = [...dataset.nodes.map((node) => node.id)].sort((left, right) => left.localeCompare(right));
  const idSet = new Set(ids);
  const linkedIds = new Set<string>();
  const outgoing = new Map(ids.map((id) => [id, [] as string[]]));
  const indegree = new Map(ids.map((id) => [id, 0]));
  for (const edge of [...dataset.edges].sort((left, right) => left.id.localeCompare(right.id))) {
    if (!idSet.has(edge.source) || !idSet.has(edge.target) || edge.source === edge.target) continue;
    linkedIds.add(edge.source);
    linkedIds.add(edge.target);
    const targets = outgoing.get(edge.source);
    if (!targets?.includes(edge.target)) {
      targets?.push(edge.target);
      indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    }
  }
  for (const targets of outgoing.values()) targets.sort((left, right) => left.localeCompare(right));

  const ranks = new Map<string, number>();
  const assignShortestRanks = (roots: string[]): void => {
    const queue: string[] = [];
    for (const root of roots.sort((left, right) => left.localeCompare(right))) {
      if (ranks.has(root)) continue;
      ranks.set(root, 0);
      queue.push(root);
    }
    let cursor = 0;
    while (cursor < queue.length) {
      const id = queue[cursor];
      cursor += 1;
      const nextRank = (ranks.get(id) ?? 0) + 1;
      for (const target of outgoing.get(id) ?? []) {
        if (ranks.has(target)) continue;
        ranks.set(target, nextRank);
        queue.push(target);
      }
    }
  };

  assignShortestRanks(ids.filter((id) => linkedIds.has(id) && indegree.get(id) === 0));

  // A component made entirely of cycles has no zero-indegree node. Collapse
  // the still-unreached subgraph conceptually into SCCs, choose the stable
  // representative of every source SCC, then continue the same BFS. Downstream
  // SCCs retain their distance from those roots instead of being flattened.
  const unreachableIds = ids.filter((id) => linkedIds.has(id) && !ranks.has(id));
  if (unreachableIds.length > 0) {
    assignShortestRanks(stableSourceComponentRoots(unreachableIds, outgoing));
  }

  const layers = new Map<number, string[]>();
  for (const id of ids) {
    if (!linkedIds.has(id)) continue;
    const rank = ranks.get(id) ?? 0;
    const layer = layers.get(rank) ?? [];
    layer.push(id);
    layers.set(rank, layer);
  }
  const positions = new Map<string, GraphPosition>();
  let nextPrimaryPosition = 0;
  const maxCrossAxisNodes = 5;
  for (const [, layer] of [...layers.entries()].sort(([left], [right]) => left - right)) {
    layer.sort((left, right) => left.localeCompare(right));
    const primaryGroups = Math.ceil(layer.length / maxCrossAxisNodes);
    for (let index = 0; index < layer.length; index += 1) {
      const primaryGroup = Math.floor(index / maxCrossAxisNodes);
      const crossIndex = index % maxCrossAxisNodes;
      const groupSize = Math.min(
        maxCrossAxisNodes,
        layer.length - primaryGroup * maxCrossAxisNodes,
      );
      const crossAxis = (crossIndex - (groupSize - 1) / 2) * 148;
      positions.set(
        layer[index],
        direction === "LR"
          ? { x: nextPrimaryPosition + primaryGroup * 286, y: crossAxis }
          : { x: crossAxis * 1.35, y: nextPrimaryPosition + primaryGroup * 176 },
      );
    }
    nextPrimaryPosition += primaryGroups * (direction === "LR" ? 286 : 176);
  }

  // Records without relationships are common in bounded knowledge-graph
  // snapshots. Keeping all of them in rank zero creates a single unbounded
  // strip, so place that deterministic subset in a compact orientation-aware
  // grid after the connected topology.
  const isolatedIds = ids.filter((id) => !linkedIds.has(id));
  if (isolatedIds.length > 0) {
    const connectedPositions = [...positions.values()];
    if (direction === "LR") {
      const rows = Math.ceil(Math.sqrt(isolatedIds.length));
      const startX = connectedPositions.length > 0
        ? Math.max(...connectedPositions.map(({ x }) => x)) + 286
        : 0;
      const centerY = connectedPositions.length > 0
        ? (Math.min(...connectedPositions.map(({ y }) => y))
          + Math.max(...connectedPositions.map(({ y }) => y))) / 2
        : 0;
      for (let index = 0; index < isolatedIds.length; index += 1) {
        const column = Math.floor(index / rows);
        const row = index % rows;
        const columnSize = Math.min(rows, isolatedIds.length - column * rows);
        positions.set(isolatedIds[index], {
          x: startX + column * 286,
          y: centerY + (row - (columnSize - 1) / 2) * 148,
        });
      }
    } else {
      const columns = Math.ceil(Math.sqrt(isolatedIds.length));
      const startY = connectedPositions.length > 0
        ? Math.max(...connectedPositions.map(({ y }) => y)) + 176
        : 0;
      const centerX = connectedPositions.length > 0
        ? (Math.min(...connectedPositions.map(({ x }) => x))
          + Math.max(...connectedPositions.map(({ x }) => x))) / 2
        : 0;
      for (let index = 0; index < isolatedIds.length; index += 1) {
        const row = Math.floor(index / columns);
        const column = index % columns;
        const rowSize = Math.min(columns, isolatedIds.length - row * columns);
        positions.set(isolatedIds[index], {
          x: centerX + (column - (rowSize - 1) / 2) * 200,
          y: startY + row * 176,
        });
      }
    }
  }

  return {
    ...dataset,
    nodes: dataset.nodes.map((node) => ({
      ...node,
      position: positions.get(node.id) ?? { x: 0, y: 0 },
    })),
  };
}

export function graphNodeKinds(dataset: GraphDataset): string[] {
  return [...new Set(dataset.nodes.map((node) => node.kind))]
    .sort((left, right) => left.localeCompare(right));
}

export function graphStatusLabel(status: GraphStatus | undefined): string {
  const labels: Record<string, string> = {
    default: "Default",
    existing: "Existing",
    not_started: "Not started",
    running: "Running",
    waiting: "Waiting for user",
    waiting_user: "Waiting for user",
    needs_attention: "Needs attention",
    succeeded: "Succeeded",
    failed: "Failed",
    cancelled: "Cancelled",
    warning: "Warning",
    error: "Error",
    observed: "Observed",
    declared: "Declared",
    unknown: "Unknown",
  };
  return status ? labels[status] ?? status.replaceAll("_", " ") : "Default";
}

export function graphDetailText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(graphDetailText).join(", ");
  return stableJson(value);
}
