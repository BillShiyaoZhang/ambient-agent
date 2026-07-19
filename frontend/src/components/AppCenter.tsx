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
  ChevronLeft,
  ChevronRight,
  Folder,
  Info,
  LoaderCircle,
  MoreHorizontal,
  Pin,
  PinOff,
  Play,
  RotateCw,
  Search,
  Sparkles,
  Trash2,
  WandSparkles,
  X,
} from "lucide-react";
import wsService from "../services/websocket";
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
  launch_mode?: "ui" | "actions";
  actions?: CatalogAction[];
  status: CatalogStatus;
}

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
  language?: "zh" | "en";
}

type FilterKind = "all" | CatalogKind;

const API_BASE = `http://${window.location.hostname}:8000`;
const FALLBACK_ACCENTS = ["#7c5cff", "#12b8a6", "#f59e58", "#e85d9e", "#4f8cff", "#76b852"];

function accentFor(item: CatalogItem): string {
  if (item.accent) return item.accent;
  let hash = 0;
  for (const char of item.catalog_id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return FALLBACK_ACCENTS[hash % FALLBACK_ACCENTS.length];
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
  onMenu?: (event: React.MouseEvent | React.PointerEvent) => void;
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
  const longPressRef = useRef<number | null>(null);
  const clearLongPress = () => {
    if (longPressRef.current !== null) window.clearTimeout(longPressRef.current);
    longPressRef.current = null;
  };
  const handlePointerDown = (event: React.PointerEvent) => {
    dragListeners?.onPointerDown?.(event);
    if (event.pointerType === "touch" && onMenu) {
      longPressRef.current = window.setTimeout(() => onMenu(event), 520);
    }
  };
  const title = item?.title ?? folder?.name ?? "";
  return (
    <button
      ref={setNodeRef as React.Ref<HTMLButtonElement>}
      type="button"
      data-launcher-entry={entryId}
      className={`app-center-tile ${isDragging ? "is-dragging" : ""}`}
      style={style}
      onClick={onActivate}
      onContextMenu={(event) => {
        event.preventDefault();
        onMenu?.(event);
      }}
      onPointerDown={handlePointerDown}
      onPointerUp={clearLongPress}
      onPointerCancel={clearLongPress}
      onPointerMove={clearLongPress}
      aria-label={
        folder
          ? `${isZh ? "打开文件夹" : "Open folder"} ${title}`
          : `${isZh ? "打开" : "Open"} ${title}`
      }
      {...dragAttributes}
      {...Object.fromEntries(Object.entries(dragListeners ?? {}).filter(([key]) => key !== "onPointerDown"))}
    >
      <span className="app-center-tile-visual">
        {item ? <AppIcon item={item} /> : folder ? <FolderIcon folder={folder} items={folderItems} /> : null}
      </span>
      <span className="app-center-tile-title">{title}</span>
      {item?.status === "needs_ui" && item.launch_mode !== "actions" && (
        <span className="app-center-tile-status">{isZh ? "需要界面" : "Needs UI"}</span>
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

export const AppCenter: React.FC<AppCenterProps> = ({
  isOpen,
  mode = "overlay",
  onClose,
  pinnedWidgetIds,
  onPinWidget,
  onUnpinWidget,
  onRunFullscreen,
  onRunCreated,
  language = "zh",
}) => {
  const isZh = language === "zh";
  const [store, setStore] = useState<AppStoreState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
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

  useEffect(() => {
    if (isOpen || mode === "home") fetchStore();
  }, [isOpen, mode, fetchStore]);

  useEffect(() => {
    const refresh = () => fetchStore();
    window.addEventListener("app-store-refresh", refresh);
    return () => window.removeEventListener("app-store-refresh", refresh);
  }, [fetchStore]);

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

  const activateItem = (item: CatalogItem) => {
    setMenu(null);
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
        if (activeId) setActiveId(null);
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
  }, [isOpen, mode, activeId, menu, detailsId, openFolderId, query, onClose]);

  if (!isOpen && mode !== "home") return null;

  const openFolder = openFolderId ? foldersById.get(openFolderId) : undefined;
  const detailsItem = detailsId ? itemsById.get(detailsId) : undefined;
  const selectedAction = detailsItem?.actions?.find((action) => action.id === selectedActionId) ?? detailsItem?.actions?.[0];
  const menuItem = menu ? itemsById.get(menu.itemId) : undefined;
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
        ? (event: React.MouseEvent | React.PointerEvent) => setMenu({ itemId: item.catalog_id, x: event.clientX, y: event.clientY })
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
            placeholder={isZh ? "搜索应用、技能或 MCP" : "Search apps, skills, or MCP"}
            aria-label={isZh ? "搜索应用" : "Search apps"}
          />
          <kbd>⌘ K</kbd>
        </div>
        {mode !== "home" && <button className="app-center-close" onClick={onClose} aria-label={isZh ? "关闭" : "Close"}><X size={21} /></button>}
      </header>

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

      {notice && (
        <button className="app-center-notice" onClick={() => setNotice("")}>
          <AlertCircle size={16} /> {notice} <X size={14} />
        </button>
      )}

      <main className="app-center-content">
        {loading && !store ? (
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

      {!isSearching && pageCount > 1 && (
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
              <div><dt>{isZh ? "状态" : "Status"}</dt><dd>{detailsItem.status === "ready" ? (isZh ? "可使用" : "Ready") : detailsItem.status === "generating" ? (isZh ? "正在生成" : "Generating") : (isZh ? "需要界面" : "Needs UI")}</dd></div>
            </dl>
            {detailsItem.tags.length > 0 && <div className="app-center-tags">{detailsItem.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
            {detailsItem.ui_app_id ? (
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
          <div className="app-center-menu" style={{ left: Math.min(menu.x, window.innerWidth - 250), top: Math.min(menu.y, window.innerHeight - 300) }} onMouseDown={(event) => event.stopPropagation()}>
            <div className="app-center-menu-heading"><AppIcon item={menuItem} compact /><span><strong>{menuItem.title}</strong><small>{menuItem.provider}</small></span><MoreHorizontal size={17} /></div>
            {menuItem.ui_app_id && <button onClick={() => activateItem(menuItem)}><Play size={16} />{isZh ? "打开" : "Open"}</button>}
            {menuItem.ui_app_id && (pinnedWidgetIds.includes(menuItem.ui_app_id) ? <button onClick={() => { onUnpinWidget(menuItem.ui_app_id!); setMenu(null); }}><PinOff size={16} />{isZh ? "从画布取消固定" : "Unpin from Canvas"}</button> : <button onClick={() => { onPinWidget(menuItem.ui_app_id!); setMenu(null); }}><Pin size={16} />{isZh ? "固定到画布" : "Pin to Canvas"}</button>)}
            <button onClick={() => { setDetailsId(menuItem.catalog_id); setMenu(null); }}><Info size={16} />{isZh ? "查看详情" : "View details"}</button>
            {menuItem.kind !== "generated_app" && <button onClick={() => requestGeneration(menuItem)}><RotateCw size={16} />{menuItem.ui_app_id ? (isZh ? "重新生成界面" : "Regenerate UI") : (isZh ? "生成界面" : "Generate UI")}</button>}
            {menuItem.kind !== "generated_app" && menuItem.ui_app_id && <button className="is-danger" onClick={() => deleteCapabilityUi(menuItem)}><Trash2 size={16} />{isZh ? "删除生成界面" : "Delete generated UI"}</button>}
            {menuItem.kind === "generated_app" && <button className="is-danger" onClick={() => deleteGeneratedApp(menuItem)}><Trash2 size={16} />{isZh ? "卸载应用" : "Uninstall app"}</button>}
          </div>
        </div>
      )}
    </div>
  );
};
