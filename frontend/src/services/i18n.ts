export type Language = "zh" | "en";

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
