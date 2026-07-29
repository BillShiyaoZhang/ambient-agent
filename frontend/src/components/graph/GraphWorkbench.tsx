import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { RefreshCw, X } from "lucide-react";
import {
  normalizeGraphExplorerPayload,
  type GraphDataset,
  type GraphExplorerPayload,
} from "../../lib/graphVisualization";
import { dataMapToGraph, workflowToGraph } from "../../lib/graphScenes";
import { SystemDialog, SystemIconButton } from "../system/SystemUI";
import { GraphExplorer } from "./GraphExplorer";
import "./GraphWorkbench.css";

type GraphScene = "ontology" | "knowledge" | "workflow" | "privacy";
type GraphRequestError = { status?: number; detail?: string };

interface GraphWorkbenchProps {
  open: boolean;
  language: "zh" | "en";
  onClose: () => void;
}

const API_BASE = `http://${window.location.hostname}:8000`;

function revealSceneTab(tab: HTMLElement): void {
  const container = tab.parentElement;
  if (!container) return;
  const left = tab.offsetLeft;
  const right = left + tab.offsetWidth;
  if (left < container.scrollLeft) {
    container.scrollLeft = left;
  } else if (right > container.scrollLeft + container.clientWidth) {
    container.scrollLeft = right - container.clientWidth;
  }
}

function localizeExplorerSnapshot(
  dataset: GraphDataset | undefined,
  scene: "ontology" | "knowledge",
  language: "zh" | "en",
): GraphDataset | undefined {
  if (!dataset || language === "en") return dataset;
  const title = scene === "ontology" ? "本体" : "知识图谱";
  const description = scene === "ontology"
    ? "规范本体实体及其子类关系。"
    : "用户 ContextRecord 及其有向关系的有界快照。";
  return {
    ...dataset,
    title,
    description,
    metadata: {
      ...dataset.metadata,
      title,
      description,
    },
  };
}

export function GraphWorkbench({ open, language, onClose }: GraphWorkbenchProps) {
  const isZh = language === "zh";
  const [scene, setScene] = useState<GraphScene>("ontology");
  const [explorerPayload, setExplorerPayload] = useState<GraphExplorerPayload | null>(null);
  const [privacyPayload, setPrivacyPayload] = useState<Record<string, unknown> | null>(null);
  const [explorerLoading, setExplorerLoading] = useState(false);
  const [privacyLoading, setPrivacyLoading] = useState(false);
  const [explorerError, setExplorerError] = useState<GraphRequestError | null>(null);
  const [privacyError, setPrivacyError] = useState<GraphRequestError | null>(null);
  const [privacyRequested, setPrivacyRequested] = useState(false);
  const explorerGeneration = useRef(0);
  const privacyGeneration = useRef(0);

  const loadExplorer = useCallback(async () => {
    const generation = ++explorerGeneration.current;
    setExplorerLoading(true);
    setExplorerError(null);
    try {
      const response = await fetch(`${API_BASE}/api/graph/explorer?record_limit=250`);
      if (!response.ok) {
        if (generation === explorerGeneration.current) {
          setExplorerError({ status: response.status });
        }
        return;
      }
      const payload = normalizeGraphExplorerPayload(await response.json());
      if (generation === explorerGeneration.current) setExplorerPayload(payload);
    } catch (error) {
      if (generation === explorerGeneration.current) {
        setExplorerError({ detail: error instanceof Error ? error.message : String(error) });
      }
    } finally {
      if (generation === explorerGeneration.current) setExplorerLoading(false);
    }
  }, []);

  const loadPrivacyMap = useCallback(async () => {
    const generation = ++privacyGeneration.current;
    setPrivacyRequested(true);
    setPrivacyLoading(true);
    setPrivacyError(null);
    try {
      const response = await fetch(`${API_BASE}/api/data-map`);
      if (!response.ok) {
        if (generation === privacyGeneration.current) {
          setPrivacyError({ status: response.status });
        }
        return;
      }
      const payload = await response.json() as Record<string, unknown>;
      if (generation === privacyGeneration.current) setPrivacyPayload(payload);
    } catch (error) {
      if (generation === privacyGeneration.current) {
        setPrivacyError({ detail: error instanceof Error ? error.message : String(error) });
      }
    } finally {
      if (generation === privacyGeneration.current) setPrivacyLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setScene("ontology");
    setPrivacyPayload(null);
    setPrivacyError(null);
    setPrivacyRequested(false);
    void loadExplorer();
  }, [loadExplorer, open]);

  useEffect(() => {
    if (open && scene === "privacy" && !privacyRequested) void loadPrivacyMap();
  }, [loadPrivacyMap, open, privacyRequested, scene]);

  const ontologyDataset = useMemo(
    () => localizeExplorerSnapshot(explorerPayload?.ontology, "ontology", language),
    [explorerPayload, language],
  );
  const knowledgeDataset = useMemo(
    () => localizeExplorerSnapshot(explorerPayload?.knowledgeGraph, "knowledge", language),
    [explorerPayload, language],
  );
  const workflowDataset = useMemo(() => workflowToGraph(undefined, language), [language]);
  const privacyDataset = useMemo(
    () => privacyPayload ? dataMapToGraph(privacyPayload, language) : null,
    [language, privacyPayload],
  );
  const activeDataset = scene === "ontology"
    ? ontologyDataset
    : scene === "knowledge"
      ? knowledgeDataset
      : scene === "workflow"
        ? workflowDataset
        : privacyDataset;
  const activeLoading = scene === "privacy" ? privacyLoading : scene === "workflow" ? false : explorerLoading;
  const activeError = scene === "privacy" ? privacyError : scene === "workflow" ? null : explorerError;
  const activeErrorPrefix = scene === "privacy"
    ? (isZh ? "数据地图请求失败" : "Data map request failed")
    : (isZh ? "图谱探索请求失败" : "Graph explorer request failed");
  const activeErrorMessage = activeError
    ? `${activeErrorPrefix}${activeError.status !== undefined
      ? ` (${activeError.status})`
      : activeError.detail
        ? `: ${activeError.detail}`
        : ""}`
    : "";
  const labels: Record<GraphScene, string> = isZh
    ? { ontology: "本体", knowledge: "知识图谱", workflow: "Agent 工作流", privacy: "隐私数据图" }
    : { ontology: "Ontology", knowledge: "Knowledge graph", workflow: "Agent workflow", privacy: "Privacy map" };
  const sceneKeys = Object.keys(labels) as GraphScene[];
  const handleSceneKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    key: GraphScene,
  ): void => {
    const currentIndex = sceneKeys.indexOf(key);
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (currentIndex + 1) % sceneKeys.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + sceneKeys.length) % sceneKeys.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = sceneKeys.length - 1;
    }
    if (nextIndex === null) return;
    event.preventDefault();
    const nextScene = sceneKeys[nextIndex];
    setScene(nextScene);
    const nextTab = document.getElementById(`graph-workbench-tab-${nextScene}`);
    nextTab?.focus();
    if (nextTab) revealSceneTab(nextTab);
  };

  return (
    <SystemDialog
      className="graph-workbench-dialog"
      description={isZh
        ? "在同一个可搜索、可筛选的画布中检查规范模型、事实关系、Agent 编排与隐私证据覆盖。"
        : "Inspect canonical models, factual relationships, agent orchestration, and privacy evidence coverage on one searchable canvas."}
      onClose={onClose}
      open={open}
      size="workbench"
      title={isZh ? "图谱探索" : "Graph Explorer"}
    >
      <div className="graph-workbench">
        <div className="graph-workbench-toolbar">
          <div aria-label={isZh ? "图谱场景" : "Graph scenes"} className="graph-workbench-tabs" role="tablist">
            {(Object.keys(labels) as GraphScene[]).map((key) => (
              <button
                aria-controls="graph-workbench-panel"
                aria-selected={scene === key}
                className={scene === key ? "is-active" : ""}
                id={`graph-workbench-tab-${key}`}
                key={key}
                onClick={(event) => {
                  setScene(key);
                  revealSceneTab(event.currentTarget);
                }}
                onKeyDown={(event) => handleSceneKeyDown(event, key)}
                role="tab"
                tabIndex={scene === key ? 0 : -1}
                type="button"
              >
                {labels[key]}
              </button>
            ))}
          </div>
          <div className="graph-workbench-actions">
            <SystemIconButton
              disabled={activeLoading || scene === "workflow"}
              label={isZh ? "刷新当前图谱" : "Refresh current graph"}
              onClick={() => {
                if (scene === "privacy") void loadPrivacyMap();
                else if (scene !== "workflow") void loadExplorer();
              }}
            >
              <RefreshCw className={activeLoading ? "is-spinning" : ""} size={16} />
            </SystemIconButton>
            <SystemIconButton label={isZh ? "关闭图谱探索" : "Close Graph Explorer"} onClick={onClose}>
              <X size={18} />
            </SystemIconButton>
          </div>
        </div>
        <section
          aria-label={labels[scene]}
          aria-labelledby={`graph-workbench-tab-${scene}`}
          className="graph-workbench-panel"
          id="graph-workbench-panel"
          role="tabpanel"
        >
          {activeLoading && !activeDataset ? (
            <div className="graph-workbench-message" role="status">
              {isZh ? "正在构建只读图谱快照…" : "Building a read-only graph snapshot…"}
            </div>
          ) : activeError ? (
            <div className="graph-workbench-message is-error" role="alert">
              <p>{activeErrorMessage}</p>
              <button
                className="system-button"
                onClick={() => scene === "privacy" ? void loadPrivacyMap() : void loadExplorer()}
                type="button"
              >
                {isZh ? "重试" : "Retry"}
              </button>
            </div>
          ) : activeDataset ? (
            <GraphExplorer
              ariaLabel={labels[scene]}
              className="graph-workbench-explorer"
              dataset={activeDataset}
              key={scene}
              language={language}
            />
          ) : (
            <div className="graph-workbench-message" role="status">
              {isZh ? "暂无图谱数据" : "No graph data is available."}
            </div>
          )}
        </section>
      </div>
    </SystemDialog>
  );
}
