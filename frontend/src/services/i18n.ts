import type {
  PrivacyMapCoverageChannelId,
  PrivacyMapStage,
  PrivacyMapWarningCode,
} from "./privacyDataMap";

export type Language = "zh" | "en";

const PRIVACY_DATA_MAP_COPY_EN = {
  title: "Privacy Map",
  description: "See what Ambient Agent can observe about data movement in this workspace.",
  refresh: "Refresh privacy map",
  close: "Close privacy map",
  loading: "Loading privacy map…",
  updated: "Privacy map updated.",
  errorTitle: "Privacy Map is temporarily unavailable.",
  errorBody: "The current map could not be loaded. No stale or inferred topology is shown.",
  retry: "Try again",
  scope: "Scope",
  workspace: "Workspace",
  window: "Observed window",
  noWindow: "No observed time window",
  source: "Source health",
  checking: "Checking…",
  unavailable: "—",
  healthy: "Healthy",
  degraded: "Degraded",
  coverageRegion: "Coverage and blind spots",
  unknown: "Unknown",
  blindSpotsTitle: "Blind spots",
  partialCoverage: "Partial coverage",
  coverageBody:
    "This map is not a complete record of every data transfer. An unknown path does not mean no transfer occurred.",
  visualMapTitle: "Visual map",
  visualMapHelp: "Select a contract-backed node or relationship to inspect its evidence.",
  visualOverviewNotice:
    "Large maps use a bounded visual overview. Browse every contract item in the semantic map.",
  semanticMapTitle: "Semantic map",
  semanticMapHelp: "The complete text equivalent of the visual map, presented in bounded pages.",
  previousPage: "Previous page",
  nextPage: "Next page",
  selectionDetailsTitle: "Selection details",
  selectionPrompt: "Select an item in either map view to inspect the same details here.",
  selectNode: "Select node",
  selectObservedTransfer: "Select observed transfer",
  selectDeclaredAssociation: "Select declared association",
  to: "to",
  and: "and",
  stageLabel: "Stage",
  evidenceLabel: "Evidence",
  eventCountLabel: "Recorded events",
  relationshipLabel: "Relationship",
  observed: "Observed",
  observedTitle: "Observed transfers",
  observedHelp: "Runtime events recorded by an instrumented path.",
  observedEmpty: "No observed transfers are available for this workspace yet.",
  observedTransfer: "Observed transfer",
  recordedEvent: "recorded event",
  recordedEvents: "recorded events",
  firstSeen: "First observed",
  lastSeen: "Last observed",
  declared: "Declared",
  declaredTitle: "Declared associations",
  declaredHelp: "Manifest relationships describe intent, not runtime behavior or permission.",
  declaredEmpty: "No valid App-to-schema declarations are available.",
  declaredAssociation: "Declared association",
  associatedWith: "associated with",
  inventoryTitle: "Map inventory",
  inventoryHelp: "All nodes represented by the current map contract.",
  platformNode: "Platform",
  modelNode: "Recorded model target",
  appNode: "App",
  schemaNode: "Graph schema",
  unknownLocation: "Location unknown",
  degradedTitle: "Source data is degraded",
  degradedBody: "Valid evidence is still shown. Skipped records are summarized without raw details.",
} as const;

type PrivacyDataMapCopy = {
  [Key in keyof typeof PRIVACY_DATA_MAP_COPY_EN]: string;
};

const PRIVACY_DATA_MAP_COPY: Record<Language, PrivacyDataMapCopy> = {
  en: PRIVACY_DATA_MAP_COPY_EN,
  zh: {
    title: "隐私数据地图",
    description: "查看 Ambient Agent 在当前工作区中能够确认的数据流动信息。",
    refresh: "刷新隐私数据地图",
    close: "关闭隐私数据地图",
    loading: "正在加载隐私数据地图…",
    updated: "隐私数据地图已更新。",
    errorTitle: "隐私数据地图暂时不可用。",
    errorBody: "当前地图无法加载。界面不会继续展示过期数据，也不会推测拓扑。",
    retry: "重试",
    scope: "范围",
    workspace: "工作区",
    window: "观测时间窗口",
    noWindow: "暂无可用的观测时间窗口",
    source: "数据源状态",
    checking: "检查中…",
    unavailable: "—",
    healthy: "正常",
    degraded: "部分降级",
    coverageRegion: "覆盖范围与盲区",
    unknown: "未知",
    blindSpotsTitle: "盲区",
    partialCoverage: "当前仅为部分覆盖",
    coverageBody: "只有已接入观测的路径才会显示为“已观测”。未知并不代表没有发生数据传输。",
    visualMapTitle: "可视地图",
    visualMapHelp: "选择由地图契约支持的节点或关系，查看对应证据。",
    visualOverviewNotice: "大型地图仅显示有界概览；可在下方语义地图中浏览每一项契约数据。",
    semanticMapTitle: "语义地图",
    semanticMapHelp: "可视地图的完整文本等价视图，并以有界分页呈现。",
    previousPage: "上一页",
    nextPage: "下一页",
    selectionDetailsTitle: "选中项详情",
    selectionPrompt: "在任一地图视图中选择一项，即可在此查看同一份详情。",
    selectNode: "选择节点",
    selectObservedTransfer: "选择已观测传输",
    selectDeclaredAssociation: "选择声明关联",
    to: "到",
    and: "与",
    stageLabel: "阶段",
    evidenceLabel: "证据",
    eventCountLabel: "记录事件",
    relationshipLabel: "关系",
    observed: "已观测",
    observedTitle: "已观测的数据传输",
    observedHelp: "由已接入观测的运行时路径记录。",
    observedEmpty: "当前工作区暂无可用的已观测传输记录。",
    observedTransfer: "已观测传输",
    recordedEvent: "条记录事件",
    recordedEvents: "条记录事件",
    firstSeen: "首次观测",
    lastSeen: "最近观测",
    declared: "已声明",
    declaredTitle: "声明关联",
    declaredHelp: "Manifest 关系描述的是声明意图，不代表运行时行为或权限。",
    declaredEmpty: "暂无有效的 App 与 Schema 声明关联。",
    declaredAssociation: "声明关联",
    associatedWith: "关联",
    inventoryTitle: "地图对象",
    inventoryHelp: "当前地图契约中包含的全部节点。",
    platformNode: "平台",
    modelNode: "已记录的模型目标",
    appNode: "App",
    schemaNode: "Graph Schema",
    unknownLocation: "位置未知",
    degradedTitle: "部分源数据存在异常",
    degradedBody: "有效证据仍会保留；跳过的记录仅显示汇总，不展示原始内容。",
  },
};

const PRIVACY_DATA_MAP_COVERAGE_CHANNELS: Record<
  Language,
  Record<PrivacyMapCoverageChannelId, string>
> = {
  en: {
    llm: "LLM calls",
    mcp: "MCP",
    http_agent: "HTTP Agent",
    coding_agent_acp: "Coding Agent / ACP",
    provider_management: "Provider management",
    isolated_widget_runtime: "Isolated Widget Runtime",
  },
  zh: {
    llm: "LLM 调用",
    mcp: "MCP",
    http_agent: "HTTP Agent",
    coding_agent_acp: "统一 Coding Agent / ACP",
    provider_management: "Provider 管理",
    isolated_widget_runtime: "隔离 Widget Runtime",
  },
};

const PRIVACY_DATA_MAP_STAGES: Record<Language, Record<PrivacyMapStage, string>> = {
  en: {
    chat: "Chat",
    route: "Routing",
    plan: "Planning",
    mutation: "Graph mutation",
    verify: "Verification",
    title: "Title generation",
    other: "Other recorded stage",
  },
  zh: {
    chat: "对话",
    route: "路由",
    plan: "规划",
    mutation: "Graph 变更",
    verify: "校验",
    title: "标题生成",
    other: "其他已记录阶段",
  },
};

const PRIVACY_DATA_MAP_COVERAGE_OBSERVATIONS: Record<
  Language,
  Record<"partial" | "not_instrumented", string>
> = {
  en: {
    partial: "partially observed",
    not_instrumented: "not instrumented",
  },
  zh: {
    partial: "部分已观测",
    not_instrumented: "尚未接入观测",
  },
};

const PRIVACY_DATA_MAP_WARNING_FORMATTERS: Record<
  PrivacyMapWarningCode,
  Record<Language, (count: number) => string>
> = {
  malformed_json_records: {
    en: (count) => `${count} malformed audit ${count === 1 ? "record was" : "records were"} skipped.`,
    zh: (count) => `已跳过 ${count} 条 JSON 格式异常的审计记录。`,
  },
  structurally_invalid_audit_records: {
    en: (count) =>
      `${count} structurally invalid audit ${count === 1 ? "record was" : "records were"} skipped.`,
    zh: (count) => `已跳过 ${count} 条结构无效的审计记录。`,
  },
  projection_ineligible_audit_records: {
    en: (count) =>
      `${count} audit ${count === 1 ? "record was" : "records were"} not eligible for this map.`,
    zh: (count) => `${count} 条审计记录不满足地图投影条件。`,
  },
  oversized_audit_lines: {
    en: (count) => `${count} oversized audit ${count === 1 ? "line was" : "lines were"} skipped.`,
    zh: (count) => `已跳过 ${count} 条超出大小限制的审计行。`,
  },
  invalid_app_declarations: {
    en: (count) =>
      `${count} invalid App ${count === 1 ? "declaration was" : "declarations were"} skipped.`,
    zh: (count) => `已跳过 ${count} 条无效的 App 声明。`,
  },
  unsafe_schema_ids: {
    en: (count) => `${count} unsafe schema ${count === 1 ? "ID was" : "IDs were"} skipped.`,
    zh: (count) => `已跳过 ${count} 个不安全的 Schema ID。`,
  },
  missing_schema_references: {
    en: (count) =>
      `${count} missing schema ${count === 1 ? "reference was" : "references were"} skipped.`,
    zh: (count) => `有 ${count} 个 Schema 引用未在中央注册表中找到。`,
  },
};

export function getPrivacyDataMapMessages(language: Language) {
  return {
    copy: PRIVACY_DATA_MAP_COPY[language],
    coverageChannels: PRIVACY_DATA_MAP_COVERAGE_CHANNELS[language],
    stages: PRIVACY_DATA_MAP_STAGES[language],
    coverageObservations: PRIVACY_DATA_MAP_COVERAGE_OBSERVATIONS[language],
  };
}

export function formatPrivacyDataMapWarning(
  code: PrivacyMapWarningCode,
  count: number,
  language: Language
): string {
  return PRIVACY_DATA_MAP_WARNING_FORMATTERS[code][language](count);
}

export function formatPrivacyDataMapRange(
  start: number,
  end: number,
  total: number,
  language: Language
): string {
  return language === "zh"
    ? `显示第 ${start}–${end} 项，共 ${total} 项。`
    : `Showing ${start}–${end} of ${total}.`;
}

export function formatPrivacyDataMapUpdated(
  nodeCount: number,
  observedFlowCount: number,
  declaredAssociationCount: number,
  language: Language
): string {
  if (language === "zh") {
    return (
      `隐私数据地图已更新：${nodeCount} 个节点、${observedFlowCount} 条已观测传输、` +
      `${declaredAssociationCount} 条声明关联。`
    );
  }

  return (
    `Privacy map updated: ${nodeCount} ${nodeCount === 1 ? "node" : "nodes"}, ` +
    `${observedFlowCount} observed ${observedFlowCount === 1 ? "transfer" : "transfers"}, ` +
    `${declaredAssociationCount} declared ` +
    `${declaredAssociationCount === 1 ? "association" : "associations"}.`
  );
}

export function formatPrivacyDataMapSelectionAnnouncement(
  label: string,
  language: Language
): string {
  return language === "zh"
    ? `已选择 ${label}，详情已更新。`
    : `Selected ${label}. Details updated.`;
}

export const translations = {
  zh: {
    conversations: "对话列表",
    newChat: "新建对话",
    deleteChat: "删除对话",
    workspaceCanvas: "工作区画布",
    auditLog: "审核日志",
    appStore: "应用商店",
    askPlaceholder: "问问 Antigravity...",
    send: "发送",
    thinking: "思考中...",
    clarifying: "向您确认中...",
    languageName: "English",
    currentLanguageLabel: "中文",

    // Plan request
    planApprovalTitle: "📋 确认开发计划",
    planApprovalDesc: "AI 为您制定了以下 widget 开发计划，请确认或提供反馈：",
    approve: "同意并继续",
    refine: "修改计划",
    feedbackPlaceholder: "输入您的修改意见或要求...",

    // Schema request
    schemaApprovalTitle: "🔍 确认数据库 Schema 变更",
    schemaApprovalDesc: "以下是此次开发需要修改或新增的数据库 schema 设计：",
    reusedSchemas: "复用的核心 Schema",
    newSchemas: "新增的自定义 Schema",
    propertyName: "属性名",
    propertyType: "类型",
    reason: "合理性说明",
    extendedProperties: "扩展的属性",
    reworkPlan: "重新制定计划",
    
    // Verification request
    verificationTitle: "⚠️ 数据库 Schema 校验警告",
    verificationDesc: "发现生成的 widget 代码与已注册的 Schema 存在以下不一致。请选择处理方式：",
    verificationPassed: "✅ Schema 校验已通过",
    reworkCode: "让 AI 修复代码",
    reworkSchema: "调整 Schema 设计",
    approveAnyway: "忽略警告并启用",
    verifiedFieldsCheck: "同意并保留勾选的字段对齐",

    // App Store
    appStoreTitle: "应用商店",
    appStoreDesc: "在工作区中安装或定制动态小组件",
    searchWidgets: "搜索小组件...",
    buildCustomWidget: "定制专属小组件",
    buildCustomWidgetDesc: "在下方输入您的需求，AI 将为您实时生成定制的交互式小组件并安装到画布上。",
    buildPlaceholder: "例如：帮我做一个番茄钟，包含25分钟倒计时、开始/暂停/重置功能，并把每次专注记录存到图中...",
    buildButton: "开始生成组件",
    install: "安装",
    installed: "已安装",
    uninstall: "卸载",
    close: "关闭",
    noWidgetsFound: "未找到相关组件",
    allCategories: "全部类别",
  },
  en: {
    conversations: "Conversations",
    newChat: "New Chat",
    deleteChat: "Delete Chat",
    workspaceCanvas: "Workspace Canvas",
    auditLog: "Audit Log",
    appStore: "App Store",
    askPlaceholder: "Ask Antigravity...",
    send: "Send",
    thinking: "Thinking...",
    clarifying: "Clarifying with you...",
    languageName: "中文",
    currentLanguageLabel: "English",

    // Plan request
    planApprovalTitle: "📋 Confirm Development Plan",
    planApprovalDesc: "The AI has drafted the following development plan. Please approve or refine it:",
    approve: "Approve & Proceed",
    refine: "Refine Plan",
    feedbackPlaceholder: "Enter your feedback or requirements...",

    // Schema request
    schemaApprovalTitle: "🔍 Confirm Schema Changes",
    schemaApprovalDesc: "The following database schema changes are required for this widget:",
    reusedSchemas: "Reused Core Schemas",
    newSchemas: "New Custom Schemas",
    propertyName: "Property Name",
    propertyType: "Type",
    reason: "Rationale",
    extendedProperties: "Extended Properties",
    reworkPlan: "Rework Plan",

    // Verification request
    verificationTitle: "⚠️ Schema Verification Warning",
    verificationDesc: "The generated widget code deviates from the registered schemas. Please select an action:",
    verificationPassed: "✅ Schema Verification Passed",
    reworkCode: "Let AI Fix Code",
    reworkSchema: "Adjust Schema Design",
    approveAnyway: "Ignore & Enable",
    verifiedFieldsCheck: "Approve checked fields alignment",

    // App Store
    appStoreTitle: "App Store",
    appStoreDesc: "Install or customize dynamic widgets on your canvas",
    searchWidgets: "Search widgets...",
    buildCustomWidget: "Build Custom Widget",
    buildCustomWidgetDesc: "Type your requirements below. The AI will generate, compile, and install a custom interactive widget in real-time.",
    buildPlaceholder: "e.g., build a pomodoro timer with start/pause/reset, and record each session to the graph...",
    buildButton: "Generate Widget",
    install: "Install",
    installed: "Installed",
    uninstall: "Uninstall",
    close: "Close",
    noWidgetsFound: "No widgets found",
    allCategories: "All Categories",
  },
};

export function getTranslation(key: keyof typeof translations.zh, lang: Language): string {
  return translations[lang]?.[key] || translations.zh[key] || String(key);
}
