import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  applyEdgeChanges,
  applyNodeChanges,
  type Edge as FlowEdge,
  type EdgeChange,
  type Node as FlowNode,
  type NodeChange,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  deterministicGraphLayout,
  filterGraphDataset,
  graphDetailText,
  graphNodeKinds,
  graphStatusLabel,
  type GraphDataset,
  type GraphEdge,
  type GraphLayoutDirection,
  type GraphNode,
} from "../../lib/graphVisualization";
import "./GraphExplorer.css";

// JSDOM and older embedded WebViews can omit ResizeObserver. React Flow uses
// it for measurements; a no-op observer preserves the semantic fallback and
// empty/detail UI until a real viewport measurement is available.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class implements ResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}

export interface GraphExplorerProps {
  dataset: GraphDataset;
  className?: string;
  initialDirection?: GraphLayoutDirection;
  compact?: boolean;
  ariaLabel?: string;
  error?: string;
}

type ExplorerFlowNode = FlowNode<{
  label: ReactNode;
  labelText: string;
  graphNode: GraphNode;
}>;

type ExplorerFlowEdge = FlowEdge<{ graphEdge: GraphEdge }>;

type GraphSelection =
  | { type: "node"; id: string }
  | { type: "edge"; id: string }
  | null;

const statusSymbol = (status: string | undefined): string => {
  if (status === "succeeded" || status === "existing" || status === "observed") return "✓";
  if (status === "running") return "↻";
  if (status === "waiting" || status === "waiting_user" || status === "declared") return "◷";
  if (status === "failed" || status === "error") return "!";
  if (status === "warning" || status === "needs_attention" || status === "unknown") return "?";
  if (status === "cancelled") return "×";
  return "○";
};

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = (): void => setReduced(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

function GraphNodeLabel({ node }: { node: GraphNode }) {
  const status = graphStatusLabel(node.status);
  return (
    <div className="graph-explorer-node-label">
      <span
        aria-hidden="true"
        className={`graph-explorer-status-symbol is-${node.status ?? "default"}`}
      >
        {statusSymbol(node.status)}
      </span>
      <span>
        <strong>{node.label}</strong>
        <small>{node.kind} · {status}</small>
      </span>
    </div>
  );
}

function toFlowNodes(
  dataset: GraphDataset,
  direction: GraphLayoutDirection,
): ExplorerFlowNode[] {
  return dataset.nodes.map((node) => ({
    id: node.id,
    position: node.position ?? { x: 0, y: 0 },
    type: "default",
    initialWidth: 210,
    initialHeight: 58,
    sourcePosition: direction === "LR" ? Position.Right : Position.Bottom,
    targetPosition: direction === "LR" ? Position.Left : Position.Top,
    data: {
      label: <GraphNodeLabel node={node} />,
      labelText: node.label,
      graphNode: node,
    },
    ariaLabel: `${node.label}, ${node.kind}, ${graphStatusLabel(node.status)}`,
    ariaRole: "button",
    focusable: true,
    className: [
      "graph-explorer-flow-node",
      `is-status-${node.status ?? "default"}`,
      `is-kind-${node.kind.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`,
    ].join(" "),
  }));
}

function toFlowEdges(
  dataset: GraphDataset,
  selection: GraphSelection,
  reducedMotion: boolean,
): ExplorerFlowEdge[] {
  const nodes = new Map(dataset.nodes.map((node) => [node.id, node]));
  return dataset.edges.map((edge) => {
    const source = nodes.get(edge.source)?.label ?? edge.source;
    const target = nodes.get(edge.target)?.label ?? edge.target;
    const label = edge.label || edge.kind;
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label,
      ariaLabel: `${label}: ${source} to ${target}, ${graphStatusLabel(edge.status)}`,
      ariaRole: "button",
      data: { graphEdge: edge },
      markerEnd: { type: MarkerType.ArrowClosed },
      animated: !reducedMotion && edge.status === "running",
      className: [
        "graph-explorer-flow-edge",
        `is-status-${edge.status ?? "default"}`,
        selection?.type === "edge" && selection.id === edge.id ? "is-selected" : "",
      ].join(" "),
      focusable: true,
      selectable: true,
      selected: selection?.type === "edge" && selection.id === edge.id,
    };
  });
}

function DetailRows({ details }: { details: Record<string, unknown> | undefined }) {
  if (!details || Object.keys(details).length === 0) return null;
  return (
    <dl className="graph-explorer-detail-list">
      {Object.entries(details)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, value]) => (
          <div key={key}>
            <dt>{key.replaceAll("_", " ")}</dt>
            <dd>{graphDetailText(value)}</dd>
          </div>
        ))}
    </dl>
  );
}

function GraphExplorerInner({
  dataset,
  className,
  initialDirection = "LR",
  compact = false,
  ariaLabel = "Interactive graph explorer",
  error,
}: GraphExplorerProps) {
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState<GraphLayoutDirection>(initialDirection);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const [enabledKinds, setEnabledKinds] = useState<Set<string>>(
    () => new Set(graphNodeKinds(dataset)),
  );
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [selection, setSelection] = useState<GraphSelection>(null);
  const instanceRef = useRef<ReactFlowInstance<ExplorerFlowNode, ExplorerFlowEdge> | null>(null);
  const reducedMotion = usePrefersReducedMotion();
  const fitGraph = useCallback(() => {
    const fit = (): void => {
      void instanceRef.current?.fitView({ padding: 0.18, duration: reducedMotion ? 0 : 240 });
    };
    if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(fit);
    else fit();
  }, [reducedMotion]);
  const kinds = useMemo(() => graphNodeKinds(dataset), [dataset]);
  const kindsSignature = JSON.stringify(kinds);
  const previousKindsRef = useRef({
    signature: kindsSignature,
    values: new Set(kinds),
  });

  useEffect(() => {
    const previous = previousKindsRef.current;
    if (previous.signature === kindsSignature) return;
    previousKindsRef.current = {
      signature: kindsSignature,
      values: new Set(kinds),
    };
    setEnabledKinds((current) => new Set(
      kinds.filter((kind) => current.has(kind) || !previous.values.has(kind)),
    ));
  }, [kinds, kindsSignature]);

  const filtered = useMemo(
    () => filterGraphDataset(dataset, { query, kinds: enabledKinds, focusNodeId }),
    [dataset, enabledKinds, focusNodeId, query],
  );
  const laidOut = useMemo(
    () => deterministicGraphLayout(filtered, direction),
    [direction, filtered],
  );
  const layoutIntentKey = JSON.stringify({
    direction,
    query,
    enabledKinds: [...enabledKinds].sort((left, right) => left.localeCompare(right)),
    focusNodeId,
    nodes: filtered.nodes.map((node) => node.id).sort((left, right) => left.localeCompare(right)),
    edges: filtered.edges
      .map((edge) => [edge.source, edge.target])
      .sort(([leftSource, leftTarget], [rightSource, rightTarget]) => (
        leftSource.localeCompare(rightSource) || leftTarget.localeCompare(rightTarget)
      )),
    revision: layoutRevision,
  });
  const projectedNodes = useMemo(
    () => toFlowNodes(laidOut, direction),
    [direction, laidOut],
  );
  const projectedEdges = useMemo(
    () => toFlowEdges(laidOut, selection, reducedMotion),
    [laidOut, reducedMotion, selection],
  );
  const [flowNodes, setFlowNodes] = useState<ExplorerFlowNode[]>(projectedNodes);
  const [flowEdges, setFlowEdges] = useState<ExplorerFlowEdge[]>(projectedEdges);
  const previousLayoutIntentRef = useRef(layoutIntentKey);
  const visibleFlowNodes = useMemo(
    () => flowNodes.map((node) => {
      const selected = selection?.type === "node" && selection.id === node.id;
      return node.selected === selected ? node : { ...node, selected };
    }),
    [flowNodes, selection],
  );

  useEffect(() => {
    const shouldRelayout = previousLayoutIntentRef.current !== layoutIntentKey;
    previousLayoutIntentRef.current = layoutIntentKey;
    setFlowNodes((current) => {
      if (shouldRelayout) return projectedNodes;
      const currentById = new Map(current.map((node) => [node.id, node]));
      return projectedNodes.map((node) => {
        const previous = currentById.get(node.id);
        return previous ? {
          ...node,
          position: previous.position,
          ...(previous.dragging === undefined ? {} : { dragging: previous.dragging }),
        } : node;
      });
    });
  }, [layoutIntentKey, projectedNodes]);
  useEffect(() => setFlowEdges(projectedEdges), [projectedEdges]);
  useEffect(() => {
    fitGraph();
  }, [fitGraph, layoutIntentKey]);
  useEffect(() => {
    const nodeIds = new Set(dataset.nodes.map((node) => node.id));
    const edgeIds = new Set(dataset.edges.map((edge) => edge.id));
    setSelection((current) => {
      if (
        (current?.type === "node" && !nodeIds.has(current.id))
        || (current?.type === "edge" && !edgeIds.has(current.id))
      ) return null;
      return current;
    });
    setFocusNodeId((current) => current && !nodeIds.has(current) ? null : current);
  }, [dataset.edges, dataset.nodes]);

  const onNodesChange = useCallback((changes: NodeChange<ExplorerFlowNode>[]) => {
    setFlowNodes((current) => applyNodeChanges(changes, current));
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange<ExplorerFlowEdge>[]) => {
    setFlowEdges((current) => applyEdgeChanges(changes, current));
  }, []);

  const selectDirection = (nextDirection: GraphLayoutDirection): void => {
    setDirection(nextDirection);
  };

  const showOverview = (): void => {
    setQuery("");
    setEnabledKinds(new Set(kinds));
    setFocusNodeId(null);
    setSelection(null);
    setLayoutRevision((current) => current + 1);
  };

  const toggleKind = (kind: string): void => {
    setEnabledKinds((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const selectedNode = selection?.type === "node"
    ? dataset.nodes.find((node) => node.id === selection.id)
    : undefined;
  const selectedEdge = selection?.type === "edge"
    ? dataset.edges.find((edge) => edge.id === selection.id)
    : undefined;
  const selectedEdgeSource = selectedEdge
    ? dataset.nodes.find((node) => node.id === selectedEdge.source)
    : undefined;
  const selectedEdgeTarget = selectedEdge
    ? dataset.nodes.find((node) => node.id === selectedEdge.target)
    : undefined;
  const graphError = error ?? dataset.metadata.error;
  const totalNodes = dataset.metadata.totalNodes
    ?? dataset.metadata.counts?.nodes?.total
    ?? dataset.nodes.length;
  const returnedNodes = dataset.metadata.returnedNodes
    ?? dataset.metadata.counts?.nodes?.returned
    ?? dataset.nodes.length;
  const totalEdges = dataset.metadata.totalEdges
    ?? dataset.metadata.counts?.edges?.total
    ?? dataset.edges.length;
  const returnedEdges = dataset.metadata.returnedEdges
    ?? dataset.metadata.counts?.edges?.returned
    ?? dataset.edges.length;
  const detailValuesTruncated = dataset.metadata.detailTruncation?.truncated === true;
  const detailTruncationCount = dataset.metadata.detailTruncation?.values_truncated;

  return (
    <section
      aria-label={ariaLabel}
      className={[
        "graph-explorer",
        compact ? "is-compact" : "",
        className ?? "",
      ].filter(Boolean).join(" ")}
      data-layout={direction}
    >
      <header className="graph-explorer-header">
        <div>
          <h3>{dataset.metadata.title || dataset.title || "Graph"}</h3>
          {(dataset.metadata.description || dataset.description) && (
            <p>{dataset.metadata.description || dataset.description}</p>
          )}
        </div>
        <div className="graph-explorer-layout-controls" aria-label="Graph layout">
          <button
            aria-pressed={direction === "LR"}
            onClick={() => selectDirection("LR")}
            type="button"
          >
            Horizontal layout
          </button>
          <button
            aria-pressed={direction === "TB"}
            onClick={() => selectDirection("TB")}
            type="button"
          >
            Vertical layout
          </button>
          <button onClick={showOverview} type="button">Reset view</button>
        </div>
      </header>

      <div className="graph-explorer-toolbar">
        <label className="graph-explorer-search">
          <span>Search graph</span>
          <input
            aria-label="Search graph"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Name, kind, summary, or detail"
            type="search"
            value={query}
          />
        </label>
        <fieldset>
          <legend>Node kinds</legend>
          <div>
            {kinds.map((kind) => (
              <label key={kind}>
                <input
                  aria-label={kind}
                  checked={enabledKinds.has(kind)}
                  onChange={() => toggleKind(kind)}
                  type="checkbox"
                />
                <span>{kind}</span>
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      {graphError && (
        <div className="graph-explorer-alert is-error" role="alert">
          <strong>Unable to load graph</strong>
          <span>{graphError}</span>
        </div>
      )}
      {dataset.metadata.truncated && (
        <div className="graph-explorer-alert is-truncated" role="status">
          Snapshot truncated: showing {returnedNodes} of {totalNodes} nodes and {returnedEdges} of{" "}
          {totalEdges} relationships. Explore this as a limited snapshot, not the complete graph.
        </div>
      )}
      {detailValuesTruncated && (
        <div className="graph-explorer-alert is-truncated" role="status">
          Some node or relationship details were shortened to keep this snapshot bounded
          {typeof detailTruncationCount === "number"
            ? ` (${detailTruncationCount} values)`
            : ""}. Topology counts are reported separately.
        </div>
      )}

      <div className="graph-explorer-content">
        <div className="graph-explorer-canvas">
          <ReactFlow<ExplorerFlowNode, ExplorerFlowEdge>
            edges={flowEdges}
            edgesFocusable
            elementsSelectable
            fitView
            fitViewOptions={{ padding: 0.18 }}
            maxZoom={2.4}
            minZoom={0.15}
            multiSelectionKeyCode={["Meta", "Control"]}
            nodes={visibleFlowNodes}
            nodesConnectable={false}
            nodesDraggable
            nodesFocusable
            onEdgesChange={onEdgesChange}
            onEdgeClick={(_event, edge) => setSelection({ type: "edge", id: edge.id })}
            onInit={(instance) => {
              instanceRef.current = instance;
            }}
            onNodeClick={(_event, node) => setSelection({ type: "node", id: node.id })}
            onNodesChange={onNodesChange}
            panOnDrag={[1, 2]}
            selectionMode={SelectionMode.Partial}
            selectionOnDrag
            zoomOnDoubleClick
            zoomOnPinch
            zoomOnScroll
          >
            <Background gap={20} size={1} />
            <MiniMap
              ariaLabel="Graph minimap"
              maskColor="color-mix(in srgb, var(--surface-panel-solid, #111827), transparent 20%)"
              nodeColor="var(--accent, #6366f1)"
              pannable
              zoomable
            />
            <Controls
              fitViewOptions={{ padding: 0.18 }}
              position="bottom-left"
              showInteractive={false}
            />
          </ReactFlow>

          {dataset.nodes.length === 0 && !graphError && (
            <div className="graph-explorer-empty">
              <strong>No graph data</strong>
              <span>This scene does not contain any nodes yet.</span>
            </div>
          )}
          {dataset.nodes.length > 0 && filtered.nodes.length === 0 && (
            <div className="graph-explorer-empty">
              <strong>No nodes match the current search and filters</strong>
              <button
                onClick={() => {
                  setQuery("");
                  setEnabledKinds(new Set(kinds));
                  setFocusNodeId(null);
                }}
                type="button"
              >
                Clear filters
              </button>
            </div>
          )}
        </div>

        <aside aria-label="Graph details" className="graph-explorer-details">
          {selectedNode ? (
            <>
              <div className="graph-explorer-detail-heading">
                <span
                  aria-hidden="true"
                  className={`graph-explorer-status-symbol is-${selectedNode.status ?? "default"}`}
                >
                  {statusSymbol(selectedNode.status)}
                </span>
                <div>
                  <h4>{selectedNode.label}</h4>
                  <p>{selectedNode.kind} · {graphStatusLabel(selectedNode.status)}</p>
                </div>
              </div>
              {selectedNode.summary && <p className="graph-explorer-summary">{selectedNode.summary}</p>}
              {selectedNode.badges && selectedNode.badges.length > 0 && (
                <ul className="graph-explorer-badges" aria-label="Node badges">
                  {selectedNode.badges.map((badge) => <li key={badge}>{badge}</li>)}
                </ul>
              )}
              <DetailRows details={selectedNode.details} />
              <div className="graph-explorer-detail-actions">
                <button onClick={() => setFocusNodeId(selectedNode.id)} type="button">
                  Focus one-hop neighbors
                </button>
                {selectedNode.action && (
                  <a href={selectedNode.action.href} rel="noreferrer" target="_blank">
                    {selectedNode.action.label}
                  </a>
                )}
              </div>
            </>
          ) : selectedEdge ? (
            <>
              <div className="graph-explorer-detail-heading">
                <span
                  aria-hidden="true"
                  className={`graph-explorer-status-symbol is-${selectedEdge.status ?? "default"}`}
                >
                  {statusSymbol(selectedEdge.status)}
                </span>
                <div>
                  <h4>{selectedEdge.label || selectedEdge.kind}</h4>
                  <p>{selectedEdge.kind} · {graphStatusLabel(selectedEdge.status)}</p>
                </div>
              </div>
              <dl className="graph-explorer-detail-list">
                <div>
                  <dt>From</dt>
                  <dd>{selectedEdgeSource?.label ?? selectedEdge.source}</dd>
                </div>
                <div>
                  <dt>To</dt>
                  <dd>{selectedEdgeTarget?.label ?? selectedEdge.target}</dd>
                </div>
              </dl>
              <DetailRows details={selectedEdge.details} />
            </>
          ) : (
            <div className="graph-explorer-inspection-hint">
              <strong>Inspect the graph</strong>
              <span>Select a node or edge to see its status, evidence, and source details.</span>
            </div>
          )}

          {(dataset.metadata.timeWindow
            || dataset.metadata.coverage
            || dataset.metadata.blindSpots?.length
            || dataset.metadata.limitations?.length
            || dataset.metadata.semantics) && (
            <section aria-label="Graph coverage" className="graph-explorer-coverage">
              <h5>Coverage and limits</h5>
              {dataset.metadata.timeWindow && (
                <div>
                  <strong>Time window</strong>
                  <span>
                    {dataset.metadata.timeWindow.start || "Unknown start"}
                    {" — "}
                    {dataset.metadata.timeWindow.end || "Unknown end"}
                  </span>
                </div>
              )}
              {dataset.metadata.coverage && (
                <div>
                  <strong>Coverage</strong>
                  <span>{graphDetailText(dataset.metadata.coverage)}</span>
                </div>
              )}
              {dataset.metadata.semantics && (
                <div>
                  <strong>Semantics</strong>
                  <span>{graphDetailText(dataset.metadata.semantics)}</span>
                </div>
              )}
              {dataset.metadata.blindSpots && dataset.metadata.blindSpots.length > 0 && (
                <div>
                  <strong>Known blind spots</strong>
                  <ul>
                    {dataset.metadata.blindSpots.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              )}
              {dataset.metadata.limitations && dataset.metadata.limitations.length > 0 && (
                <div>
                  <strong>Limitations</strong>
                  <ul>
                    {dataset.metadata.limitations.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              )}
            </section>
          )}

          {filtered.edges.length > 0 && (
            <label className="graph-explorer-edge-picker">
              <span>Inspect relationship</span>
              <select
                aria-label="Inspect relationship"
                onChange={(event) => {
                  if (event.target.value) setSelection({ type: "edge", id: event.target.value });
                }}
                value={selection?.type === "edge" ? selection.id : ""}
              >
                <option value="">Choose an edge</option>
                {filtered.edges.map((edge) => {
                  const source = dataset.nodes.find((node) => node.id === edge.source)?.label ?? edge.source;
                  const target = dataset.nodes.find((node) => node.id === edge.target)?.label ?? edge.target;
                  return (
                    <option key={edge.id} value={edge.id}>
                      {edge.label || edge.kind}: {source} → {target}
                    </option>
                  );
                })}
              </select>
            </label>
          )}

          <section aria-label="Graph legend" className="graph-explorer-legend">
            <h5>Legend</h5>
            <div>
              {[...new Set(dataset.nodes.map((node) => node.status ?? "default"))].sort().map((status) => (
                <span key={status}>
                  <i className={`graph-explorer-status-symbol is-${status}`} aria-hidden="true">
                    {statusSymbol(status)}
                  </i>
                  {graphStatusLabel(status)}
                </span>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </section>
  );
}

export function GraphExplorer(props: GraphExplorerProps) {
  return (
    <ReactFlowProvider>
      <GraphExplorerInner {...props} />
    </ReactFlowProvider>
  );
}

export default GraphExplorer;
