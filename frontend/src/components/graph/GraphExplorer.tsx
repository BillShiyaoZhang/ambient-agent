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
  language?: "zh" | "en";
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

const GRAPH_COPY = {
  en: {
    defaultAriaLabel: "Interactive graph explorer",
    defaultTitle: "Graph",
    layoutLabel: "Graph layout",
    horizontalLayout: "Horizontal layout",
    verticalLayout: "Vertical layout",
    resetView: "Reset view",
    enterFullscreen: "Enter fullscreen",
    exitFullscreen: "Exit fullscreen",
    fullscreenStatusLabel: "Fullscreen status",
    fullscreenEntered: "Fullscreen mode. Press Escape to exit.",
    fullscreenFallback:
      "Fullscreen mode (browser fallback). Press Escape to exit.",
    fullscreenExited: "Exited fullscreen mode.",
    fullscreenExitUnavailable:
      "The browser cannot exit fullscreen through this control. Press Escape to exit.",
    searchGraph: "Search graph",
    searchPlaceholder: "Name, kind, summary, or detail",
    nodeKinds: "Node kinds",
    unableToLoad: "Unable to load graph",
    topologyTruncated: (
      returnedNodes: number,
      totalNodes: number,
      returnedEdges: number,
      totalEdges: number,
    ) =>
      `Snapshot truncated: showing ${returnedNodes} of ${totalNodes} nodes and `
      + `${returnedEdges} of ${totalEdges} relationships. Explore this as a limited `
      + "snapshot, not the complete graph.",
    detailsTruncated: (count: number | undefined) =>
      "Some node or relationship details were shortened to keep this snapshot bounded"
      + (typeof count === "number" ? ` (${count} values)` : "")
      + ". Topology counts are reported separately.",
    minimap: "Graph minimap",
    noGraphData: "No graph data",
    noGraphDataDescription: "This scene does not contain any nodes yet.",
    noMatches: "No nodes match the current search and filters",
    clearFilters: "Clear filters",
    graphDetails: "Graph details",
    nodeBadges: "Node badges",
    focusNeighbors: "Focus one-hop neighbors",
    from: "From",
    to: "To",
    inspectGraph: "Inspect the graph",
    inspectionHint:
      "Select a node or edge to see its status, evidence, and source details.",
    graphCoverage: "Graph coverage",
    coverageAndLimits: "Coverage and limits",
    timeWindow: "Time window",
    unknownStart: "Unknown start",
    unknownEnd: "Unknown end",
    coverage: "Coverage",
    semantics: "Semantics",
    blindSpots: "Known blind spots",
    limitations: "Limitations",
    inspectRelationship: "Inspect relationship",
    chooseEdge: "Choose an edge",
    graphLegend: "Graph legend",
    legend: "Legend",
    edgeTo: "to",
    controls: "Graph controls",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    fitView: "Fit view",
    toggleInteractivity: "Toggle interactivity",
    nodeKeyboardDescription:
      "Press Enter or Space to select a node. Use the arrow keys to move it, and Escape to cancel.",
    nodeKeyboardDisabledDescription: "Press Enter or Space to select a node.",
    edgeKeyboardDescription:
      "Press Enter or Space to select an edge, and Escape to cancel.",
    nodeMoved: ({ direction, x, y }: { direction: string; x: number; y: number }) =>
      `Moved selected node ${direction}. New position, x: ${x}, y: ${y}`,
    handle: "Handle",
  },
  zh: {
    defaultAriaLabel: "交互式图谱探索",
    defaultTitle: "图谱",
    layoutLabel: "图谱布局",
    horizontalLayout: "水平布局",
    verticalLayout: "垂直布局",
    resetView: "重置视图",
    enterFullscreen: "进入全屏",
    exitFullscreen: "退出全屏",
    fullscreenStatusLabel: "全屏状态",
    fullscreenEntered: "已进入全屏模式。按 Escape 可退出。",
    fullscreenFallback: "已进入全屏模式（浏览器兼容模式）。按 Escape 可退出。",
    fullscreenExited: "已退出全屏模式。",
    fullscreenExitUnavailable: "浏览器无法通过此控件退出全屏。请按 Escape 退出。",
    searchGraph: "搜索图谱",
    searchPlaceholder: "名称、类型、摘要或详情",
    nodeKinds: "节点类型",
    unableToLoad: "无法加载图谱",
    topologyTruncated: (
      returnedNodes: number,
      totalNodes: number,
      returnedEdges: number,
      totalEdges: number,
    ) =>
      `快照已截断：当前显示 ${totalNodes} 个节点中的 ${returnedNodes} 个，以及 `
      + `${totalEdges} 个关系中的 ${returnedEdges} 个。请将其视为有限快照，而非完整图谱。`,
    detailsTruncated: (count: number | undefined) =>
      "部分节点或关系详情已缩短，以限制快照大小"
      + (typeof count === "number" ? `（${count} 个值）` : "")
      + "。拓扑数量单独报告。",
    minimap: "图谱缩略图",
    noGraphData: "暂无图谱数据",
    noGraphDataDescription: "此场景目前还没有节点。",
    noMatches: "没有节点符合当前搜索和筛选条件",
    clearFilters: "清除筛选",
    graphDetails: "图谱详情",
    nodeBadges: "节点标记",
    focusNeighbors: "聚焦一跳邻居",
    from: "来源",
    to: "目标",
    inspectGraph: "检查图谱",
    inspectionHint: "选择节点或关系以查看其状态、证据和来源详情。",
    graphCoverage: "图谱覆盖范围",
    coverageAndLimits: "覆盖范围与限制",
    timeWindow: "时间窗口",
    unknownStart: "未知起始时间",
    unknownEnd: "未知结束时间",
    coverage: "覆盖范围",
    semantics: "语义",
    blindSpots: "已知盲区",
    limitations: "限制",
    inspectRelationship: "检查关系",
    chooseEdge: "选择一条关系",
    graphLegend: "图谱图例",
    legend: "图例",
    edgeTo: "到",
    controls: "图谱控件",
    zoomIn: "放大",
    zoomOut: "缩小",
    fitView: "适应视图",
    toggleInteractivity: "切换交互模式",
    nodeKeyboardDescription:
      "按 Enter 或空格选择节点。使用方向键移动节点，按 Escape 取消。",
    nodeKeyboardDisabledDescription: "按 Enter 或空格选择节点。",
    edgeKeyboardDescription: "按 Enter 或空格选择关系，按 Escape 取消。",
    nodeMoved: ({ direction, x, y }: { direction: string; x: number; y: number }) => {
      const directionLabel: Record<string, string> = {
        left: "左",
        right: "右",
        up: "上",
        down: "下",
      };
      return `已将所选节点向${directionLabel[direction] ?? direction}移动。`
        + `新位置：x ${x}，y ${y}`;
    },
    handle: "连接点",
  },
} as const;

const GRAPH_STATUS_ZH: Record<string, string> = {
  default: "默认",
  existing: "已存在",
  not_started: "未开始",
  running: "运行中",
  waiting: "等待用户",
  waiting_user: "等待用户",
  needs_attention: "需要处理",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  warning: "警告",
  error: "错误",
  observed: "已观察",
  declared: "已声明",
  unknown: "未知",
  abstract: "抽象",
};

function localizedStatusLabel(
  status: string | undefined,
  language: "zh" | "en",
): string {
  if (language === "en") return graphStatusLabel(status);
  if (!status) return GRAPH_STATUS_ZH.default;
  return GRAPH_STATUS_ZH[status] ?? status.replaceAll("_", " ");
}

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

function GraphNodeLabel({
  node,
  language,
}: {
  node: GraphNode;
  language: "zh" | "en";
}) {
  const status = localizedStatusLabel(node.status, language);
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
  language: "zh" | "en",
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
      label: <GraphNodeLabel language={language} node={node} />,
      labelText: node.label,
      graphNode: node,
    },
    ariaLabel: `${node.label}, ${node.kind}, ${localizedStatusLabel(node.status, language)}`,
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
  language: "zh" | "en",
): ExplorerFlowEdge[] {
  const copy = GRAPH_COPY[language];
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
      ariaLabel:
        `${label}: ${source} ${copy.edgeTo} ${target}, `
        + localizedStatusLabel(edge.status, language),
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

function CoverageValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    return (
      <ul className="graph-explorer-coverage-values">
        {value.map((item, index) => (
          <li key={`${graphDetailText(item)}:${index}`}>
            <CoverageValue value={item} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object" && value !== null) {
    return (
      <dl className="graph-explorer-coverage-fields">
        {Object.entries(value as Record<string, unknown>)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, item]) => (
            <div key={key}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd><CoverageValue value={item} /></dd>
            </div>
          ))}
      </dl>
    );
  }
  return <span>{graphDetailText(value)}</span>;
}

function GraphExplorerInner({
  dataset,
  className,
  initialDirection = "LR",
  compact = false,
  ariaLabel,
  error,
  language = "en",
}: GraphExplorerProps) {
  const copy = GRAPH_COPY[language];
  const resolvedAriaLabel = ariaLabel ?? copy.defaultAriaLabel;
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState<GraphLayoutDirection>(initialDirection);
  const [layoutRevision, setLayoutRevision] = useState(0);
  const [enabledKinds, setEnabledKinds] = useState<Set<string>>(
    () => new Set(graphNodeKinds(dataset)),
  );
  const [focusNodeId, setFocusNodeId] = useState<string | null>(null);
  const [selection, setSelection] = useState<GraphSelection>(null);
  const [fullscreenMode, setFullscreenMode] = useState<"native" | "fallback" | null>(null);
  const [fullscreenAnnouncement, setFullscreenAnnouncement] = useState<
    "entered" | "fallback" | "exited" | "exit-unavailable" | null
  >(null);
  const explorerRef = useRef<HTMLElement | null>(null);
  const fullscreenModeRef = useRef<"native" | "fallback" | null>(null);
  const fullscreenFallbackHostRef = useRef<HTMLElement | null>(null);
  const instanceRef = useRef<ReactFlowInstance<ExplorerFlowNode, ExplorerFlowEdge> | null>(null);
  const reducedMotion = usePrefersReducedMotion();
  const updateFullscreenMode = useCallback(
    (mode: "native" | "fallback" | null): void => {
      fullscreenModeRef.current = mode;
      setFullscreenMode(mode);
    },
    [],
  );
  const fitGraph = useCallback(() => {
    const fit = (): void => {
      void instanceRef.current?.fitView({ padding: 0.18, duration: reducedMotion ? 0 : 240 });
    };
    if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(fit);
    else fit();
  }, [reducedMotion]);
  const releaseFullscreenFallbackHost = useCallback(() => {
    fullscreenFallbackHostRef.current?.classList.remove(
      "has-graph-fullscreen-fallback",
    );
    fullscreenFallbackHostRef.current = null;
  }, []);
  const enterFullscreenFallback = useCallback(() => {
    releaseFullscreenFallbackHost();
    const host = explorerRef.current?.closest<HTMLElement>(
      ".system-dialog, .system-drawer",
    ) ?? null;
    host?.classList.add("has-graph-fullscreen-fallback");
    fullscreenFallbackHostRef.current = host;
    updateFullscreenMode("fallback");
    setFullscreenAnnouncement("fallback");
  }, [releaseFullscreenFallbackHost, updateFullscreenMode]);
  const leaveFullscreen = useCallback(async () => {
    const explorer = explorerRef.current;
    if (fullscreenMode === "fallback") {
      releaseFullscreenFallbackHost();
      updateFullscreenMode(null);
      setFullscreenAnnouncement("exited");
      return;
    }
    if (fullscreenMode !== "native") return;
    if (document.fullscreenElement !== explorer) {
      updateFullscreenMode(null);
      setFullscreenAnnouncement("exited");
      return;
    }
    if (typeof document.exitFullscreen !== "function") {
      setFullscreenAnnouncement("exit-unavailable");
      return;
    }
    try {
      await document.exitFullscreen();
      if (document.fullscreenElement !== explorer) {
        updateFullscreenMode(null);
        setFullscreenAnnouncement("exited");
      }
    } catch {
      setFullscreenAnnouncement("exit-unavailable");
    }
  }, [fullscreenMode, releaseFullscreenFallbackHost, updateFullscreenMode]);
  const enterFullscreen = useCallback(async () => {
    const explorer = explorerRef.current;
    if (!explorer) return;
    if (typeof explorer.requestFullscreen !== "function") {
      enterFullscreenFallback();
      return;
    }
    try {
      await explorer.requestFullscreen();
      if (document.fullscreenElement === explorer) {
        updateFullscreenMode("native");
        setFullscreenAnnouncement("entered");
      } else {
        enterFullscreenFallback();
      }
    } catch {
      enterFullscreenFallback();
    }
  }, [enterFullscreenFallback, updateFullscreenMode]);
  const toggleFullscreen = useCallback(() => {
    if (fullscreenMode) void leaveFullscreen();
    else void enterFullscreen();
  }, [enterFullscreen, fullscreenMode, leaveFullscreen]);

  useEffect(() => {
    const handleFullscreenChange = (): void => {
      const isExplorerFullscreen = document.fullscreenElement === explorerRef.current;
      const current = fullscreenModeRef.current;
      if (isExplorerFullscreen) {
        releaseFullscreenFallbackHost();
        if (current !== "native") setFullscreenAnnouncement("entered");
        updateFullscreenMode("native");
      } else if (current === "native") {
        setFullscreenAnnouncement("exited");
        updateFullscreenMode(null);
      }
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    handleFullscreenChange();
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, [releaseFullscreenFallbackHost, updateFullscreenMode]);

  useEffect(
    () => () => releaseFullscreenFallbackHost(),
    [releaseFullscreenFallbackHost],
  );

  useEffect(() => {
    const handleFullscreenKeyDown = (event: KeyboardEvent): void => {
      if (!fullscreenMode) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        void leaveFullscreen();
        return;
      }
      if (event.key !== "Tab") return;
      const explorer = explorerRef.current;
      if (!explorer) return;
      const focusable = [...explorer.querySelectorAll<HTMLElement>(
        "button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), "
        + "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
      )];
      if (focusable.length === 0) {
        event.preventDefault();
        event.stopPropagation();
        explorer.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeElement = document.activeElement;
      const focusMovedOutside = !activeElement || !explorer.contains(activeElement);
      if (event.shiftKey && (activeElement === first || focusMovedOutside)) {
        event.preventDefault();
        event.stopPropagation();
        last.focus();
      } else if (!event.shiftKey && (activeElement === last || focusMovedOutside)) {
        event.preventDefault();
        event.stopPropagation();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleFullscreenKeyDown, true);
    return () => window.removeEventListener("keydown", handleFullscreenKeyDown, true);
  }, [fullscreenMode, leaveFullscreen]);

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
    () => toFlowNodes(laidOut, direction, language),
    [direction, laidOut, language],
  );
  const projectedEdges = useMemo(
    () => toFlowEdges(laidOut, selection, reducedMotion, language),
    [laidOut, language, reducedMotion, selection],
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
  }, [fitGraph, fullscreenMode, layoutIntentKey]);
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
  const fullscreenStatus = fullscreenAnnouncement === "entered"
    ? copy.fullscreenEntered
    : fullscreenAnnouncement === "fallback"
      ? copy.fullscreenFallback
      : fullscreenAnnouncement === "exited"
        ? copy.fullscreenExited
        : fullscreenAnnouncement === "exit-unavailable"
          ? copy.fullscreenExitUnavailable
          : null;
  const reactFlowAriaLabelConfig = useMemo(() => ({
    "node.a11yDescription.default": copy.nodeKeyboardDescription,
    "node.a11yDescription.keyboardDisabled": copy.nodeKeyboardDisabledDescription,
    "node.a11yDescription.ariaLiveMessage": copy.nodeMoved,
    "edge.a11yDescription.default": copy.edgeKeyboardDescription,
    "controls.ariaLabel": copy.controls,
    "controls.zoomIn.ariaLabel": copy.zoomIn,
    "controls.zoomOut.ariaLabel": copy.zoomOut,
    "controls.fitView.ariaLabel": copy.fitView,
    "controls.interactive.ariaLabel": copy.toggleInteractivity,
    "minimap.ariaLabel": copy.minimap,
    "handle.ariaLabel": copy.handle,
  }), [copy]);

  return (
    <section
      aria-label={resolvedAriaLabel}
      className={[
        "graph-explorer",
        compact ? "is-compact" : "",
        fullscreenMode ? "is-fullscreen" : "",
        fullscreenMode === "fallback" ? "is-fullscreen-fallback" : "",
        className ?? "",
      ].filter(Boolean).join(" ")}
      data-fullscreen={fullscreenMode ? "true" : "false"}
      data-layout={direction}
      lang={language === "zh" ? "zh-CN" : "en"}
      ref={explorerRef}
    >
      <header className="graph-explorer-header">
        <div>
          <h3>{dataset.metadata.title || dataset.title || copy.defaultTitle}</h3>
          {(dataset.metadata.description || dataset.description) && (
            <p>{dataset.metadata.description || dataset.description}</p>
          )}
        </div>
        <div className="graph-explorer-layout-controls" aria-label={copy.layoutLabel}>
          <button
            aria-pressed={direction === "LR"}
            onClick={() => selectDirection("LR")}
            type="button"
          >
            {copy.horizontalLayout}
          </button>
          <button
            aria-pressed={direction === "TB"}
            onClick={() => selectDirection("TB")}
            type="button"
          >
            {copy.verticalLayout}
          </button>
          <button onClick={showOverview} type="button">{copy.resetView}</button>
          <button
            aria-pressed={Boolean(fullscreenMode)}
            onClick={toggleFullscreen}
            title={fullscreenMode ? copy.exitFullscreen : copy.enterFullscreen}
            type="button"
          >
            {fullscreenMode ? copy.exitFullscreen : copy.enterFullscreen}
          </button>
        </div>
      </header>
      {fullscreenStatus && (
        <div
          aria-label={copy.fullscreenStatusLabel}
          aria-live="polite"
          className="graph-explorer-fullscreen-status"
          role="status"
        >
          {fullscreenStatus}
        </div>
      )}

      <div className="graph-explorer-toolbar">
        <label className="graph-explorer-search">
          <span>{copy.searchGraph}</span>
          <input
            aria-label={copy.searchGraph}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={copy.searchPlaceholder}
            type="search"
            value={query}
          />
        </label>
        <fieldset>
          <legend>{copy.nodeKinds}</legend>
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
          <strong>{copy.unableToLoad}</strong>
          <span>{graphError}</span>
        </div>
      )}
      {dataset.metadata.truncated && (
        <div className="graph-explorer-alert is-truncated" role="status">
          {copy.topologyTruncated(returnedNodes, totalNodes, returnedEdges, totalEdges)}
        </div>
      )}
      {detailValuesTruncated && (
        <div className="graph-explorer-alert is-truncated" role="status">
          {copy.detailsTruncated(
            typeof detailTruncationCount === "number" ? detailTruncationCount : undefined,
          )}
        </div>
      )}

      <div className="graph-explorer-content">
        <div className="graph-explorer-canvas">
          <ReactFlow<ExplorerFlowNode, ExplorerFlowEdge>
            ariaLabelConfig={reactFlowAriaLabelConfig}
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
            panOnDrag
            selectionMode={SelectionMode.Partial}
            selectionOnDrag
            zoomOnDoubleClick
            zoomOnPinch
            zoomOnScroll
          >
            <Background gap={20} size={1} />
            <MiniMap
              ariaLabel={copy.minimap}
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
              <strong>{copy.noGraphData}</strong>
              <span>{copy.noGraphDataDescription}</span>
            </div>
          )}
          {dataset.nodes.length > 0 && filtered.nodes.length === 0 && (
            <div className="graph-explorer-empty">
              <strong>{copy.noMatches}</strong>
              <button
                onClick={() => {
                  setQuery("");
                  setEnabledKinds(new Set(kinds));
                  setFocusNodeId(null);
                }}
                type="button"
              >
                {copy.clearFilters}
              </button>
            </div>
          )}
        </div>

        <aside aria-label={copy.graphDetails} className="graph-explorer-details">
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
                  <p>{selectedNode.kind} · {localizedStatusLabel(selectedNode.status, language)}</p>
                </div>
              </div>
              {selectedNode.summary && <p className="graph-explorer-summary">{selectedNode.summary}</p>}
              {selectedNode.badges && selectedNode.badges.length > 0 && (
                <ul className="graph-explorer-badges" aria-label={copy.nodeBadges}>
                  {selectedNode.badges.map((badge) => <li key={badge}>{badge}</li>)}
                </ul>
              )}
              <DetailRows details={selectedNode.details} />
              <div className="graph-explorer-detail-actions">
                <button onClick={() => setFocusNodeId(selectedNode.id)} type="button">
                  {copy.focusNeighbors}
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
                  <p>{selectedEdge.kind} · {localizedStatusLabel(selectedEdge.status, language)}</p>
                </div>
              </div>
              <dl className="graph-explorer-detail-list">
                <div>
                  <dt>{copy.from}</dt>
                  <dd>{selectedEdgeSource?.label ?? selectedEdge.source}</dd>
                </div>
                <div>
                  <dt>{copy.to}</dt>
                  <dd>{selectedEdgeTarget?.label ?? selectedEdge.target}</dd>
                </div>
              </dl>
              <DetailRows details={selectedEdge.details} />
            </>
          ) : (
            <div className="graph-explorer-inspection-hint">
              <strong>{copy.inspectGraph}</strong>
              <span>{copy.inspectionHint}</span>
            </div>
          )}

          {(dataset.metadata.timeWindow
            || dataset.metadata.coverage
            || dataset.metadata.blindSpots?.length
            || dataset.metadata.limitations?.length
            || dataset.metadata.semantics) && (
            <section aria-label={copy.graphCoverage} className="graph-explorer-coverage">
              <h5>{copy.coverageAndLimits}</h5>
              {dataset.metadata.timeWindow && (
                <div>
                  <strong>{copy.timeWindow}</strong>
                  <span>
                    {dataset.metadata.timeWindow.start || copy.unknownStart}
                    {" — "}
                    {dataset.metadata.timeWindow.end || copy.unknownEnd}
                  </span>
                </div>
              )}
              {dataset.metadata.coverage && (
                <div>
                  <strong>{copy.coverage}</strong>
                  <CoverageValue value={dataset.metadata.coverage} />
                </div>
              )}
              {dataset.metadata.semantics && (
                <div>
                  <strong>{copy.semantics}</strong>
                  <CoverageValue value={dataset.metadata.semantics} />
                </div>
              )}
              {dataset.metadata.blindSpots && dataset.metadata.blindSpots.length > 0 && (
                <div>
                  <strong>{copy.blindSpots}</strong>
                  <ul>
                    {dataset.metadata.blindSpots.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              )}
              {dataset.metadata.limitations && dataset.metadata.limitations.length > 0 && (
                <div>
                  <strong>{copy.limitations}</strong>
                  <ul>
                    {dataset.metadata.limitations.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              )}
            </section>
          )}

          {filtered.edges.length > 0 && (
            <label className="graph-explorer-edge-picker">
              <span>{copy.inspectRelationship}</span>
              <select
                aria-label={copy.inspectRelationship}
                onChange={(event) => {
                  if (event.target.value) setSelection({ type: "edge", id: event.target.value });
                }}
                value={selection?.type === "edge" ? selection.id : ""}
              >
                <option value="">{copy.chooseEdge}</option>
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

          <section aria-label={copy.graphLegend} className="graph-explorer-legend">
            <h5>{copy.legend}</h5>
            <div>
              {[...new Set(dataset.nodes.map((node) => node.status ?? "default"))].sort().map((status) => (
                <span key={status}>
                  <i className={`graph-explorer-status-symbol is-${status}`} aria-hidden="true">
                    {statusSymbol(status)}
                  </i>
                  {localizedStatusLabel(status, language)}
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
