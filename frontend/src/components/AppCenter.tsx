import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragMoveEvent,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  rectSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  AlertCircle,
  AppWindow,
  Blocks,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  Folder,
  Info,
  LoaderCircle,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Play,
  Power,
  PowerOff,
  RotateCw,
  Search,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  WandSparkles,
  X,
} from "lucide-react";
import wsService from "../services/websocket";
import {
  installSkill,
  loadSkillMarket,
  setSkillAuthorization,
  setSkillEnabled,
  uninstallSkill,
  type MarketSkill,
  type SkillActivationPolicy,
  type SkillAuthorization,
  type SkillMarket,
  type SkillSurface,
} from "../services/skills";
import { SystemDialog, SystemIconButton } from "./system/SystemUI";
import "./AppCenter.css";

export type CatalogKind = "generated_app" | "skill" | "mcp";
export type CatalogStatus = "ready" | "needs_ui" | "generating" | "unavailable";

export interface CatalogAction {
  id: string;
  title: string;
  description?: string;
  input_schema: {
    type?: string;
    required?: string[];
    properties?: Record<string, { type?: string; title?: string; description?: string; default?: unknown; enum?: unknown[] }>;
  };
  result_schema?: Record<string, unknown>;
  recovery?: "manual" | "restart_safe";
}

export interface CatalogItem {
  catalog_id: string;
  kind: CatalogKind;
  title: string;
  description: string;
  version: string;
  provider: string;
  tags: string[];
  icon?: string | null;
  accent?: string | null;
  ui_app_id?: string | null;
  launch_mode?: "ui" | "actions" | "details";
  surfaces?: SkillSurface[];
  actions?: CatalogAction[];
  status: CatalogStatus;
  skill?: {
    enabled: boolean;
    digest: string;
    source: string;
    verified: boolean;
    installed_at: string;
    ontology_refs: string[];
    license?: string | null;
    compatibility?: string | null;
    registry_revision?: number;
    authorization?: SkillAuthorization;
  };
}

type InstructionSkillItem = CatalogItem & {
  kind: "skill";
  launch_mode: "details";
  surfaces: SkillSurface[];
};

export interface AppFolder {
  id: string;
  name: string;
  items: string[];
}

interface AppStoreState {
  version: number;
  revision: number;
  items: CatalogItem[];
  root: string[];
  folders: AppFolder[];
}

interface AppCenterProps {
  isOpen: boolean;
  mode?: "home" | "overlay";
  onClose: () => void;
  pinnedWidgetIds: string[];
  onPinWidget: (id: string) => void;
  onUnpinWidget: (id: string) => void;
  onRunFullscreen: (id: string) => void;
  onRunCreated?: (run: { id: string }) => void;
  onAppUpdated?: (id: string) => void | Promise<void>;
  language?: "zh" | "en";
  headerActions?: React.ReactNode;
}

type FilterKind = "all" | CatalogKind;
type AppCenterSection = "installed" | "discover";
type AppEditorState = {
  mode: "rename" | "configure";
  itemId: string;
  title: string;
  description: string;
  version: string;
  tags: string;
};

const API_BASE = `http://${window.location.hostname}:8000`;
const FALLBACK_ACCENTS = ["#7c5cff", "#12b8a6", "#f59e58", "#e85d9e", "#4f8cff", "#76b852"];

function accentFor(item: CatalogItem): string {
  if (item.accent) return item.accent;
  let hash = 0;
  for (const char of item.catalog_id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return FALLBACK_ACCENTS[hash % FALLBACK_ACCENTS.length];
}

function isInstructionSkill(
  item: CatalogItem | undefined,
): item is InstructionSkillItem {
  return Boolean(
    item
    && item.kind === "skill"
    && item.launch_mode === "details"
    && item.surfaces?.includes("agent_context"),
  );
}

interface SkillAuthorizationView {
  trusted: boolean;
  external: boolean;
  authorized: boolean;
  quarantined: boolean;
  digestChanged: boolean;
  activationPolicy: SkillActivationPolicy;
}

function skillAuthorizationView(skill: {
  verified: boolean;
  digest: string;
  authorization?: SkillAuthorization;
}): SkillAuthorizationView {
  const authorization = skill.authorization;
  // Legacy verified bundled skills predate authorization metadata. Keep them
  // trusted while failing closed for every unverified legacy record.
  const trusted = authorization?.state === "trusted"
    || (!authorization && skill.verified);
  const activationPolicy = authorization?.activation_policy ?? "none";
  const digestChanged = Boolean(
    authorization?.authorized_digest
    && authorization.authorized_digest !== skill.digest,
  ) || (
    authorization?.state === "authorized"
    && authorization.authorized_digest !== skill.digest
  );
  const authorizationRequired = !trusted && Boolean(
    authorization?.requires_reauthorization || digestChanged,
  );
  const authorized = !trusted
    && authorization?.state === "authorized"
    && !authorizationRequired
    && (activationPolicy === "explicit_only" || activationPolicy === "implicit");

  return {
    trusted,
    external: !trusted,
    authorized,
    quarantined: !trusted && !authorized,
    digestChanged,
    activationPolicy,
  };
}

function marketSkillAuthorizationView(skill: MarketSkill): SkillAuthorizationView {
  return skillAuthorizationView({
    verified: skill.provenance.verified,
    digest: skill.provenance.digest,
    authorization: skill.authorization,
  });
}

function skillAsCatalogItem(skill: MarketSkill): CatalogItem {
  return {
    catalog_id: skill.catalog_id,
    kind: "skill",
    title: skill.title,
    description: skill.description,
    version: skill.version,
    provider: skill.provider,
    tags: skill.tags,
    icon: skill.icon,
    accent: skill.accent,
    launch_mode: "details",
    surfaces: skill.surfaces,
    status: "ready",
  };
}

function ItemGlyph({ item, size = 34 }: { item: CatalogItem; size?: number }) {
  if (item.icon) return <span className="app-center-emoji">{item.icon}</span>;
  if (item.kind === "skill") return <WandSparkles size={size} strokeWidth={1.65} />;
  if (item.kind === "mcp") return <Blocks size={size} strokeWidth={1.65} />;
  return <AppWindow size={size} strokeWidth={1.65} />;
}

function AppIcon({ item, compact = false }: { item: CatalogItem; compact?: boolean }) {
  const accent = accentFor(item);
  return (
    <div
      className={`app-center-icon ${compact ? "is-compact" : ""}`}
      style={{
        "--app-accent": accent,
        "--app-accent-soft": `${accent}55`,
      } as React.CSSProperties}
    >
      <div className="app-center-icon-shine" />
      <ItemGlyph item={item} size={compact ? 24 : 36} />
      {item.status === "generating" && <LoaderCircle className="app-center-icon-spinner" size={20} />}
      {item.status === "needs_ui" && item.launch_mode !== "actions" && <Sparkles className="app-center-icon-badge" size={16} />}
    </div>
  );
}

function FolderIcon({ items }: { folder: AppFolder; items: CatalogItem[] }) {
  return (
    <div className="app-center-folder-icon" aria-hidden="true">
      <div className="app-center-folder-grid">
        {items.slice(0, 4).map((item) => (
          <AppIcon key={item.catalog_id} item={item} compact />
        ))}
      </div>
    </div>
  );
}

interface TileProps {
  entryId: string;
  item?: CatalogItem;
  folder?: AppFolder;
  folderItems?: CatalogItem[];
  isZh: boolean;
  onActivate: () => void;
  onMenu?: (position: { clientX: number; clientY: number }) => void;
  dragListeners?: Record<string, any>;
  dragAttributes?: Record<string, any>;
  setNodeRef?: (node: HTMLElement | null) => void;
  style?: React.CSSProperties;
  isDragging?: boolean;
}

function AppTileView({
  entryId,
  item,
  folder,
  folderItems = [],
  isZh,
  onActivate,
  onMenu,
  dragListeners,
  dragAttributes,
  setNodeRef,
  style,
  isDragging,
}: TileProps) {
  const longPressRef = useRef<{
    timer: number;
    pointerId: number;
    x: number;
    y: number;
  } | null>(null);
  const suppressClickRef = useRef(false);
  const clearLongPress = () => {
    if (longPressRef.current) window.clearTimeout(longPressRef.current.timer);
    longPressRef.current = null;
  };
  const handleIconPointerDown = (event: React.PointerEvent<HTMLSpanElement>) => {
    if (!onMenu || event.button !== 0) return;
    event.stopPropagation();
    clearLongPress();
    const gesture = {
      timer: 0,
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
    };
    gesture.timer = window.setTimeout(() => {
      suppressClickRef.current = true;
      onMenu({ clientX: gesture.x, clientY: gesture.y });
    }, 500);
    longPressRef.current = gesture;
  };
  const handleIconPointerMove = (event: React.PointerEvent<HTMLSpanElement>) => {
    event.stopPropagation();
    const gesture = longPressRef.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    if (Math.hypot(event.clientX - gesture.x, event.clientY - gesture.y) > 10) clearLongPress();
  };
  const handleIconPointerEnd = (event: React.PointerEvent<HTMLSpanElement>) => {
    event.stopPropagation();
    clearLongPress();
  };
  const title = item?.title ?? folder?.name ?? "";
  const detailsOnly = isInstructionSkill(item);
  const authorization = detailsOnly && item.skill
    ? skillAuthorizationView(item.skill)
    : null;
  return (
    <button
      ref={setNodeRef as React.Ref<HTMLButtonElement>}
      type="button"
      data-launcher-entry={entryId}
      className={`app-center-tile ${isDragging ? "is-dragging" : ""}`}
      style={style}
      onClick={(event) => {
        if (suppressClickRef.current) {
          suppressClickRef.current = false;
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        onActivate();
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        onMenu?.({ clientX: event.clientX, clientY: event.clientY });
      }}
      onPointerDown={(event) => dragListeners?.onPointerDown?.(event)}
      aria-label={
        folder
          ? `${isZh ? "打开文件夹" : "Open folder"} ${title}`
          : detailsOnly
            ? `${isZh ? "查看技能" : "View skill"} ${title}`
          : `${isZh ? "打开" : "Open"} ${title}`
      }
      {...dragAttributes}
      {...Object.fromEntries(Object.entries(dragListeners ?? {}).filter(([key]) => key !== "onPointerDown"))}
    >
      <span
        className="app-center-tile-visual"
        data-app-icon={item ? entryId : undefined}
        onPointerDown={handleIconPointerDown}
        onPointerMove={handleIconPointerMove}
        onPointerUp={handleIconPointerEnd}
        onPointerCancel={handleIconPointerEnd}
      >
        {item ? <AppIcon item={item} /> : folder ? <FolderIcon folder={folder} items={folderItems} /> : null}
      </span>
      <span className="app-center-tile-title">{title}</span>
      {item?.status === "needs_ui" && item.launch_mode !== "actions" && (
        <span className="app-center-tile-status">{isZh ? "需要界面" : "Needs UI"}</span>
      )}
      {detailsOnly && (
        <span className="app-center-tile-status">
          {authorization?.digestChanged
            ? (isZh ? "需要重新授权" : "Reauthorization required")
            : authorization?.quarantined
              ? (isZh ? "已隔离" : "Quarantined")
              : authorization?.authorized
                ? authorization.activationPolicy === "explicit_only"
                  ? (isZh ? "仅 /skill" : "/skill only")
                  : (isZh ? "自动匹配" : "Auto matching")
                : item.skill?.enabled === false
                  ? (isZh ? "已停用" : "Disabled")
                  : item.status === "unavailable"
                    ? (isZh ? "不可用" : "Unavailable")
                    : (isZh ? "Agent 技能" : "Agent skill")}
        </span>
      )}
    </button>
  );
}

function SortableTile(props: Omit<TileProps, "dragListeners" | "dragAttributes" | "setNodeRef" | "style" | "isDragging">) {
  const sortable = useSortable({ id: props.entryId });
  return (
    <AppTileView
      {...props}
      setNodeRef={sortable.setNodeRef}
      dragListeners={sortable.listeners as Record<string, any>}
      dragAttributes={sortable.attributes as Record<string, any>}
      isDragging={sortable.isDragging}
      style={{
        transform: CSS.Transform.toString(sortable.transform),
        transition: sortable.transition,
      }}
    />
  );
}

function FolderExitDrop({ isZh }: { isZh: boolean }) {
  const { setNodeRef, isOver } = useDroppable({ id: "folder-exit" });
  return (
    <div ref={setNodeRef} className={`app-center-folder-exit ${isOver ? "is-over" : ""}`}>
      {isZh ? "拖到这里移出文件夹" : "Drop here to move out"}
    </div>
  );
}

function MarketSkillCard({
  skill,
  busy,
  isZh,
  onInstall,
}: {
  skill: MarketSkill;
  busy: boolean;
  isZh: boolean;
  onInstall: () => void;
}) {
  const item = skillAsCatalogItem(skill);
  const authorization = marketSkillAuthorizationView(skill);
  const installed = skill.install_state === "installed";
  const updateAvailable = skill.install_state === "update_available";
  const marketOlder = skill.install_state === "market_older";
  const integrityConflict = skill.install_state === "integrity_conflict";
  const incompatible = skill.package_compatibility?.status === "incompatible";
  const blocked = marketOlder || integrityConflict || incompatible;
  const contentRevision = skill.catalog_source?.update_strategy === "content_hash"
    ? skill.catalog_source.source_revision
    : null;
  const displayedVersion = contentRevision
    ? `commit ${contentRevision.slice(0, 12)}`
    : skill.version;
  const displayedInstalledVersion = (
    contentRevision && skill.installed_version
      ? skill.installed_version.slice(0, 12)
      : skill.installed_version
  );
  const actionLabel = installed
    ? (isZh ? "已安装" : "Installed")
    : updateAvailable
      ? (isZh ? "更新" : "Update")
      : marketOlder
        ? (isZh ? "已安装较新版本" : "Newer version installed")
        : integrityConflict
          ? (isZh ? "版本内容冲突" : "Version conflict")
          : incompatible
            ? (isZh ? "当前版本不兼容" : "Incompatible")
          : (isZh ? "安装" : "Install");
  return (
    <article className="app-center-market-card">
      <header>
        <AppIcon item={item} compact />
        <div>
          <h2>{skill.title}</h2>
          <p>{skill.provider} · {displayedVersion}</p>
        </div>
        <span className={`app-center-market-trust ${skill.provenance.verified ? "is-verified" : ""}`}>
          {skill.provenance.verified ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
          {skill.provenance.verified
            ? (isZh ? "已验证" : "Verified")
            : (isZh ? "未验证" : "Not verified")}
        </span>
      </header>
      <p className="app-center-market-description">{skill.description}</p>
      {authorization.external && (
        <div
          className={`app-center-market-risk ${authorization.authorized ? "is-authorized" : ""}`}
          role="note"
        >
          {authorization.digestChanged || updateAvailable
            ? <RotateCw size={15} />
            : authorization.authorized
              ? <ShieldCheck size={15} />
              : <ShieldAlert size={15} />}
          <p>
            {authorization.digestChanged || updateAvailable
              ? (isZh
                ? "此版本的内容摘要已变化。更新后会停用并隔离，必须针对新摘要重新授权。"
                : "This version changes the content digest. Updating disables and quarantines it until the new digest is authorized.")
              : authorization.authorized
                ? (authorization.activationPolicy === "explicit_only"
                  ? (isZh ? "外部技能已授权，但只能通过 /skill 明确使用。" : "External skill authorized for explicit /skill use only.")
                  : (isZh ? "外部技能已获自动匹配授权。" : "External skill authorized for automatic matching."))
                : (isZh
                  ? "外部技能安装后会保持停用并进入隔离区；审查详情并授权前，不会注入 Agent 上下文。"
                  : "External skills install disabled and quarantined. They cannot enter agent context until you review and authorize them.")}
          </p>
        </div>
      )}
      <div className="app-center-market-meta">
        <span>{isZh ? "按需 Agent 上下文" : "On-demand agent context"}</span>
        {skill.catalog_source && (
          <span>
            {skill.catalog_source.kind === "github" ? "GitHub" : skill.catalog_source.kind}
            {" · "}
            {skill.catalog_source.id}
          </span>
        )}
        {skill.package_compatibility && (
          <span>{skill.package_compatibility.profile}</span>
        )}
        {updateAvailable && (
          <span className="is-update">
            <span>{isZh ? "有可用更新" : "Update available"}</span>
            {displayedInstalledVersion
              ? ` · ${displayedInstalledVersion} → ${contentRevision?.slice(0, 12) ?? skill.version}`
              : ""}
          </span>
        )}
        {marketOlder && (
          <span>
            {isZh ? "Market 版本较旧，已阻止降级" : "Market version is older; downgrade blocked"}
          </span>
        )}
        {integrityConflict && (
          <span>
            {isZh ? "同一版本的内容摘要不同" : "Same version has a different digest"}
          </span>
        )}
        {incompatible && (
          <span>
            {skill.package_compatibility?.reasons[0]
              ?? (isZh ? "当前运行环境不支持该包" : "This package is not supported by the current runtime")}
          </span>
        )}
      </div>
      {skill.ontology_refs.length > 0 && (
        <div className="app-center-market-refs" aria-label={isZh ? "本体引用" : "Ontology references"}>
          {skill.ontology_refs.map((reference) => <span key={reference}>{reference}</span>)}
        </div>
      )}
      <dl className="app-center-market-provenance">
        <div><dt>{isZh ? "来源" : "Source"}</dt><dd title={skill.provenance.source}>{skill.provenance.source}</dd></div>
        {skill.catalog_source?.source_revision && (
          <div>
            <dt>{isZh ? "版本钉住" : "Revision"}</dt>
            <dd title={skill.catalog_source.source_revision}>
              {skill.catalog_source.source_revision.slice(0, 12)}
            </dd>
          </div>
        )}
        {skill.catalog_source?.upstream_hash && (
          <div>
            <dt>{isZh ? "上游摘要" : "Upstream"}</dt>
            <dd title={skill.catalog_source.upstream_hash}>
              {skill.catalog_source.upstream_hash}
            </dd>
          </div>
        )}
        <div><dt>Digest</dt><dd title={skill.provenance.digest}>{skill.provenance.digest}</dd></div>
      </dl>
      <button
        type="button"
        className="app-center-market-install"
        aria-label={`${actionLabel} ${skill.title}`}
        disabled={installed || blocked || busy}
        onClick={onInstall}
      >
        {busy
          ? <LoaderCircle className="animate-spin" size={15} />
          : installed
            ? <CheckCircle2 size={15} />
            : updateAvailable
              ? <RotateCw size={15} />
              : blocked
                ? <ShieldAlert size={15} />
                : <Download size={15} />}
        {busy ? (isZh ? "处理中…" : "Working…") : actionLabel}
      </button>
    </article>
  );
}

export const AppCenter: React.FC<AppCenterProps> = ({
  isOpen,
  mode = "overlay",
  onClose,
  pinnedWidgetIds,
  onPinWidget,
  onUnpinWidget,
  onRunFullscreen,
  onRunCreated,
  onAppUpdated,
  language = "zh",
  headerActions,
}) => {
  const isZh = language === "zh";
  const [section, setSection] = useState<AppCenterSection>("installed");
  const [store, setStore] = useState<AppStoreState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [market, setMarket] = useState<SkillMarket | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState("");
  const [skillBusyId, setSkillBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKind>("all");
  const [page, setPage] = useState(0);
  const [openFolderId, setOpenFolderId] = useState<string | null>(null);
  const [detailsId, setDetailsId] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ itemId: string; x: number; y: number } | null>(null);
  const [pageCapacity, setPageCapacity] = useState(24);
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null);
  const [actionInput, setActionInput] = useState<Record<string, unknown>>({});
  const [actionError, setActionError] = useState("");
  const [actionSubmitting, setActionSubmitting] = useState(false);
  const [editor, setEditor] = useState<AppEditorState | null>(null);
  const [editorError, setEditorError] = useState("");
  const [editorSaving, setEditorSaving] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const overRef = useRef<{ id: string | null; since: number }>({ id: null, since: 0 });
  const pageFlipRef = useRef(0);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const fetchStore = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/app-store`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setStore(await response.json());
    } catch (fetchError) {
      console.error("Unable to load App Center", fetchError);
      setError(isZh ? "无法载入应用目录，请稍后重试。" : "The app catalog could not be loaded. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [isZh]);

  const fetchMarket = useCallback(async () => {
    setMarketLoading(true);
    setMarketError("");
    try {
      setMarket(await loadSkillMarket(API_BASE));
    } catch (fetchError) {
      console.error("Unable to load skill market", fetchError);
      setMarketError(
        fetchError instanceof Error
          ? fetchError.message
          : (isZh ? "无法载入技能市场。" : "The skill market could not be loaded."),
      );
    } finally {
      setMarketLoading(false);
    }
  }, [isZh]);

  useEffect(() => {
    if (isOpen || mode === "home") fetchStore();
  }, [isOpen, mode, fetchStore]);

  useEffect(() => {
    if ((isOpen || mode === "home") && section === "discover") fetchMarket();
  }, [fetchMarket, isOpen, mode, section]);

  useEffect(() => {
    const refresh = () => {
      void fetchStore();
      if (section === "discover") void fetchMarket();
    };
    window.addEventListener("app-store-refresh", refresh);
    return () => window.removeEventListener("app-store-refresh", refresh);
  }, [fetchMarket, fetchStore, section]);

  useEffect(() => {
    const updateCapacity = () => {
      if (window.innerWidth < 640) setPageCapacity(Number.MAX_SAFE_INTEGER);
      else if (window.innerWidth < 1100) setPageCapacity(15);
      else setPageCapacity(24);
    };
    updateCapacity();
    window.addEventListener("resize", updateCapacity);
    return () => window.removeEventListener("resize", updateCapacity);
  }, []);

  const itemsById = useMemo(
    () => new Map((store?.items ?? []).map((item) => [item.catalog_id, item])),
    [store?.items]
  );
  const foldersById = useMemo(
    () => new Map((store?.folders ?? []).map((folder) => [folder.id, folder])),
    [store?.folders]
  );

  const filteredItems = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase(language);
    return (store?.items ?? []).filter((item) => {
      if (filter !== "all" && item.kind !== filter) return false;
      if (!normalized) return true;
      return [item.title, item.description, item.provider, ...item.tags]
        .join(" ")
        .toLocaleLowerCase(language)
        .includes(normalized);
    });
  }, [store?.items, query, filter, language]);

  const filteredMarketSkills = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase(language);
    if (!normalized) return market?.items ?? [];
    return (market?.items ?? []).filter((skill) =>
      [
        skill.name,
        skill.title,
        skill.description,
        skill.provider,
        skill.license ?? "",
        skill.compatibility ?? "",
        ...skill.tags,
        ...skill.ontology_refs,
      ]
        .join(" ")
        .toLocaleLowerCase(language)
        .includes(normalized)
    );
  }, [language, market?.items, query]);

  const isSearching = Boolean(query.trim()) || filter !== "all";
  const rootEntries = store?.root ?? [];
  const pageCount = Math.max(1, Math.ceil(rootEntries.length / pageCapacity));
  const pageEntries = isSearching
    ? filteredItems.map((item) => item.catalog_id)
    : rootEntries.slice(page * pageCapacity, (page + 1) * pageCapacity);

  useEffect(() => {
    if (page >= pageCount) setPage(pageCount - 1);
  }, [page, pageCount]);

  const persistLayout = useCallback(
    async (nextRoot: string[], nextFolders: AppFolder[]) => {
      if (!store) return;
      const previous = store;
      setStore({ ...store, root: nextRoot, folders: nextFolders });
      try {
        const response = await fetch(`${API_BASE}/api/app-store/layout`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: store.revision, root: nextRoot, folders: nextFolders }),
        });
        const payload = await response.json();
        if (response.status === 409) {
          setStore(payload.detail.state);
          setNotice(isZh ? "布局已在另一台设备更新，已载入最新版本。" : "Layout changed elsewhere. The latest version was loaded.");
          return;
        }
        if (!response.ok) throw new Error(payload.detail ?? `HTTP ${response.status}`);
        setStore(payload);
      } catch (saveError) {
        console.error("Unable to save App Center layout", saveError);
        setStore(previous);
        setNotice(isZh ? "布局保存失败，已恢复原来的排列。" : "Layout could not be saved. Your previous order was restored.");
      }
    },
    [store, isZh]
  );

  const openAppEditor = (item: CatalogItem, mode: AppEditorState["mode"]) => {
    setMenu(null);
    setEditorError("");
    setEditor({
      mode,
      itemId: item.catalog_id,
      title: item.title,
      description: item.description,
      version: item.version,
      tags: item.tags.join(", "),
    });
  };

  const saveAppEditor = async () => {
    if (!editor) return;
    const item = itemsById.get(editor.itemId);
    if (!item?.ui_app_id || item.kind !== "generated_app") return;
    const intents = [...new Set(
      editor.tags
        .split(/[,\n]/)
        .map((tag) => tag.trim())
        .filter(Boolean)
    )];
    const update = editor.mode === "rename"
      ? { title: editor.title.trim() }
      : {
          description: editor.description.trim(),
          app_version: editor.version.trim(),
          intents,
        };
    setEditorSaving(true);
    setEditorError("");
    try {
      const response = await fetch(`${API_BASE}/api/apps/${encodeURIComponent(item.ui_app_id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update),
      });
      const payload = await response.json();
      if (!response.ok) {
        const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
        throw new Error(detail || `HTTP ${response.status}`);
      }
      setStore((current) => current ? {
        ...current,
        items: current.items.map((candidate) => candidate.catalog_id === item.catalog_id ? {
          ...candidate,
          title: payload.title ?? candidate.title,
          description: payload.description ?? candidate.description,
          version: payload.app_version ?? candidate.version,
          tags: payload.intents ?? candidate.tags,
        } : candidate),
      } : current);
      setEditor(null);
      setNotice(editor.mode === "rename"
        ? (isZh ? "应用已重命名。" : "App renamed.")
        : (isZh ? "应用属性已保存。" : "App properties saved."));
      Promise.resolve(onAppUpdated?.(item.ui_app_id)).catch((refreshError) => {
        console.error("Unable to refresh the updated App window", refreshError);
      });
    } catch (saveError) {
      setEditorError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setEditorSaving(false);
    }
  };

  const activateItem = (item: CatalogItem) => {
    setMenu(null);
    if (isInstructionSkill(item)) {
      setDetailsId(item.catalog_id);
      return;
    }
    if (item.ui_app_id && item.status === "ready") {
      onRunFullscreen(item.ui_app_id);
      return;
    }
    setDetailsId(item.catalog_id);
    const action = item.actions?.[0];
    setSelectedActionId(action?.id ?? null);
    setActionInput(Object.fromEntries(Object.entries(action?.input_schema.properties || {}).map(([key, schema]) => [key, schema.default ?? (schema.type === "boolean" ? false : "")])));
  };

  const selectAction = (action: CatalogAction) => {
    setSelectedActionId(action.id);
    setActionError("");
    setActionInput(Object.fromEntries(Object.entries(action.input_schema.properties || {}).map(([key, schema]) => [key, schema.default ?? (schema.type === "boolean" ? false : "")])));
  };

  const submitAction = async (item: CatalogItem, action: CatalogAction) => {
    setActionSubmitting(true);
    setActionError("");
    try {
      const normalized = Object.fromEntries(Object.entries(actionInput).map(([key, value]) => {
        const type = action.input_schema.properties?.[key]?.type;
        if (type === "number" || type === "integer") return [key, value === "" ? null : Number(value)];
        return [key, value];
      }));
      const response = await fetch(`${API_BASE}/api/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ catalog_id: item.catalog_id, action_id: action.id, input: normalized, source: { type: "user", id: "app-center" } }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      setDetailsId(null);
      onClose();
      onRunCreated?.(payload);
    } catch (submitError) {
      setActionError(submitError instanceof Error ? submitError.message : String(submitError));
    } finally {
      setActionSubmitting(false);
    }
  };

  const requestGeneration = (item: CatalogItem) => {
    if (isInstructionSkill(item)) return;
    wsService.sendMessage({ type: "generate_capability_ui", catalog_id: item.catalog_id });
    setStore((current) => current ? {
      ...current,
      items: current.items.map((candidate) =>
        candidate.catalog_id === item.catalog_id ? { ...candidate, status: "generating" } : candidate
      ),
    } : current);
    setDetailsId(null);
    onClose();
  };

  const deleteGeneratedApp = async (item: CatalogItem) => {
    if (!item.ui_app_id) return;
    const message = isZh
      ? `确定要永久卸载“${item.title}”吗？其源文件和状态数据会被删除。`
      : `Permanently uninstall “${item.title}”? Its source files and state will be deleted.`;
    if (!window.confirm(message)) return;
    const response = await fetch(`${API_BASE}/api/apps/${item.ui_app_id}`, { method: "DELETE" });
    if (response.ok) {
      onUnpinWidget(item.ui_app_id);
      setMenu(null);
      await fetchStore();
    }
  };

  const deleteCapabilityUi = async (item: CatalogItem) => {
    if (!window.confirm(isZh ? "删除生成的界面并保留原能力？" : "Delete the generated UI and keep the capability?")) return;
    const response = await fetch(`${API_BASE}/api/capabilities/${encodeURIComponent(item.catalog_id)}/ui`, {
      method: "DELETE",
    });
    if (response.ok) {
      if (item.ui_app_id) onUnpinWidget(item.ui_app_id);
      setMenu(null);
      await fetchStore();
    }
  };

  const installMarketSkill = async (skill: MarketSkill) => {
    setSkillBusyId(`market:${skill.market_id}`);
    setNotice("");
    try {
      await installSkill(API_BASE, skill.market_id, market?.revision);
      await Promise.all([fetchMarket(), fetchStore()]);
      const external = marketSkillAuthorizationView(skill).external;
      setNotice(
        skill.install_state === "update_available"
          ? external
            ? (isZh
              ? `“${skill.title}”已更新并重新隔离；请审查新版本后重新授权。`
              : `${skill.title} was updated and quarantined again. Review the new version before reauthorizing it.`)
            : (isZh ? `“${skill.title}”已更新。` : `${skill.title} was updated.`)
          : external
            ? (isZh
              ? `“${skill.title}”已安装但仍处于隔离状态。请从详情页审查并授权。`
              : `${skill.title} was installed in quarantine. Review and authorize it from its details.`)
            : (isZh ? `“${skill.title}”已安装到当前工作区。` : `${skill.title} was installed in this workspace.`),
      );
    } catch (installError) {
      await Promise.allSettled([fetchMarket(), fetchStore()]);
      setNotice(installError instanceof Error ? installError.message : String(installError));
    } finally {
      setSkillBusyId(null);
    }
  };

  const toggleInstructionSkill = async (item: CatalogItem) => {
    if (!isInstructionSkill(item) || !item.skill) return;
    const enabled = !item.skill.enabled;
    setSkillBusyId(`installed:${item.catalog_id}`);
    setNotice("");
    try {
      await setSkillEnabled(
        API_BASE,
        item.catalog_id,
        enabled,
        item.skill.registry_revision,
      );
      await fetchStore();
      setNotice(
        enabled
          ? (isZh ? `“${item.title}”已启用。` : `${item.title} is enabled.`)
          : (isZh ? `“${item.title}”已停用。` : `${item.title} is disabled.`),
      );
      setMenu(null);
    } catch (updateError) {
      await Promise.allSettled([
        fetchStore(),
        market ? fetchMarket() : Promise.resolve(),
      ]);
      setNotice(updateError instanceof Error ? updateError.message : String(updateError));
    } finally {
      setSkillBusyId(null);
    }
  };

  const changeInstructionSkillAuthorization = async (
    item: CatalogItem,
    activationPolicy: SkillActivationPolicy,
  ) => {
    if (!isInstructionSkill(item) || !item.skill) return;
    const confirmation = activationPolicy === "none"
      ? (isZh
        ? `撤销“${item.title}”的 agent.context.inject 授权？该技能会立即停用并回到隔离状态；已经开始的回复无法撤回。`
        : `Revoke the agent.context.inject grant for “${item.title}”? It will be disabled immediately and returned to quarantine; an already-started response cannot be recalled.`)
      : activationPolicy === "explicit_only"
        ? (isZh
          ? `授予“${item.title}”agent.context.inject 权限，仅在你明确输入 /skill 时影响 Agent？这不会授予工具、数据、网络或文件权限。`
          : `Grant agent.context.inject to “${item.title}” only when you explicitly use /skill? This grants no tool, data, network, or file access.`)
        : (isZh
          ? `授予“${item.title}”自动匹配的 agent.context.inject 权限？外部文本可能影响 Agent 的建议；这不会授予工具、数据、网络或文件权限。`
          : `Grant automatic agent.context.inject access to “${item.title}”? External text may influence agent suggestions; this grants no tool, data, network, or file access.`);
    if (!window.confirm(confirmation)) return;

    setSkillBusyId(`installed:${item.catalog_id}`);
    setNotice("");
    try {
      await setSkillAuthorization(
        API_BASE,
        item.catalog_id,
        activationPolicy,
        item.skill.digest,
        item.skill.registry_revision,
      );
      await Promise.all([
        fetchStore(),
        market ? fetchMarket() : Promise.resolve(),
      ]);
      setNotice(
        activationPolicy === "none"
          ? (isZh
            ? `“${item.title}”的授权已撤销，技能已停用并隔离。`
            : `${item.title}'s authorization was revoked. The skill is disabled and quarantined.`)
          : activationPolicy === "explicit_only"
            ? (isZh
              ? `“${item.title}”已授权，仅可通过 /skill 明确使用。`
              : `${item.title} is authorized for explicit /skill use only.`)
            : (isZh
              ? `“${item.title}”已获自动匹配授权。`
              : `${item.title} is authorized for automatic matching.`),
      );
    } catch (authorizationError) {
      await Promise.allSettled([
        fetchStore(),
        market ? fetchMarket() : Promise.resolve(),
      ]);
      setNotice(
        authorizationError instanceof Error
          ? authorizationError.message
          : String(authorizationError),
      );
    } finally {
      setSkillBusyId(null);
    }
  };

  const removeInstructionSkill = async (item: CatalogItem) => {
    if (!isInstructionSkill(item)) return;
    const confirmed = window.confirm(
      isZh
        ? `确定要卸载“${item.title}”吗？Agent 将不再加载此技能。`
        : `Uninstall “${item.title}”? The agent will no longer load this skill.`,
    );
    if (!confirmed) return;
    setSkillBusyId(`installed:${item.catalog_id}`);
    setNotice("");
    try {
      await uninstallSkill(
        API_BASE,
        item.catalog_id,
        item.skill?.registry_revision,
      );
      setDetailsId(null);
      setMenu(null);
      await Promise.all([
        fetchStore(),
        market ? fetchMarket() : Promise.resolve(),
      ]);
      setNotice(isZh ? `“${item.title}”已卸载。` : `${item.title} was uninstalled.`);
    } catch (uninstallError) {
      await Promise.allSettled([
        fetchStore(),
        market ? fetchMarket() : Promise.resolve(),
      ]);
      setNotice(uninstallError instanceof Error ? uninstallError.message : String(uninstallError));
    } finally {
      setSkillBusyId(null);
    }
  };

  const createFolder = (activeEntry: string, overEntry: string) => {
    if (!store || activeEntry === overEntry || !itemsById.has(activeEntry) || !itemsById.has(overEntry)) return;
    const folderId = crypto.randomUUID();
    const activeIndex = store.root.indexOf(activeEntry);
    const overIndex = store.root.indexOf(overEntry);
    const insertAt = Math.min(activeIndex, overIndex);
    const nextRoot = store.root.filter((entry) => entry !== activeEntry && entry !== overEntry);
    nextRoot.splice(insertAt, 0, `folder:${folderId}`);
    const nextFolders = [
      ...store.folders,
      { id: folderId, name: isZh ? "新建文件夹" : "New Folder", items: [overEntry, activeEntry] },
    ];
    persistLayout(nextRoot, nextFolders);
    setOpenFolderId(folderId);
  };

  const moveRootItemIntoFolder = (itemId: string, folderId: string) => {
    if (!store || !itemsById.has(itemId)) return;
    const nextRoot = store.root.filter((entry) => entry !== itemId);
    const nextFolders = store.folders.map((folder) =>
      folder.id === folderId ? { ...folder, items: [...folder.items, itemId] } : folder
    );
    persistLayout(nextRoot, nextFolders);
  };

  const handleRootDragStart = (event: DragStartEvent) => {
    setActiveId(String(event.active.id));
    overRef.current = { id: null, since: Date.now() };
  };
  const handleRootDragOver = (event: DragOverEvent) => {
    const overId = event.over ? String(event.over.id) : null;
    if (overId !== overRef.current.id) overRef.current = { id: overId, since: Date.now() };
  };
  const handleRootDragMove = (event: DragMoveEvent) => {
    const now = Date.now();
    if (now - pageFlipRef.current < 600 || pageCount <= 1) return;
    const left = event.active.rect.current.translated?.left;
    const right = event.active.rect.current.translated?.right;
    if (typeof left === "number" && left < 30 && page > 0) {
      pageFlipRef.current = now;
      setPage((value) => value - 1);
    } else if (typeof right === "number" && right > window.innerWidth - 30 && page < pageCount - 1) {
      pageFlipRef.current = now;
      setPage((value) => value + 1);
    }
  };
  const handleRootDragEnd = (event: DragEndEvent) => {
    setActiveId(null);
    if (!store || !event.over || isSearching) return;
    const activeEntry = String(event.active.id);
    const overEntry = String(event.over.id);
    if (activeEntry === overEntry) return;
    if (overEntry.startsWith("folder:") && !activeEntry.startsWith("folder:")) {
      moveRootItemIntoFolder(activeEntry, overEntry.slice(7));
      return;
    }
    const heldOverItem = itemsById.has(overEntry) && Date.now() - overRef.current.since >= 480;
    if (!activeEntry.startsWith("folder:") && heldOverItem) {
      createFolder(activeEntry, overEntry);
      return;
    }
    const oldIndex = store.root.indexOf(activeEntry);
    const newIndex = store.root.indexOf(overEntry);
    if (oldIndex >= 0 && newIndex >= 0) persistLayout(arrayMove(store.root, oldIndex, newIndex), store.folders);
  };

  const handleFolderDragEnd = (event: DragEndEvent) => {
    setActiveId(null);
    if (!store || !openFolderId || !event.over) return;
    const folder = foldersById.get(openFolderId);
    if (!folder) return;
    const activeEntry = String(event.active.id);
    const overEntry = String(event.over.id);
    if (overEntry === "folder-exit") {
      const nextItems = folder.items.filter((id) => id !== activeEntry);
      let nextRoot = [...store.root];
      let nextFolders = store.folders.filter((candidate) => candidate.id !== folder.id);
      const folderPosition = nextRoot.indexOf(`folder:${folder.id}`);
      if (nextItems.length >= 2) {
        nextFolders.push({ ...folder, items: nextItems });
        nextRoot.splice(Math.max(0, folderPosition + 1), 0, activeEntry);
      } else {
        nextRoot = nextRoot.filter((entry) => entry !== `folder:${folder.id}`);
        nextRoot.splice(Math.max(0, folderPosition), 0, ...nextItems, activeEntry);
        setOpenFolderId(null);
      }
      persistLayout(nextRoot, nextFolders);
      return;
    }
    const oldIndex = folder.items.indexOf(activeEntry);
    const newIndex = folder.items.indexOf(overEntry);
    if (oldIndex < 0 || newIndex < 0) return;
    persistLayout(
      store.root,
      store.folders.map((candidate) =>
        candidate.id === folder.id ? { ...candidate, items: arrayMove(candidate.items, oldIndex, newIndex) } : candidate
      )
    );
  };

  useEffect(() => {
    if (!isOpen && mode !== "home") return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") {
        if (editor) setEditor(null);
        else if (activeId) setActiveId(null);
        else if (menu) setMenu(null);
        else if (detailsId) setDetailsId(null);
        else if (openFolderId) setOpenFolderId(null);
        else if (query) setQuery("");
        else if (mode !== "home") onClose();
      }
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        const tiles = Array.from(document.querySelectorAll<HTMLElement>("[data-launcher-entry]"));
        const index = tiles.indexOf(document.activeElement as HTMLElement);
        if (index < 0 || tiles.length === 0) return;
        event.preventDefault();
        const columns = window.innerWidth < 640 ? 3 : window.innerWidth < 1100 ? 5 : 8;
        const delta = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : event.key === "ArrowUp" ? -columns : columns;
        tiles[Math.max(0, Math.min(tiles.length - 1, index + delta))]?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, mode, editor, activeId, menu, detailsId, openFolderId, query, onClose]);

  if (!isOpen && mode !== "home") return null;

  const openFolder = openFolderId ? foldersById.get(openFolderId) : undefined;
  const detailsItem = detailsId ? itemsById.get(detailsId) : undefined;
  const detailsIsInstructionSkill = isInstructionSkill(detailsItem);
  const detailsSkillAuthorization = detailsIsInstructionSkill && detailsItem.skill
    ? skillAuthorizationView(detailsItem.skill)
    : null;
  const selectedAction = detailsItem?.actions?.find((action) => action.id === selectedActionId) ?? detailsItem?.actions?.[0];
  const menuItem = menu ? itemsById.get(menu.itemId) : undefined;
  const menuSkillAuthorization = isInstructionSkill(menuItem) && menuItem.skill
    ? skillAuthorizationView(menuItem.skill)
    : null;
  const activeItem = activeId ? itemsById.get(activeId) : undefined;
  const activeFolder = activeId?.startsWith("folder:") ? foldersById.get(activeId.slice(7)) : undefined;
  const filters: Array<{ id: FilterKind; zh: string; en: string }> = [
    { id: "all", zh: "全部", en: "All" },
    { id: "generated_app", zh: "App", en: "Apps" },
    { id: "skill", zh: "技能", en: "Skills" },
    { id: "mcp", zh: "MCP", en: "MCP" },
  ];

  const renderEntry = (entryId: string, sortable: boolean) => {
    const item = itemsById.get(entryId);
    const folder = entryId.startsWith("folder:") ? foldersById.get(entryId.slice(7)) : undefined;
    if (!item && !folder) return null;
    const common = {
      entryId,
      item,
      folder,
      folderItems: folder?.items.map((id) => itemsById.get(id)).filter(Boolean) as CatalogItem[] | undefined,
      isZh,
      onActivate: () => item ? activateItem(item) : setOpenFolderId(folder!.id),
      onMenu: item
        ? (position: { clientX: number; clientY: number }) => setMenu({ itemId: item.catalog_id, x: position.clientX, y: position.clientY })
        : undefined,
    };
    return sortable ? <SortableTile key={entryId} {...common} /> : <AppTileView key={entryId} {...common} />;
  };

  return (
    <div className={`app-center-shell ${mode === "home" ? "is-home" : "is-overlay"}`} role={mode === "home" ? "main" : "dialog"} aria-modal={mode === "overlay" ? "true" : undefined} aria-label={isZh ? "应用中心" : "App Center"}>
      <div className="app-center-aurora app-center-aurora-one" />
      <div className="app-center-aurora app-center-aurora-two" />
      <header className="app-center-header">
        <div className="app-center-brand">
          <div className="app-center-brand-mark"><Sparkles size={18} /></div>
          <div>
            <h1>{isZh ? "应用中心" : "App Center"}</h1>
            <p>{isZh ? "你的应用、技能与连接器" : "Your apps, skills, and connectors"}</p>
          </div>
        </div>
        <div className="app-center-search-wrap">
          <Search size={18} />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => { setQuery(event.target.value); setPage(0); }}
            placeholder={section === "discover"
              ? (isZh ? "搜索技能市场" : "Search the skill market")
              : (isZh ? "搜索应用、技能或 MCP" : "Search apps, skills, or MCP")}
            aria-label={section === "discover"
              ? (isZh ? "搜索技能市场" : "Search skill market")
              : (isZh ? "搜索应用" : "Search apps")}
          />
          <kbd>⌘ K</kbd>
        </div>
        <div className="app-center-header-actions">
          {headerActions}
          {mode !== "home" && <SystemIconButton className="app-center-close" label={isZh ? "关闭" : "Close"} onClick={onClose}><X size={19} /></SystemIconButton>}
        </div>
      </header>

      <nav className="app-center-sections" role="tablist" aria-label={isZh ? "应用中心视图" : "App Center views"}>
        {([
          { id: "installed" as const, zh: "已安装", en: "Installed" },
          { id: "discover" as const, zh: "发现技能", en: "Discover Skills" },
        ]).map((option) => (
          <button
            key={option.id}
            type="button"
            role="tab"
            aria-selected={section === option.id}
            className={section === option.id ? "is-active" : ""}
            onClick={() => {
              setSection(option.id);
              setQuery("");
              setPage(0);
              setOpenFolderId(null);
              setDetailsId(null);
              setMenu(null);
            }}
          >
            {isZh ? option.zh : option.en}
          </button>
        ))}
      </nav>

      {section === "installed" && (
        <nav className="app-center-filters" aria-label={isZh ? "应用类型" : "App types"}>
          {filters.map((option) => (
            <button
              key={option.id}
              className={filter === option.id ? "is-active" : ""}
              onClick={() => { setFilter(option.id); setPage(0); }}
            >
              {isZh ? option.zh : option.en}
            </button>
          ))}
        </nav>
      )}

      {notice && (
        <button className="app-center-notice" onClick={() => setNotice("")}>
          <AlertCircle size={16} /> {notice} <X size={14} />
        </button>
      )}

      <main className={`app-center-content ${section === "discover" ? "is-market" : ""}`}>
        {section === "discover" ? (
          marketLoading && !market ? (
            <div className="app-center-state"><LoaderCircle className="animate-spin" size={28} /><p>{isZh ? "正在载入技能市场…" : "Loading the skill market…"}</p></div>
          ) : marketError ? (
            <div className="app-center-state">
              <AlertCircle size={30} />
              <h2>{isZh ? "技能市场暂时不可用" : "Skill market unavailable"}</h2>
              <p>{marketError}</p>
              <button onClick={fetchMarket}>{isZh ? "重试" : "Try again"}</button>
            </div>
          ) : filteredMarketSkills.length === 0 ? (
            <div className="app-center-state"><Search size={30} /><h2>{isZh ? "没有找到技能" : "No skills found"}</h2><p>{isZh ? "试试更短的关键词。" : "Try a shorter search term."}</p></div>
          ) : (
            <div className="app-center-market-layout">
              {(market?.sources ?? [])
                .filter((source) => source.status === "unavailable")
                .map((source) => (
                  <div
                    className="app-center-market-source-warning"
                    role="status"
                    key={source.id}
                  >
                    <AlertCircle size={15} />
                    <p>
                      {isZh
                        ? `${source.id} 暂时不可用：${source.error ?? "来源没有返回可用快照"}`
                        : `${source.id} is unavailable: ${source.error ?? "the source returned no usable snapshot"}`}
                    </p>
                  </div>
                ))}
              <div className="app-center-market-grid">
                {filteredMarketSkills.map((skill) => (
                  <MarketSkillCard
                    key={skill.market_id}
                    skill={skill}
                    busy={skillBusyId === `market:${skill.market_id}`}
                    isZh={isZh}
                    onInstall={() => void installMarketSkill(skill)}
                  />
                ))}
              </div>
            </div>
          )
        ) : loading && !store ? (
          <div className="app-center-state"><LoaderCircle className="animate-spin" size={28} /><p>{isZh ? "正在整理你的应用…" : "Organizing your apps…"}</p></div>
        ) : error ? (
          <div className="app-center-state"><AlertCircle size={30} /><h2>{isZh ? "目录暂时不可用" : "Catalog unavailable"}</h2><p>{error}</p><button onClick={fetchStore}>{isZh ? "重试" : "Try again"}</button></div>
        ) : pageEntries.length === 0 ? (
          <div className="app-center-state"><Search size={30} /><h2>{isZh ? "没有找到结果" : "No results found"}</h2><p>{isZh ? "试试更短的关键词或其他类型。" : "Try a shorter term or a different type."}</p></div>
        ) : isSearching ? (
          <div className="app-center-grid is-search-grid">{pageEntries.map((entry) => renderEntry(entry, false))}</div>
        ) : (
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragStart={handleRootDragStart}
            onDragOver={handleRootDragOver}
            onDragMove={handleRootDragMove}
            onDragEnd={handleRootDragEnd}
            onDragCancel={() => setActiveId(null)}
          >
            <SortableContext items={pageEntries} strategy={rectSortingStrategy}>
              <div className="app-center-grid">{pageEntries.map((entry) => renderEntry(entry, true))}</div>
            </SortableContext>
            <DragOverlay>
              {activeItem ? <AppTileView entryId={activeId!} item={activeItem} isZh={isZh} onActivate={() => {}} /> : activeFolder ? <AppTileView entryId={activeId!} folder={activeFolder} folderItems={activeFolder.items.map((id) => itemsById.get(id)).filter(Boolean) as CatalogItem[]} isZh={isZh} onActivate={() => {}} /> : null}
            </DragOverlay>
          </DndContext>
        )}
      </main>

      {section === "installed" && !isSearching && pageCount > 1 && (
        <div className="app-center-pagination">
          <button onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={page === 0} aria-label={isZh ? "上一页" : "Previous page"}><ChevronLeft size={18} /></button>
          <div>{Array.from({ length: pageCount }, (_, index) => <button key={index} className={page === index ? "is-active" : ""} onClick={() => setPage(index)} aria-label={`${isZh ? "第" : "Page "}${index + 1}${isZh ? "页" : ""}`} />)}</div>
          <button onClick={() => setPage((value) => Math.min(pageCount - 1, value + 1))} disabled={page === pageCount - 1} aria-label={isZh ? "下一页" : "Next page"}><ChevronRight size={18} /></button>
        </div>
      )}

      {openFolder && (
        <div className="app-center-folder-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) setOpenFolderId(null); }}>
          <section className="app-center-folder-panel">
            <div className="app-center-folder-title-row">
              <Folder size={20} />
              <input
                value={openFolder.name}
                onChange={(event) => setStore((current) => current ? { ...current, folders: current.folders.map((folder) => folder.id === openFolder.id ? { ...folder, name: event.target.value } : folder) } : current)}
                onBlur={(event) => {
                  if (!store) return;
                  persistLayout(store.root, store.folders.map((folder) => folder.id === openFolder.id ? { ...folder, name: event.target.value || (isZh ? "文件夹" : "Folder") } : folder));
                }}
                aria-label={isZh ? "文件夹名称" : "Folder name"}
              />
              <button onClick={() => setOpenFolderId(null)} aria-label={isZh ? "关闭文件夹" : "Close folder"}><X size={19} /></button>
            </div>
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragStart={(event) => setActiveId(String(event.active.id))} onDragEnd={handleFolderDragEnd} onDragCancel={() => setActiveId(null)}>
              <SortableContext items={openFolder.items} strategy={rectSortingStrategy}>
                <div className="app-center-folder-items">{openFolder.items.map((entry) => renderEntry(entry, true))}</div>
              </SortableContext>
              <FolderExitDrop isZh={isZh} />
              <DragOverlay>{activeItem ? <AppTileView entryId={activeItem.catalog_id} item={activeItem} isZh={isZh} onActivate={() => {}} /> : null}</DragOverlay>
            </DndContext>
          </section>
        </div>
      )}

      {detailsItem && (
        <div className="app-center-details-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) setDetailsId(null); }}>
          <aside className="app-center-details">
            <button className="app-center-details-close" onClick={() => setDetailsId(null)} aria-label={isZh ? "关闭详情" : "Close details"}><X size={19} /></button>
            <AppIcon item={detailsItem} />
            <span className="app-center-kind">{detailsItem.kind === "generated_app" ? "APP" : detailsItem.kind.toUpperCase()}</span>
            <h2>{detailsItem.title}</h2>
            <p>{detailsItem.description || (isZh ? "这个能力还没有详细描述。" : "No description has been provided yet.")}</p>
            <dl>
              <div><dt>{isZh ? "来源" : "Provider"}</dt><dd>{detailsItem.provider}</dd></div>
              <div><dt>{isZh ? "版本" : "Version"}</dt><dd>{detailsItem.version}</dd></div>
              <div>
                <dt>{isZh ? "状态" : "Status"}</dt>
                <dd>
                  {detailsIsInstructionSkill
                    ? detailsSkillAuthorization?.digestChanged
                      ? (isZh ? "内容已变化，需要重新授权" : "Content changed; reauthorization required")
                      : detailsSkillAuthorization?.quarantined
                        ? (isZh ? "已停用并隔离" : "Disabled and quarantined")
                        : detailsSkillAuthorization?.authorized
                          ? detailsItem.skill?.enabled === false
                            ? (isZh ? "已授权但当前停用" : "Authorized but disabled")
                            : detailsSkillAuthorization.activationPolicy === "explicit_only"
                              ? (isZh ? "已授权：仅 /skill 明确使用" : "Authorized: explicit /skill only")
                              : (isZh ? "已授权：允许自动匹配" : "Authorized: automatic matching")
                          : detailsItem.skill?.enabled === false
                            ? (isZh ? "已停用" : "Disabled")
                            : detailsItem.status === "ready"
                              ? (isZh ? "可供 Agent 使用" : "Available to agent")
                              : (isZh ? "不可用" : "Unavailable")
                    : detailsItem.status === "ready"
                      ? (isZh ? "可使用" : "Ready")
                      : detailsItem.status === "generating"
                        ? (isZh ? "正在生成" : "Generating")
                        : detailsItem.status === "unavailable"
                          ? (isZh ? "不可用" : "Unavailable")
                          : (isZh ? "需要界面" : "Needs UI")}
                </dd>
              </div>
              {detailsIsInstructionSkill && detailsItem.skill && (
                <>
                  <div><dt>{isZh ? "技能来源" : "Source"}</dt><dd className="app-center-detail-value" title={detailsItem.skill.source}>{detailsItem.skill.source}</dd></div>
                  <div><dt>Digest</dt><dd className="app-center-detail-value" title={detailsItem.skill.digest}>{detailsItem.skill.digest}</dd></div>
                  <div><dt>{isZh ? "来源验证" : "Provenance"}</dt><dd>{detailsItem.skill.verified ? (isZh ? "已验证" : "Verified") : (isZh ? "未验证" : "Not verified")}</dd></div>
                  {detailsItem.skill.authorization?.principal_id && (
                    <div>
                      <dt>{isZh ? "授权主体" : "Grant principal"}</dt>
                      <dd
                        className="app-center-detail-value"
                        title={detailsItem.skill.authorization.principal_id}
                      >
                        {detailsItem.skill.authorization.principal_id}
                      </dd>
                    </div>
                  )}
                  {detailsItem.skill.authorization?.grant_digest && (
                    <div>
                      <dt>{isZh ? "授权摘要" : "Grant digest"}</dt>
                      <dd
                        className="app-center-detail-value"
                        title={detailsItem.skill.authorization.grant_digest}
                      >
                        {detailsItem.skill.authorization.grant_digest}
                      </dd>
                    </div>
                  )}
                  {detailsItem.skill.license && <div><dt>{isZh ? "许可证" : "License"}</dt><dd>{detailsItem.skill.license}</dd></div>}
                  {detailsItem.skill.compatibility && <div><dt>{isZh ? "兼容性" : "Compatibility"}</dt><dd>{detailsItem.skill.compatibility}</dd></div>}
                </>
              )}
            </dl>
            {detailsItem.tags.length > 0 && <div className="app-center-tags">{detailsItem.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
            {detailsIsInstructionSkill ? (
              <div className="app-center-installed-skill">
                {detailsSkillAuthorization?.external ? (
                  <div
                    className={`app-center-agent-context-note is-risk ${detailsSkillAuthorization.authorized ? "is-authorized" : ""}`}
                    role={detailsSkillAuthorization.authorized ? "note" : "alert"}
                  >
                    {detailsSkillAuthorization.authorized
                      ? <ShieldCheck size={17} />
                      : <ShieldAlert size={17} />}
                    <div>
                      <strong>
                        {detailsSkillAuthorization.digestChanged
                          ? (isZh ? "内容摘要已变化" : "Content digest changed")
                          : detailsSkillAuthorization.authorized
                            ? (isZh ? "外部技能已获授权" : "External skill authorized")
                            : (isZh ? "外部技能已隔离" : "External skill quarantined")}
                      </strong>
                      <p>
                        {detailsSkillAuthorization.digestChanged
                          ? (isZh
                            ? "旧授权不会延续到新版本。请重新审查当前摘要并选择新的上下文注入策略。"
                            : "The prior grant does not carry over to this version. Review the current digest and choose a new context-injection policy.")
                          : detailsSkillAuthorization.authorized
                            ? (isZh
                              ? "授权只允许指令文本进入 Agent 上下文，不包含工具、网络、文件或数据访问。"
                              : "This grant only permits instruction text to enter agent context. It includes no tool, network, file, or data access.")
                            : (isZh
                              ? "该技能已停用，其文本不会进入 Agent 上下文，直到你针对当前摘要明确授权。"
                              : "The skill is disabled and its text cannot enter agent context until you explicitly authorize this digest.")}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="app-center-agent-context-note">
                    <WandSparkles size={17} />
                    <p>
                      {isZh
                        ? "此技能来自受信任来源。启用后，Agent 会在相关任务中按需加载它，而不是把它作为可执行工具运行。"
                        : "This skill comes from a trusted source. When enabled, the agent loads it on demand for relevant tasks; it is not an executable tool."}
                    </p>
                  </div>
                )}
                {(detailsItem.skill?.ontology_refs.length || 0) > 0 && (
                  <section className="app-center-ontology-refs" aria-label={isZh ? "本体引用" : "Ontology references"}>
                    <h3>{isZh ? "本体引用" : "Ontology references"}</h3>
                    <div>{detailsItem.skill?.ontology_refs.map((reference) => <span key={reference}>{reference}</span>)}</div>
                  </section>
                )}
                {detailsSkillAuthorization?.external ? (
                  <section
                    className="app-center-skill-authorization"
                    aria-label={isZh ? "Agent 上下文授权" : "Agent context authorization"}
                  >
                    <h3>{isZh ? "agent.context.inject 授权" : "agent.context.inject grant"}</h3>
                    <p>
                      {isZh
                        ? "选择此 Skill 何时可以影响模型提示。命中的 turn 会进入无工具、无历史、无工作区 artifact 的语义沙盒；建议优先选择仅 /skill 明确使用。每次授权都绑定当前版本与摘要。"
                        : "Choose when this skill may influence the model prompt. A matching turn enters a semantic sandbox with no tools, history, or workspace artifacts; explicit /skill use is recommended. Every grant is bound to this version and digest."}
                    </p>
                    <button
                      type="button"
                      aria-label={isZh ? "授权仅 /skill 明确使用" : "Authorize explicit /skill use"}
                      className={`app-center-authorization-option ${
                        detailsSkillAuthorization.authorized
                        && detailsSkillAuthorization.activationPolicy === "explicit_only"
                          ? "is-active"
                          : ""
                      }`}
                      disabled={
                        !detailsItem.skill
                        || skillBusyId === `installed:${detailsItem.catalog_id}`
                        || (
                          detailsSkillAuthorization.authorized
                          && detailsSkillAuthorization.activationPolicy === "explicit_only"
                          && detailsItem.skill.enabled !== false
                        )
                      }
                      onClick={() => void changeInstructionSkillAuthorization(detailsItem, "explicit_only")}
                    >
                      <ShieldCheck size={17} />
                      <span>
                        <strong>{isZh ? "授权仅 /skill 明确使用（推荐）" : "Authorize explicit /skill use (recommended)"}</strong>
                        <small>{isZh ? "只有你的明确命令才能进入隔离 turn" : "Only your explicit command can enter a sandboxed turn"}</small>
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label={isZh ? "允许自动匹配" : "Allow automatic matching"}
                      className={`app-center-authorization-option is-elevated ${
                        detailsSkillAuthorization.authorized
                        && detailsSkillAuthorization.activationPolicy === "implicit"
                          ? "is-active"
                          : ""
                      }`}
                      disabled={
                        !detailsItem.skill
                        || skillBusyId === `installed:${detailsItem.catalog_id}`
                        || (
                          detailsSkillAuthorization.authorized
                          && detailsSkillAuthorization.activationPolicy === "implicit"
                          && detailsItem.skill.enabled !== false
                        )
                      }
                      onClick={() => void changeInstructionSkillAuthorization(detailsItem, "implicit")}
                    >
                      <Sparkles size={17} />
                      <span>
                        <strong>{isZh ? "允许自动匹配" : "Allow automatic matching"}</strong>
                        <small>{isZh ? "相关任务可能自动切换到无工具、无历史沙盒" : "Relevant tasks may automatically switch to the no-tool, no-history sandbox"}</small>
                      </span>
                    </button>
                    {(
                      detailsSkillAuthorization.authorized
                      || detailsItem.skill?.authorization?.authorized_digest
                    ) && (
                      <button
                        type="button"
                        className="app-center-secondary is-danger"
                        aria-label={isZh ? "撤销技能授权" : "Revoke skill authorization"}
                        disabled={skillBusyId === `installed:${detailsItem.catalog_id}`}
                        onClick={() => void changeInstructionSkillAuthorization(detailsItem, "none")}
                      >
                        <PowerOff size={16} />
                        {isZh ? "撤销授权并隔离" : "Revoke authorization and quarantine"}
                      </button>
                    )}
                  </section>
                ) : (
                  <button
                    type="button"
                    className="app-center-primary"
                    aria-label={detailsItem.skill?.enabled === false ? (isZh ? "启用技能" : "Enable skill") : (isZh ? "停用技能" : "Disable skill")}
                    disabled={!detailsItem.skill || skillBusyId === `installed:${detailsItem.catalog_id}`}
                    onClick={() => void toggleInstructionSkill(detailsItem)}
                  >
                    {skillBusyId === `installed:${detailsItem.catalog_id}`
                      ? <LoaderCircle className="animate-spin" size={17} />
                      : detailsItem.skill?.enabled === false
                        ? <Power size={17} />
                        : <PowerOff size={17} />}
                    {detailsItem.skill?.enabled === false
                      ? (isZh ? "启用技能" : "Enable skill")
                      : (isZh ? "停用技能" : "Disable skill")}
                  </button>
                )}
                <button
                  type="button"
                  className="app-center-secondary is-danger"
                  aria-label={isZh ? "卸载技能" : "Uninstall skill"}
                  disabled={skillBusyId === `installed:${detailsItem.catalog_id}`}
                  onClick={() => void removeInstructionSkill(detailsItem)}
                >
                  <Trash2 size={16} />
                  {isZh ? "卸载技能" : "Uninstall skill"}
                </button>
              </div>
            ) : detailsItem.ui_app_id ? (
              <button className="app-center-primary" onClick={() => activateItem(detailsItem)}><Play size={17} />{isZh ? "打开应用" : "Open app"}</button>
            ) : detailsItem.launch_mode === "actions" && selectedAction ? (
              <div className="app-center-actions">
                {(detailsItem.actions?.length || 0) > 1 && <div className="app-center-action-tabs">{detailsItem.actions?.map((action) => <button key={action.id} className={selectedAction.id === action.id ? "is-active" : ""} onClick={() => selectAction(action)}>{action.title}</button>)}</div>}
                <h3>{selectedAction.title}</h3>
                {selectedAction.description && <p>{selectedAction.description}</p>}
                {Object.entries(selectedAction.input_schema.properties || {}).map(([key, schema]) => (
                  <label key={key}>
                    <span>{schema.title || key}{selectedAction.input_schema.required?.includes(key) ? " *" : ""}</span>
                    {schema.enum ? (
                      <select value={String(actionInput[key] ?? "")} onChange={(event) => setActionInput((current) => ({ ...current, [key]: event.target.value }))}>
                        <option value="">—</option>{schema.enum.map((value) => <option key={String(value)} value={String(value)}>{String(value)}</option>)}
                      </select>
                    ) : schema.type === "boolean" ? (
                      <input type="checkbox" checked={Boolean(actionInput[key])} onChange={(event) => setActionInput((current) => ({ ...current, [key]: event.target.checked }))} />
                    ) : (
                      <input type={schema.type === "number" || schema.type === "integer" ? "number" : "text"} value={String(actionInput[key] ?? "")} onChange={(event) => setActionInput((current) => ({ ...current, [key]: event.target.value }))} placeholder={schema.description} />
                    )}
                  </label>
                ))}
                {actionError && <p className="app-center-action-error">{actionError}</p>}
                <button className="app-center-primary" onClick={() => void submitAction(detailsItem, selectedAction)} disabled={actionSubmitting}>{actionSubmitting ? <LoaderCircle className="animate-spin" size={17} /> : <Play size={17} />}{isZh ? "后台运行" : "Run in background"}</button>
                <button className="app-center-secondary" onClick={() => requestGeneration(detailsItem)}>{isZh ? "生成可视化界面" : "Generate visual interface"}</button>
              </div>
            ) : (
              <button className="app-center-primary" onClick={() => requestGeneration(detailsItem)} disabled={detailsItem.status === "generating"}>{detailsItem.status === "generating" ? <LoaderCircle className="animate-spin" size={17} /> : <WandSparkles size={17} />}{isZh ? "生成专属界面" : "Generate interface"}</button>
            )}
          </aside>
        </div>
      )}

      {menu && menuItem && (
        <div className="app-center-menu-scrim" onMouseDown={() => setMenu(null)}>
          <div
            className="app-center-menu"
            role="menu"
            aria-label={isZh ? `管理 ${menuItem.title}` : `Manage ${menuItem.title}`}
            style={{
              left: Math.max(8, Math.min(menu.x, window.innerWidth - 250)),
              top: Math.max(8, Math.min(menu.y, window.innerHeight - 360)),
            }}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="app-center-menu-heading"><AppIcon item={menuItem} compact /><span><strong>{menuItem.title}</strong><small>{menuItem.provider}</small></span><MoreHorizontal size={17} /></div>
            {menuItem.ui_app_id && <button role="menuitem" onClick={() => activateItem(menuItem)}><Play size={16} />{isZh ? "打开" : "Open"}</button>}
            {menuItem.ui_app_id && (pinnedWidgetIds.includes(menuItem.ui_app_id) ? <button role="menuitem" onClick={() => { onUnpinWidget(menuItem.ui_app_id!); setMenu(null); }}><PinOff size={16} />{isZh ? "从画布取消固定" : "Unpin from Canvas"}</button> : <button role="menuitem" onClick={() => { onPinWidget(menuItem.ui_app_id!); setMenu(null); }}><Pin size={16} />{isZh ? "固定到画布" : "Pin to Canvas"}</button>)}
            <button role="menuitem" onClick={() => { setDetailsId(menuItem.catalog_id); setMenu(null); }}><Info size={16} />{isZh ? "查看详情" : "View details"}</button>
            {isInstructionSkill(menuItem) && menuSkillAuthorization?.trusted && <button role="menuitem" disabled={!menuItem.skill || skillBusyId === `installed:${menuItem.catalog_id}`} onClick={() => void toggleInstructionSkill(menuItem)}>{menuItem.skill?.enabled === false ? <Power size={16} /> : <PowerOff size={16} />}{menuItem.skill?.enabled === false ? (isZh ? "启用技能" : "Enable skill") : (isZh ? "停用技能" : "Disable skill")}</button>}
            {menuItem.kind === "generated_app" && <button role="menuitem" onClick={() => openAppEditor(menuItem, "configure")}><Settings2 size={16} />{isZh ? "配置属性" : "Configure properties"}</button>}
            {menuItem.kind === "generated_app" && <button role="menuitem" onClick={() => openAppEditor(menuItem, "rename")}><Pencil size={16} />{isZh ? "重命名" : "Rename"}</button>}
            {menuItem.kind !== "generated_app" && !isInstructionSkill(menuItem) && <button role="menuitem" onClick={() => requestGeneration(menuItem)}><RotateCw size={16} />{menuItem.ui_app_id ? (isZh ? "重新生成界面" : "Regenerate UI") : (isZh ? "生成界面" : "Generate UI")}</button>}
            {menuItem.kind !== "generated_app" && !isInstructionSkill(menuItem) && menuItem.ui_app_id && <button role="menuitem" className="is-danger" onClick={() => deleteCapabilityUi(menuItem)}><Trash2 size={16} />{isZh ? "删除生成界面" : "Delete generated UI"}</button>}
            {isInstructionSkill(menuItem) && <button role="menuitem" className="is-danger" disabled={skillBusyId === `installed:${menuItem.catalog_id}`} onClick={() => void removeInstructionSkill(menuItem)}><Trash2 size={16} />{isZh ? "卸载技能" : "Uninstall skill"}</button>}
            {menuItem.kind === "generated_app" && <button role="menuitem" className="is-danger" onClick={() => deleteGeneratedApp(menuItem)}><Trash2 size={16} />{isZh ? "卸载应用" : "Uninstall app"}</button>}
          </div>
        </div>
      )}

      {editor && (
        <SystemDialog
          open
          size="compact"
          title={editor.mode === "rename"
            ? (isZh ? "重命名应用" : "Rename app")
            : (isZh ? "配置应用属性" : "Configure app properties")}
          description={editor.mode === "rename"
            ? (isZh ? "只修改显示名称，稳定的 App ID 不会变化。" : "Only the display name changes; the stable App ID stays the same.")
            : (isZh ? "修改应用描述、版本与用于搜索的标签。" : "Edit the description, version, and searchable tags.")}
          onClose={() => { if (!editorSaving) setEditor(null); }}
        >
          <form
            className="system-dialog-body app-center-property-editor"
            onSubmit={(event) => {
              event.preventDefault();
              void saveAppEditor();
            }}
          >
            {editor.mode === "rename" ? (
              <label>
                <span>{isZh ? "应用名称" : "App name"}</span>
                <input
                  autoFocus
                  required
                  maxLength={200}
                  value={editor.title}
                  onChange={(event) => setEditor((current) => current ? { ...current, title: event.target.value } : current)}
                />
              </label>
            ) : (
              <>
                <label>
                  <span>{isZh ? "描述" : "Description"}</span>
                  <textarea
                    maxLength={2000}
                    rows={4}
                    value={editor.description}
                    onChange={(event) => setEditor((current) => current ? { ...current, description: event.target.value } : current)}
                  />
                </label>
                <label>
                  <span>{isZh ? "版本" : "Version"}</span>
                  <input
                    required
                    maxLength={64}
                    value={editor.version}
                    onChange={(event) => setEditor((current) => current ? { ...current, version: event.target.value } : current)}
                  />
                </label>
                <label>
                  <span>{isZh ? "标签" : "Tags"}</span>
                  <input
                    aria-label={isZh ? "标签" : "Tags"}
                    value={editor.tags}
                    onChange={(event) => setEditor((current) => current ? { ...current, tags: event.target.value } : current)}
                    placeholder={isZh ? "用逗号分隔" : "Separate with commas"}
                  />
                  <small>{isZh ? "标签用于应用中心搜索，不会改变 App 权限。" : "Tags improve App Center search and do not change permissions."}</small>
                </label>
              </>
            )}
            {editorError && <p className="app-center-editor-error" role="alert">{editorError}</p>}
            <div className="system-dialog-actions">
              <button type="button" className="system-button" disabled={editorSaving} onClick={() => setEditor(null)}>{isZh ? "取消" : "Cancel"}</button>
              <button
                type="submit"
                className="system-button is-primary"
                disabled={editorSaving || (editor.mode === "rename" ? !editor.title.trim() : !editor.version.trim())}
              >
                {editorSaving && <LoaderCircle className="animate-spin" size={15} />}
                {editor.mode === "rename"
                  ? (isZh ? "保存名称" : "Save name")
                  : (isZh ? "保存属性" : "Save properties")}
              </button>
            </div>
          </form>
        </SystemDialog>
      )}
    </div>
  );
};
