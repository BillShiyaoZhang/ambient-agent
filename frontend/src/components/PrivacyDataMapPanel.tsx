import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Database,
  Eye,
  FileCheck2,
  Link2,
  Network,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  loadPrivacyDataMap,
  type PrivacyMapDeclaredAssociation,
  type PrivacyMapNode,
  type PrivacyMapObservedFlow,
  type PrivacyDataMapResponse,
} from "../services/privacyDataMap";
import {
  formatPrivacyDataMapRange,
  formatPrivacyDataMapSelectionAnnouncement,
  formatPrivacyDataMapUpdated,
  formatPrivacyDataMapWarning,
  getPrivacyDataMapMessages,
} from "../services/i18n";
import { SystemDrawer, SystemIconButton } from "./system/SystemUI";
import "./PrivacyDataMapPanel.css";

interface PrivacyDataMapPanelProps {
  open: boolean;
  language: "zh" | "en";
  apiBase: string;
  onClose: () => void;
}

type LoadState =
  | { status: "idle" | "loading" }
  | { status: "ready"; data: PrivacyDataMapResponse }
  | { status: "error" };

type PrivacyMapNodeView = {
  selectionId: string;
  type: "node";
  node: PrivacyMapNode;
  accessibleName: string;
};

type PrivacyMapObservedFlowView = {
  selectionId: string;
  type: "observed-flow";
  flow: PrivacyMapObservedFlow;
  source: PrivacyMapNodeView;
  destination: PrivacyMapNodeView;
  accessibleName: string;
};

type PrivacyMapDeclaredAssociationView = {
  selectionId: string;
  type: "declared-association";
  association: PrivacyMapDeclaredAssociation;
  app: PrivacyMapNodeView;
  schema: PrivacyMapNodeView;
  accessibleName: string;
};

type PrivacyMapSelection =
  | PrivacyMapNodeView
  | PrivacyMapObservedFlowView
  | PrivacyMapDeclaredAssociationView;

type PrivacyMapViewModel = {
  nodes: PrivacyMapNodeView[];
  observedFlows: PrivacyMapObservedFlowView[];
  declaredAssociations: PrivacyMapDeclaredAssociationView[];
  selections: Map<string, PrivacyMapSelection>;
};

type PrivacyMapPageKey = "observed" | "declared" | "nodes";

type PrivacyMapPageIndexes = Record<PrivacyMapPageKey, number>;

type PrivacyMapPage<T> = {
  items: T[];
  pageIndex: number;
  start: number;
  end: number;
  total: number;
};

type PrivacyMapVisualOverview = {
  nodes: PrivacyMapNodeView[];
  observedFlows: PrivacyMapObservedFlowView[];
  declaredAssociations: PrivacyMapDeclaredAssociationView[];
  bounded: boolean;
};

const SEMANTIC_PAGE_SIZE = 24;
const VISUAL_NODE_LIMIT = 32;
const VISUAL_OBSERVED_FLOW_LIMIT = 8;
const VISUAL_DECLARED_ASSOCIATION_LIMIT = 8;
const SELECTION_DETAILS_ID = "privacy-map-selection-details";
const INITIAL_PAGE_INDEXES: PrivacyMapPageIndexes = {
  observed: 0,
  declared: 0,
  nodes: 0,
};

function formatTimestamp(value: string, language: "zh" | "en"): string {
  return new Intl.DateTimeFormat(language === "zh" ? "zh-CN" : "en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function buildPrivacyMapViewModel(
  data: PrivacyDataMapResponse,
  copy: ReturnType<typeof getPrivacyDataMapMessages>["copy"]
): PrivacyMapViewModel {
  const nodes = data.nodes.map<PrivacyMapNodeView>((node) => ({
    selectionId: `node:${node.id}`,
    type: "node",
    node,
    accessibleName: `${copy.selectNode} ${node.label}`,
  }));
  const nodesById = new Map(nodes.map((node) => [node.node.id, node]));
  const requireNode = (nodeId: string): PrivacyMapNodeView => {
    const node = nodesById.get(nodeId);
    if (!node) throw new Error("Validated Privacy Map relationship referenced a missing node.");
    return node;
  };

  const observedFlows = data.observed_flows.map<PrivacyMapObservedFlowView>((flow) => {
    const source = requireNode(flow.source_node_id);
    const destination = requireNode(flow.destination_node_id);
    return {
      selectionId: `relationship:${flow.id}`,
      type: "observed-flow",
      flow,
      source,
      destination,
      accessibleName:
        `${copy.selectObservedTransfer} ${source.node.label} ${copy.to} ${destination.node.label}`,
    };
  });
  const declaredAssociations =
    data.declared_associations.map<PrivacyMapDeclaredAssociationView>((association) => {
      const app = requireNode(association.app_node_id);
      const schema = requireNode(association.schema_node_id);
      return {
        selectionId: `relationship:${association.id}`,
        type: "declared-association",
        association,
        app,
        schema,
        accessibleName:
          `${copy.selectDeclaredAssociation} ${app.node.label} ${copy.and} ${schema.node.label}`,
      };
    });
  const selections = new Map<string, PrivacyMapSelection>();
  for (const item of [...nodes, ...observedFlows, ...declaredAssociations]) {
    selections.set(item.selectionId, item);
  }
  return { nodes, observedFlows, declaredAssociations, selections };
}

function buildVisualOverview(viewModel: PrivacyMapViewModel): PrivacyMapVisualOverview {
  const observedFlows = viewModel.observedFlows.slice(0, VISUAL_OBSERVED_FLOW_LIMIT);
  const declaredAssociations = viewModel.declaredAssociations.slice(
    0,
    VISUAL_DECLARED_ASSOCIATION_LIMIT
  );
  const referencedNodeIds = new Set<string>();
  for (const item of observedFlows) {
    referencedNodeIds.add(item.source.node.id);
    referencedNodeIds.add(item.destination.node.id);
  }
  for (const item of declaredAssociations) {
    referencedNodeIds.add(item.app.node.id);
    referencedNodeIds.add(item.schema.node.id);
  }

  const referencedNodes = viewModel.nodes.filter((item) => referencedNodeIds.has(item.node.id));
  const remainingNodeCapacity = Math.max(0, VISUAL_NODE_LIMIT - referencedNodes.length);
  const remainingNodes = viewModel.nodes
    .filter((item) => !referencedNodeIds.has(item.node.id))
    .slice(0, remainingNodeCapacity);
  const nodes = [...referencedNodes, ...remainingNodes];

  return {
    nodes,
    observedFlows,
    declaredAssociations,
    bounded:
      nodes.length < viewModel.nodes.length ||
      observedFlows.length < viewModel.observedFlows.length ||
      declaredAssociations.length < viewModel.declaredAssociations.length,
  };
}

function getSelectionAnnouncementLabel(item: PrivacyMapSelection): string {
  if (item.type === "node") return item.node.label;
  if (item.type === "observed-flow") {
    return `${item.source.node.label} → ${item.destination.node.label}`;
  }
  return `${item.app.node.label} · ${item.schema.node.label}`;
}

function buildPage<T>(items: T[], requestedPageIndex: number): PrivacyMapPage<T> {
  const pageCount = Math.max(1, Math.ceil(items.length / SEMANTIC_PAGE_SIZE));
  const pageIndex = Math.min(Math.max(requestedPageIndex, 0), pageCount - 1);
  const offset = pageIndex * SEMANTIC_PAGE_SIZE;
  const pageItems = items.slice(offset, offset + SEMANTIC_PAGE_SIZE);
  return {
    items: pageItems,
    pageIndex,
    start: items.length === 0 ? 0 : offset + 1,
    end: offset + pageItems.length,
    total: items.length,
  };
}

interface PrivacyMapPaginationProps {
  label: string;
  language: "zh" | "en";
  page: PrivacyMapPage<unknown>;
  previousLabel: string;
  nextLabel: string;
  onPageChange: (pageIndex: number) => void;
}

function PrivacyMapPagination({
  label,
  language,
  page,
  previousLabel,
  nextLabel,
  onPageChange,
}: PrivacyMapPaginationProps) {
  if (page.total <= SEMANTIC_PAGE_SIZE) return null;

  const atFirstPage = page.pageIndex === 0;
  const atLastPage = page.end >= page.total;

  return (
    <div className="privacy-map-pagination">
      <p aria-live="polite" aria-atomic="true">
        {formatPrivacyDataMapRange(page.start, page.end, page.total, language)}
      </p>
      <div>
        <button
          type="button"
          aria-label={`${previousLabel}: ${label}`}
          aria-disabled={atFirstPage}
          onClick={() => {
            if (!atFirstPage) onPageChange(page.pageIndex - 1);
          }}
        >
          <ChevronLeft size={15} aria-hidden="true" />
          <span>{previousLabel}</span>
        </button>
        <button
          type="button"
          aria-label={`${nextLabel}: ${label}`}
          aria-disabled={atLastPage}
          onClick={() => {
            if (!atLastPage) onPageChange(page.pageIndex + 1);
          }}
        >
          <span>{nextLabel}</span>
          <ChevronRight size={15} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

export function PrivacyDataMapPanel({
  open,
  language,
  apiBase,
  onClose,
}: PrivacyDataMapPanelProps) {
  const {
    copy,
    coverageChannels,
    coverageObservations,
    stages,
  } = getPrivacyDataMapMessages(language);
  const requestRef = useRef<AbortController | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [state, setState] = useState<LoadState>({ status: "idle" });
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [pageIndexes, setPageIndexes] =
    useState<PrivacyMapPageIndexes>(INITIAL_PAGE_INDEXES);

  const refresh = useCallback(async () => {
    if (requestRef.current) return;
    const controller = new AbortController();
    requestRef.current = controller;
    setSelectedItemId(null);
    setPageIndexes(INITIAL_PAGE_INDEXES);
    setState({ status: "loading" });

    try {
      const data = await loadPrivacyDataMap(apiBase, controller.signal);
      if (!controller.signal.aborted) setState({ status: "ready", data });
    } catch {
      if (!controller.signal.aborted) setState({ status: "error" });
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
    }
  }, [apiBase]);

  useEffect(() => {
    if (!open) {
      requestRef.current?.abort();
      requestRef.current = null;
      setSelectedItemId(null);
      setPageIndexes(INITIAL_PAGE_INDEXES);
      setState({ status: "idle" });
      return;
    }

    void refresh();
    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, [open, refresh]);

  const data = state.status === "ready" ? state.data : null;
  const viewModel = useMemo(
    () => (data ? buildPrivacyMapViewModel(data, copy) : null),
    [copy, data]
  );
  const selectedItem = selectedItemId && viewModel
    ? viewModel.selections.get(selectedItemId) ?? null
    : null;
  const visualOverview = useMemo(
    () => (viewModel ? buildVisualOverview(viewModel) : null),
    [viewModel]
  );
  const observedPage = useMemo(
    () => buildPage(viewModel?.observedFlows ?? [], pageIndexes.observed),
    [pageIndexes.observed, viewModel]
  );
  const declaredPage = useMemo(
    () => buildPage(viewModel?.declaredAssociations ?? [], pageIndexes.declared),
    [pageIndexes.declared, viewModel]
  );
  const nodePage = useMemo(
    () => buildPage(viewModel?.nodes ?? [], pageIndexes.nodes),
    [pageIndexes.nodes, viewModel]
  );
  const setPageIndex = (key: PrivacyMapPageKey, pageIndex: number) => {
    setPageIndexes((current) =>
      current[key] === pageIndex ? current : { ...current, [key]: pageIndex }
    );
  };

  return (
    <SystemDrawer
      open={open}
      onClose={onClose}
      label={copy.title}
      className="privacy-map-panel"
      restoreFocusOnClose={false}
      initialFocusRef={closeButtonRef}
    >
      <header className="privacy-map-header">
        <div className="privacy-map-heading">
          <span className="privacy-map-heading-icon" aria-hidden="true">
            <ShieldCheck size={18} />
          </span>
          <div>
            <h2>{copy.title}</h2>
            <p>{copy.description}</p>
          </div>
        </div>
        <div className="privacy-map-header-actions">
          <SystemIconButton
            onClick={() => void refresh()}
            disabled={state.status === "loading"}
            label={copy.refresh}
          >
            <RefreshCw className={state.status === "loading" ? "is-spinning" : ""} size={17} />
          </SystemIconButton>
          <SystemIconButton ref={closeButtonRef} onClick={onClose} label={copy.close}>
            <X size={18} />
          </SystemIconButton>
        </div>
      </header>

      <div className="privacy-map-body">
        <section className="privacy-map-overview" aria-label={copy.scope}>
          <article>
            <span>{copy.scope}</span>
            <strong>{copy.workspace}</strong>
          </article>
          <article>
            <span>{copy.window}</span>
            {data?.scope.observed_window ? (
              <strong>
                <time dateTime={data.scope.observed_window.from}>
                  {formatTimestamp(data.scope.observed_window.from, language)}
                </time>
                <span aria-hidden="true"> — </span>
                <time dateTime={data.scope.observed_window.to}>
                  {formatTimestamp(data.scope.observed_window.to, language)}
                </time>
              </strong>
            ) : data ? (
              <strong>{copy.noWindow}</strong>
            ) : (
              <strong>{state.status === "loading" ? copy.checking : copy.unavailable}</strong>
            )}
          </article>
          <article>
            <span>{copy.source}</span>
            <strong data-tone={data?.source_health.status ?? "unknown"}>
              {data
                ? data.source_health.status === "degraded"
                  ? copy.degraded
                  : copy.healthy
                : state.status === "loading"
                  ? copy.checking
                  : copy.unavailable}
            </strong>
          </article>
        </section>

        <section className="privacy-map-dashboard" role="group" aria-label={copy.title}>
          <section
            className="privacy-map-coverage"
            aria-labelledby="privacy-map-blind-spots-title"
          >
          <div className="privacy-map-section-heading">
            <span className="privacy-map-evidence is-unknown">
              <CircleHelp size={14} aria-hidden="true" />
              {copy.unknown}
            </span>
            <div>
              <h3 id="privacy-map-blind-spots-title">{copy.blindSpotsTitle}</h3>
              <strong>{copy.partialCoverage}</strong>
              <p>{copy.coverageBody}</p>
            </div>
          </div>
          <ul>
            {(data?.coverage.channels ?? []).map((channel) => (
              <li key={channel.id} data-observation={channel.observation}>
                <span>{coverageChannels[channel.id]}</span>
                <strong>{coverageObservations[channel.observation]}</strong>
              </li>
            ))}
          </ul>
          </section>

          {state.status === "loading" && (
            <section className="privacy-map-state" role="status" aria-live="polite">
              <RefreshCw className="is-spinning" size={22} aria-hidden="true" />
              <p>{copy.loading}</p>
            </section>
          )}

          {state.status === "error" && (
            <section className="privacy-map-state is-error" role="alert">
              <AlertTriangle size={24} aria-hidden="true" />
              <h3>{copy.errorTitle}</h3>
              <p>{copy.errorBody}</p>
              <button type="button" onClick={() => void refresh()}>
                <RefreshCw size={16} aria-hidden="true" />
                {copy.retry}
              </button>
            </section>
          )}

          {data && (
            <>
            <p className="privacy-map-live-status" role="status" aria-live="polite">
              {formatPrivacyDataMapUpdated(
                data.nodes.length,
                data.observed_flows.length,
                data.declared_associations.length,
                language
              )}
            </p>
            <p
              className="privacy-map-live-status"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              {selectedItem
                ? formatPrivacyDataMapSelectionAnnouncement(
                    getSelectionAnnouncementLabel(selectedItem),
                    language
                  )
                : ""}
            </p>

            {data.source_health.status === "degraded" && (
              <section className="privacy-map-warning" aria-labelledby="privacy-map-warning-title">
                <AlertTriangle size={18} aria-hidden="true" />
                <div>
                  <h3 id="privacy-map-warning-title">{copy.degradedTitle}</h3>
                  <p>{copy.degradedBody}</p>
                  <ul>
                    {data.warnings.map((warning) => (
                      <li key={warning.code}>
                        {formatPrivacyDataMapWarning(warning.code, warning.count, language)}
                      </li>
                    ))}
                  </ul>
                </div>
              </section>
            )}

            {viewModel && visualOverview && (
              <>
                <section
                  className="privacy-map-visual"
                  aria-labelledby="privacy-map-visual-title"
                >
                  <div className="privacy-map-layer-heading">
                    <span className="privacy-map-evidence is-inventory">
                      <Network size={14} aria-hidden="true" />
                      {viewModel.nodes.length}
                    </span>
                    <div>
                      <h3 id="privacy-map-visual-title">{copy.visualMapTitle}</h3>
                      <p>{copy.visualMapHelp}</p>
                    </div>
                  </div>

                  {visualOverview.bounded && (
                    <p className="privacy-map-overview-notice">
                      {copy.visualOverviewNotice}
                    </p>
                  )}

                  <div className="privacy-map-visual-canvas">
                    <div className="privacy-map-visual-node-grid">
                      {visualOverview.nodes.map((item) => (
                        <button
                          key={item.selectionId}
                          type="button"
                          className="privacy-map-selectable privacy-map-visual-node"
                          data-kind={item.node.kind}
                          aria-label={item.accessibleName}
                          aria-pressed={selectedItemId === item.selectionId}
                          aria-controls={SELECTION_DETAILS_ID}
                          onClick={() => setSelectedItemId(item.selectionId)}
                        >
                          <span aria-hidden="true">
                            {item.node.kind === "schema" ? (
                              <Database size={16} />
                            ) : item.node.kind === "app" ? (
                              <Boxes size={16} />
                            ) : item.node.kind === "recorded_model_target" ? (
                              <Eye size={16} />
                            ) : (
                              <ShieldCheck size={16} />
                            )}
                          </span>
                          <strong>{item.node.label}</strong>
                          <small>
                            {item.node.kind === "platform"
                              ? copy.platformNode
                              : item.node.kind === "recorded_model_target"
                                ? `${copy.modelNode} · ${copy.unknownLocation}`
                                : item.node.kind === "app"
                                  ? copy.appNode
                                  : copy.schemaNode}
                          </small>
                        </button>
                      ))}
                    </div>

                    <div className="privacy-map-visual-relationships">
                      {visualOverview.observedFlows.map((item) => (
                        <button
                          key={item.selectionId}
                          type="button"
                          className="privacy-map-selectable privacy-map-visual-edge is-observed"
                          aria-label={item.accessibleName}
                          aria-pressed={selectedItemId === item.selectionId}
                          aria-controls={SELECTION_DETAILS_ID}
                          onClick={() => setSelectedItemId(item.selectionId)}
                        >
                          <span className="privacy-map-visual-edge-evidence">
                            <Eye size={13} aria-hidden="true" />
                            {copy.observed}
                          </span>
                          <span className="privacy-map-visual-edge-route">
                            <strong>{item.source.node.label}</strong>
                            <ArrowRight size={16} aria-hidden="true" />
                            <strong>{item.destination.node.label}</strong>
                          </span>
                          <small>{stages[item.flow.stage]}</small>
                        </button>
                      ))}
                      {visualOverview.declaredAssociations.map((item) => (
                        <button
                          key={item.selectionId}
                          type="button"
                          className="privacy-map-selectable privacy-map-visual-edge is-declared"
                          aria-label={item.accessibleName}
                          aria-pressed={selectedItemId === item.selectionId}
                          aria-controls={SELECTION_DETAILS_ID}
                          onClick={() => setSelectedItemId(item.selectionId)}
                        >
                          <span className="privacy-map-visual-edge-evidence">
                            <FileCheck2 size={13} aria-hidden="true" />
                            {copy.declared}
                          </span>
                          <span className="privacy-map-visual-edge-route">
                            <strong>{item.app.node.label}</strong>
                            <Link2 size={16} aria-hidden="true" />
                            <strong>{item.schema.node.label}</strong>
                          </span>
                          <small>{copy.declaredHelp}</small>
                        </button>
                      ))}
                    </div>
                  </div>
                </section>

                <section
                  id={SELECTION_DETAILS_ID}
                  className="privacy-map-selection-details"
                  aria-labelledby="privacy-map-selection-details-title"
                >
                  <div className="privacy-map-layer-heading">
                    <span className="privacy-map-evidence is-inventory">
                      <CircleHelp size={14} aria-hidden="true" />
                    </span>
                    <div>
                      <h3 id="privacy-map-selection-details-title">
                        {copy.selectionDetailsTitle}
                      </h3>
                    </div>
                  </div>

                  {!selectedItem ? (
                    <p>{copy.selectionPrompt}</p>
                  ) : selectedItem.type === "node" ? (
                    <div className="privacy-map-selection-card">
                      <strong>{selectedItem.node.label}</strong>
                      <dl>
                        <div>
                          <dt>{copy.relationshipLabel}</dt>
                          <dd>
                            {selectedItem.node.kind === "platform"
                              ? copy.platformNode
                              : selectedItem.node.kind === "recorded_model_target"
                                ? copy.modelNode
                                : selectedItem.node.kind === "app"
                                  ? copy.appNode
                                  : copy.schemaNode}
                          </dd>
                        </div>
                        {selectedItem.node.kind === "recorded_model_target" && (
                          <div>
                            <dt>{copy.unknown}</dt>
                            <dd>{copy.unknownLocation}</dd>
                          </div>
                        )}
                      </dl>
                    </div>
                  ) : selectedItem.type === "observed-flow" ? (
                    <div className="privacy-map-selection-card">
                      <strong>
                        {selectedItem.source.node.label} → {selectedItem.destination.node.label}
                      </strong>
                      <dl>
                        <div>
                          <dt>{copy.evidenceLabel}</dt>
                          <dd>{copy.observed}</dd>
                        </div>
                        <div>
                          <dt>{copy.stageLabel}</dt>
                          <dd>{stages[selectedItem.flow.stage]}</dd>
                        </div>
                        <div>
                          <dt>{copy.eventCountLabel}</dt>
                          <dd>
                            {selectedItem.flow.count}{" "}
                            {language === "zh"
                              ? copy.recordedEvents
                              : selectedItem.flow.count === 1
                                ? copy.recordedEvent
                                : copy.recordedEvents}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  ) : (
                    <div className="privacy-map-selection-card">
                      <strong>
                        {selectedItem.app.node.label} · {selectedItem.schema.node.label}
                      </strong>
                      <dl>
                        <div>
                          <dt>{copy.evidenceLabel}</dt>
                          <dd>{copy.declared}</dd>
                        </div>
                      </dl>
                      <p>{copy.declaredHelp}</p>
                    </div>
                  )}
                </section>

                <section
                  className="privacy-map-semantic"
                  aria-labelledby="privacy-map-semantic-title"
                >
                  <div className="privacy-map-layer-heading">
                    <span className="privacy-map-evidence is-inventory">
                      <Boxes size={14} aria-hidden="true" />
                    </span>
                    <div>
                      <h3 id="privacy-map-semantic-title">{copy.semanticMapTitle}</h3>
                      <p>{copy.semanticMapHelp}</p>
                    </div>
                  </div>

                  <section
                    className="privacy-map-layer"
                    aria-labelledby="privacy-map-observed-title"
                  >
                    <div className="privacy-map-layer-heading">
                      <span className="privacy-map-evidence is-observed">
                        <Eye size={14} aria-hidden="true" />
                        {copy.observed}
                      </span>
                      <div>
                        <h3 id="privacy-map-observed-title">{copy.observedTitle}</h3>
                        <p>{copy.observedHelp}</p>
                      </div>
                    </div>

                    {viewModel.observedFlows.length === 0 ? (
                      <div className="privacy-map-empty">
                        <Eye size={20} aria-hidden="true" />
                        <p>{copy.observedEmpty}</p>
                      </div>
                    ) : (
                      <ol className="privacy-map-relationship-list">
                        {observedPage.items.map((item) => (
                          <li key={item.selectionId}>
                            <button
                              type="button"
                              className="privacy-map-selectable privacy-map-semantic-control"
                              aria-label={item.accessibleName}
                              aria-pressed={selectedItemId === item.selectionId}
                              aria-controls={SELECTION_DETAILS_ID}
                              onClick={() => setSelectedItemId(item.selectionId)}
                            >
                              <span className="privacy-map-relationship-label">
                                <strong>{copy.observedTransfer}</strong>
                                <span>{stages[item.flow.stage]}</span>
                              </span>
                              <span className="privacy-map-route">
                                <span>{item.source.node.label}</span>
                                <ArrowRight size={16} aria-hidden="true" />
                                <span>{item.destination.node.label}</span>
                              </span>
                              <dl>
                                <div>
                                  <dt>{copy.observed}</dt>
                                  <dd>
                                    {item.flow.count}{" "}
                                    {language === "zh"
                                      ? copy.recordedEvents
                                      : item.flow.count === 1
                                        ? copy.recordedEvent
                                        : copy.recordedEvents}
                                  </dd>
                                </div>
                                <div>
                                  <dt>{copy.firstSeen}</dt>
                                  <dd>
                                    <time dateTime={item.flow.first_observed_at}>
                                      {formatTimestamp(item.flow.first_observed_at, language)}
                                    </time>
                                  </dd>
                                </div>
                                <div>
                                  <dt>{copy.lastSeen}</dt>
                                  <dd>
                                    <time dateTime={item.flow.last_observed_at}>
                                      {formatTimestamp(item.flow.last_observed_at, language)}
                                    </time>
                                  </dd>
                                </div>
                              </dl>
                            </button>
                          </li>
                        ))}
                      </ol>
                    )}
                    <PrivacyMapPagination
                      label={copy.observedTitle}
                      language={language}
                      page={observedPage}
                      previousLabel={copy.previousPage}
                      nextLabel={copy.nextPage}
                      onPageChange={(pageIndex) => setPageIndex("observed", pageIndex)}
                    />
                  </section>

                  <section
                    className="privacy-map-layer"
                    aria-labelledby="privacy-map-declared-title"
                  >
                    <div className="privacy-map-layer-heading">
                      <span className="privacy-map-evidence is-declared">
                        <FileCheck2 size={14} aria-hidden="true" />
                        {copy.declared}
                      </span>
                      <div>
                        <h3 id="privacy-map-declared-title">{copy.declaredTitle}</h3>
                        <p>{copy.declaredHelp}</p>
                      </div>
                    </div>

                    {viewModel.declaredAssociations.length === 0 ? (
                      <div className="privacy-map-empty">
                        <FileCheck2 size={20} aria-hidden="true" />
                        <p>{copy.declaredEmpty}</p>
                      </div>
                    ) : (
                      <ol className="privacy-map-relationship-list is-declared">
                        {declaredPage.items.map((item) => (
                          <li key={item.selectionId}>
                            <button
                              type="button"
                              className="privacy-map-selectable privacy-map-semantic-control"
                              aria-label={item.accessibleName}
                              aria-pressed={selectedItemId === item.selectionId}
                              aria-controls={SELECTION_DETAILS_ID}
                              onClick={() => setSelectedItemId(item.selectionId)}
                            >
                              <span className="privacy-map-relationship-label">
                                <strong>{copy.declaredAssociation}</strong>
                              </span>
                              <span className="privacy-map-route">
                                <span>{item.app.node.label}</span>
                                <span className="privacy-map-association-word">
                                  {copy.associatedWith}
                                </span>
                                <span>{item.schema.node.label}</span>
                              </span>
                            </button>
                          </li>
                        ))}
                      </ol>
                    )}
                    <PrivacyMapPagination
                      label={copy.declaredTitle}
                      language={language}
                      page={declaredPage}
                      previousLabel={copy.previousPage}
                      nextLabel={copy.nextPage}
                      onPageChange={(pageIndex) => setPageIndex("declared", pageIndex)}
                    />
                  </section>

                  <section
                    className="privacy-map-layer"
                    aria-labelledby="privacy-map-inventory-title"
                  >
                    <div className="privacy-map-layer-heading">
                      <span className="privacy-map-evidence is-inventory">
                        <Boxes size={14} aria-hidden="true" />
                        {viewModel.nodes.length}
                      </span>
                      <div>
                        <h3 id="privacy-map-inventory-title">{copy.inventoryTitle}</h3>
                        <p>{copy.inventoryHelp}</p>
                      </div>
                    </div>
                    <ul className="privacy-map-node-list">
                      {nodePage.items.map((item) => (
                        <li key={item.selectionId}>
                          <button
                            type="button"
                            className="privacy-map-selectable privacy-map-semantic-control"
                            aria-label={item.accessibleName}
                            aria-pressed={selectedItemId === item.selectionId}
                            aria-controls={SELECTION_DETAILS_ID}
                            onClick={() => setSelectedItemId(item.selectionId)}
                          >
                            <span aria-hidden="true">
                              {item.node.kind === "schema" ? (
                                <Database size={16} />
                              ) : item.node.kind === "app" ? (
                                <Boxes size={16} />
                              ) : item.node.kind === "recorded_model_target" ? (
                                <Eye size={16} />
                              ) : (
                                <ShieldCheck size={16} />
                              )}
                            </span>
                            <span>
                              <strong>{item.node.label}</strong>
                              <small>
                                {item.node.kind === "platform"
                                  ? copy.platformNode
                                  : item.node.kind === "recorded_model_target"
                                    ? `${copy.modelNode} · ${copy.unknownLocation}`
                                    : item.node.kind === "app"
                                      ? copy.appNode
                                      : copy.schemaNode}
                              </small>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                    <PrivacyMapPagination
                      label={copy.inventoryTitle}
                      language={language}
                      page={nodePage}
                      previousLabel={copy.previousPage}
                      nextLabel={copy.nextPage}
                      onPageChange={(pageIndex) => setPageIndex("nodes", pageIndex)}
                    />
                  </section>
                </section>
              </>
            )}
            </>
          )}
        </section>
      </div>
    </SystemDrawer>
  );
}
