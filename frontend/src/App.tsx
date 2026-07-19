import { useState, useEffect, useRef, useCallback } from "react";
import wsService from "./services/websocket";
import type { Message } from "./components/ChatPanel";
import type { Widget } from "./components/DashboardCanvas";
import { SandboxWidget } from "./components/SandboxWidget";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AuditLogPanel } from "./components/AuditLogPanel";
import type { Session } from "./components/SessionSidebar";
import { AppCenter } from "./components/AppCenter";
import { AppPermissionModal } from "./components/AppPermissionModal";
import { MutationPreview, type MutationPreviewData } from "./components/MutationPreview";
import { AppWorkspace } from "./components/AppWorkspace";
import { AgentChatOverlay } from "./components/AgentChatOverlay";
import { TaskDrawer } from "./components/TaskDrawer";
import { LLMSettingsDialog } from "./components/LLMSettings";
import { SystemDialog, SystemIconButton } from "./components/system/SystemUI";
import { createThemeController, type ThemeSnapshot } from "./services/theme";
import { EMPTY_CANVAS, migrateCanvasConfig, type CanvasConfigV3 } from "./lib/windowManager";
import { mergeIncomingMessage } from "./lib/messages";
import { Languages, ListTodo, Moon, Settings2, ShieldCheck, Sun } from "lucide-react";
import { runService, type AmbientRun } from "./services/runs";
import {
  loadCodingAgentConfiguration,
  updateCodingAgentSettings,
  type CodingAgentDefinition,
  type CodingAgentSettings,
} from "./services/codingAgents";
import {
  createProvider,
  deleteProvider,
  discoverProviderModels,
  loadLLMConfiguration,
  testProviderConnection,
  updateLLMSettings,
  updateProvider,
  updateSessionModel,
  type LLMProvider,
  type LLMSettings,
  type ModelSelection,
  type ProviderPreset,
} from "./services/llm";

const API_BASE = `http://${window.location.hostname}:8000`;

function localizedLLMError(code: string, language: "zh" | "en"): string {
  const messages: Record<string, [string, string]> = {
    llm_configuration_required: ["请先在“模型与 Provider”中完成配置。", "Configure a model and provider before sending a request."],
    llm_auth_failed: ["Provider 鉴权失败，请检查凭据。", "Provider authentication failed. Check the credentials."],
    llm_rate_limited: ["Provider 已限流，请稍后重试。", "The provider rate limit was reached. Try again later."],
    llm_timeout: ["模型请求超时，请检查端点或超时设置。", "The model request timed out. Check the endpoint or timeout setting."],
    llm_model_not_found: ["Provider 找不到所选模型。", "The selected model was not found by the provider."],
    llm_capability_unsupported: ["所选模型不兼容 Agent 工具调用。", "The selected model is not compatible with agent tool calls."],
  };
  const pair = messages[code] ?? ["模型请求失败，请检查 Provider 设置。", "The model request failed. Check provider settings."];
  return pair[language === "zh" ? 0 : 1];
}

function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [canvasConfig, setCanvasConfig] = useState<CanvasConfigV3>(() => ({ ...EMPTY_CANVAS, windows: {} }));
  const [llmCatalog, setLLMCatalog] = useState<ProviderPreset[]>([]);
  const [llmProviders, setLLMProviders] = useState<LLMProvider[]>([]);
  const [llmSettings, setLLMSettings] = useState<LLMSettings>({ default_model: null, fast_model: null });
  const [codingAgents, setCodingAgents] = useState<CodingAgentDefinition[]>([]);
  const [codingAgentSettings, setCodingAgentSettings] = useState<CodingAgentSettings>({ default_agent: "opencode" });
  const [isLLMSettingsOpen, setIsLLMSettingsOpen] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const [isChatOpen, setIsChatOpen] = useState(false);
  const chatOpenRef = useRef(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const themeControllerRef = useRef<ReturnType<typeof createThemeController> | null>(null);
  if (!themeControllerRef.current) themeControllerRef.current = createThemeController();
  const [theme, setTheme] = useState<ThemeSnapshot>(() => themeControllerRef.current!.snapshot());

  useEffect(() => {
    const controller = themeControllerRef.current!;
    const unsubscribe = controller.subscribe(setTheme);
    return () => { unsubscribe(); controller.destroy(); };
  }, []);

  const handleChatOpenChange = (open: boolean) => {
    chatOpenRef.current = open;
    setIsChatOpen(open);
    if (open) setUnreadCount(0);
  };

  const handleLanguageChange = async (lang: "zh" | "en") => {
    setLanguage(lang);
    if (activeSessionId) {
      setSessions((prev) =>
        prev.map((s) => (s.id === activeSessionId ? { ...s, language: lang } : s))
      );
      try {
        await fetch(`${API_BASE}/api/sessions/${activeSessionId}/language`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ language: lang }),
        });
      } catch (err) {
        console.error("Error setting session language:", err);
      }
    }
  };

  const saveCanvasConfig = useCallback(async (config: CanvasConfigV3) => {
    try {
      await fetch(`${API_BASE}/api/canvas`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
    } catch (err) {
      console.error("Error saving canvas configuration:", err);
    }
  }, []);

  const refreshLLMConfiguration = useCallback(async () => {
    try {
      const [llmConfiguration, codingAgentConfiguration] = await Promise.all([
        loadLLMConfiguration(API_BASE),
        loadCodingAgentConfiguration(API_BASE),
      ]);
      setLLMCatalog(llmConfiguration.catalog);
      setLLMProviders(llmConfiguration.providers);
      setLLMSettings(llmConfiguration.settings);
      setCodingAgents(codingAgentConfiguration.agents);
      setCodingAgentSettings(codingAgentConfiguration.settings);
    } catch (error) {
      console.error("Error loading LLM configuration:", error);
    }
  }, []);
  const [isAppStoreOpen, setIsAppStoreOpen] = useState(false);
  const [isAuditOpen, setIsAuditOpen] = useState(false);
  const [isTaskDrawerOpen, setIsTaskDrawerOpen] = useState(false);
  const [taskCounts, setTaskCounts] = useState({ active: 0, attention: 0 });
  
  interface PermissionRequest {
    request_id: string;
    tool_call: string;
    details: string;
  }
  const [pendingPermission, setPendingPermission] = useState<PermissionRequest | null>(null);
  const [pendingBackendPermission, setPendingBackendPermission] = useState<any | null>(null);

  const handleResolvePermission = (approved: boolean) => {
    if (!pendingPermission) return;
    wsService.sendMessage({
      type: "permission_response",
      request_id: pendingPermission.request_id,
      approved: approved
    });
    setPendingPermission(null);
  };

  const handleResolveBackendPermission = (approved: boolean) => {
    if (!pendingBackendPermission) return;
    wsService.sendMessage({
      type: "backend_permission_response",
      request_id: pendingBackendPermission.request_id,
      approved: approved
    });
    setPendingBackendPermission(null);
  };

  interface SchemaProposal {
    reused_schemas: Array<{
      id: string;
      reason: string;
      extended_properties: Record<string, string>;
    }>;
    new_schemas: Array<{
      id: string;
      name: string;
      description: string;
      properties: Record<string, string>;
    }>;
  }
  interface SchemaApprovalRequest {
    request_id: string;
    app_id: string;
    proposal: SchemaProposal;
  }

  interface PlanApprovalRequest {
    type: "plan_approval_request";
    request_id: string;
    app_id: string;
    plan: string;
  }

  interface VerificationApprovalRequest {
    type: "verification_approval_request";
    request_id: string;
    app_id: string;
    report: string;
    options?: Array<{
      node_type: string;
      property_name: string;
      detected_type?: string;
      risk?: string;
    }>;
  }

  const [pendingSchemaRequest, setPendingSchemaRequest] = useState<SchemaApprovalRequest | null>(null);
  const [editedProposal, setEditedProposal] = useState<SchemaProposal | null>(null);
  const [pendingPlanRequest, setPendingPlanRequest] = useState<PlanApprovalRequest | null>(null);
  const [planFeedback, setPlanFeedback] = useState("");
  const [runningSessions, setRunningSessions] = useState<string[]>([]);

  const [schemaFeedback, setSchemaFeedback] = useState("");
  const [pendingVerificationRequest, setPendingVerificationRequest] = useState<VerificationApprovalRequest | null>(null);
  const [verificationFeedback, setVerificationFeedback] = useState("");

  useEffect(() => {
    if (pendingSchemaRequest) {
      setEditedProposal(JSON.parse(JSON.stringify(pendingSchemaRequest.proposal)));
      setSchemaFeedback("");
    } else {
      setEditedProposal(null);
      setSchemaFeedback("");
    }
  }, [pendingSchemaRequest]);

  useEffect(() => {
    if (!pendingVerificationRequest) {
      setVerificationFeedback("");
    }
  }, [pendingVerificationRequest]);

  const handleResolveSchemaRequest = (approved: boolean | "refine" | "rework_plan", feedbackText?: string) => {
    if (!pendingSchemaRequest) return;
    wsService.sendMessage({
      type: "schema_approval_response",
      request_id: pendingSchemaRequest.request_id,
      approved: approved,
      proposal: editedProposal || pendingSchemaRequest.proposal,
      feedback: feedbackText || ""
    });
    setPendingSchemaRequest(null);
  };

  const handleResolveVerificationRequest = (approved: "approve" | "rework_code" | "rework_schema" | "rework_plan", feedbackText?: string, approvedOptions?: Array<{ node_type: string; property_name: string }>) => {
    if (!pendingVerificationRequest) return;
    wsService.sendMessage({
      type: "verification_approval_response",
      request_id: pendingVerificationRequest.request_id,
      approved: approved,
      feedback: feedbackText || "",
      approved_options: approvedOptions || []
    });
    setPendingVerificationRequest(null);
  };

  const collectCheckedOptions = (req: any): Array<{ node_type: string; property_name: string; detected_type: string; action: string; risk: string }> => {
    const opts = Array.isArray(req?.options) ? req.options : [];
    if (typeof document === "undefined") return opts;
    return opts.filter((opt: any) => {
      const key = `${opt.node_type}:${opt.property_name}`;
      const el = document.querySelector<HTMLInputElement>(`input[data-verify-opt="${key}"]`);
      return el ? el.checked : true;
    });
  };

  const handleResolvePlanRequest = (approved: boolean | "refine", feedbackText?: string) => {
    if (!pendingPlanRequest) return;
    wsService.sendMessage({
      type: "plan_approval_response",
      request_id: pendingPlanRequest.request_id,
      approved: approved,
      plan: pendingPlanRequest.plan,
      feedback: feedbackText || ""
    });
    setPendingPlanRequest(null);
  };

  // Helper functions for editing Reused Schema extensions
  const handleAddExtendedProperty = (schemaIndex: number) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    const schema = updated.reused_schemas[schemaIndex];
    let counter = 1;
    let newKey = `new_field_${counter}`;
    while (newKey in schema.extended_properties) {
      counter++;
      newKey = `new_field_${counter}`;
    }
    schema.extended_properties[newKey] = "string";
    setEditedProposal(updated);
  };

  const handleUpdateExtendedPropertyKey = (schemaIndex: number, oldKey: string, newKey: string) => {
    if (!editedProposal || !newKey || oldKey === newKey) return;
    const updated = { ...editedProposal };
    const schema = updated.reused_schemas[schemaIndex];
    if (newKey in schema.extended_properties) return;
    
    const val = schema.extended_properties[oldKey];
    delete schema.extended_properties[oldKey];
    schema.extended_properties[newKey] = val;
    setEditedProposal(updated);
  };

  const handleUpdateExtendedPropertyType = (schemaIndex: number, key: string, newType: string) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    updated.reused_schemas[schemaIndex].extended_properties[key] = newType;
    setEditedProposal(updated);
  };

  const handleRemoveExtendedProperty = (schemaIndex: number, key: string) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    delete updated.reused_schemas[schemaIndex].extended_properties[key];
    setEditedProposal(updated);
  };

  // Helper functions for editing New Schemas
  const handleAddNewSchemaProperty = (schemaIndex: number) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    const schema = updated.new_schemas[schemaIndex];
    let counter = 1;
    let newKey = `field_${counter}`;
    while (newKey in schema.properties) {
      counter++;
      newKey = `field_${counter}`;
    }
    schema.properties[newKey] = "string";
    setEditedProposal(updated);
  };

  const handleUpdateNewSchemaPropertyKey = (schemaIndex: number, oldKey: string, newKey: string) => {
    if (!editedProposal || !newKey || oldKey === newKey) return;
    const updated = { ...editedProposal };
    const schema = updated.new_schemas[schemaIndex];
    if (newKey in schema.properties) return;
    
    const val = schema.properties[oldKey];
    delete schema.properties[oldKey];
    schema.properties[newKey] = val;
    setEditedProposal(updated);
  };

  const handleUpdateNewSchemaPropertyType = (schemaIndex: number, key: string, newType: string) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    updated.new_schemas[schemaIndex].properties[key] = newType;
    setEditedProposal(updated);
  };

  const handleRemoveNewSchemaProperty = (schemaIndex: number, key: string) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    delete updated.new_schemas[schemaIndex].properties[key];
    setEditedProposal(updated);
  };

  const handleUpdateNewSchemaMeta = (schemaIndex: number, field: "name" | "description" | "id", val: string) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    updated.new_schemas[schemaIndex][field] = val;
    if (field === "id") {
      updated.new_schemas[schemaIndex].name = val.replace(/([A-Z])/g, ' $1').trim();
    }
    setEditedProposal(updated);
  };

  const handleRemoveNewSchema = (schemaIndex: number) => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    updated.new_schemas.splice(schemaIndex, 1);
    setEditedProposal(updated);
  };

  const handleAddNewSchema = () => {
    if (!editedProposal) return;
    const updated = { ...editedProposal };
    let counter = 1;
    let newId = `CustomEntity${counter}`;
    while (updated.new_schemas.some(s => s.id === newId)) {
      counter++;
      newId = `CustomEntity${counter}`;
    }
    updated.new_schemas.push({
      id: newId,
      name: `Custom Entity ${counter}`,
      description: "Custom entity registered by user",
      properties: { "title": "string" }
    });
    setEditedProposal(updated);
  };

  const [mutationPreview, setMutationPreview] = useState<MutationPreviewData | null>(null);

  // 1. Fetch sessions list on mount
  const fetchSessions = async (selectId?: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/sessions`);
      if (res.ok) {
        const rawData = await res.json();
        const data = rawData.filter((s: Session) => s && s.id && !(s.id.includes("=>") || s.id.includes("function") || s.id.includes("{") || s.id.includes("(")));
        setSessions(data);
        
        if (data.length > 0) {
          // If a specific ID is requested, select it, otherwise use the first session or last active
          let nextActiveId = selectId || localStorage.getItem("last_active_session") || data[0].id;
          
          // Defensive check to avoid corrupted session ID (e.g. callback function string from legacy states)
          if (nextActiveId && (nextActiveId.includes("=>") || nextActiveId.includes("function") || nextActiveId.includes("{"))) {
            localStorage.removeItem("last_active_session");
            nextActiveId = data[0].id;
          }

          const exists = data.some((s: Session) => s.id === nextActiveId);
          const finalId = exists ? nextActiveId : data[0].id;
          setActiveSessionId(finalId);
          localStorage.setItem("last_active_session", finalId);
          const activeSess = data.find((s: Session) => s.id === finalId);
          if (activeSess && activeSess.language) {
            setLanguage(activeSess.language as "zh" | "en");
          } else {
            setLanguage("zh");
          }
        } else {
          // Create a default session if list is empty
          handleCreateSession();
        }
      }
    } catch (err) {
      console.error("Error fetching sessions:", err);
    }
  };

  useEffect(() => {
    fetchSessions();
    void refreshLLMConfiguration();

    // Check connection state every second
    const interval = setInterval(() => {
      setIsConnected(wsService.isConnected());
    }, 1000);

    return () => {
      clearInterval(interval);
      wsService.disconnect();
    };
    // Session bootstrap is intentionally mount-only; subsequent refreshes are explicit.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshLLMConfiguration]);

  useEffect(() => {
    const loadCanvasConfig = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/canvas`);
        if (!res.ok) return;
        const raw = await res.json();
        const config = migrateCanvasConfig(raw);
        setCanvasConfig(config);
        const loaded = await Promise.all(config.open_app_ids.map(async (id) => {
          try {
            const appRes = await fetch(`${API_BASE}/api/apps/${id}`);
            return appRes.ok ? await appRes.json() as Widget : null;
          } catch {
            return null;
          }
        }));
        setWidgets(loaded.filter((widget): widget is Widget => Boolean(widget)));
        if (raw.version !== 3) saveCanvasConfig(config);
      } catch (err) {
        console.error("Error loading canvas configuration:", err);
      }
    };
    loadCanvasConfig();
  }, [saveCanvasConfig]);

  // 2. Fetch messages and connect the selected chat session.
  useEffect(() => {
    if (!activeSessionId) return;

    // Load message history from DB
    const loadSessionHistory = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/sessions/${activeSessionId}/messages`);
        if (res.ok) {
          const data = await res.json();
          // Filter to only user and agent conversational messages for the Chat UI
          const chatMsgs = data.filter((msg: any) => msg.role === "user" || msg.role === "agent");
          setMessages(chatMsgs);
        }
      } catch (err) {
        console.error("Error loading chat history:", err);
      }
    };

    loadSessionHistory();

    const handleProjection = (data: any) => {
      if (data.type === "ack" || data.type === "reply") {
        if (data.type === "reply" && data.message?.sender === "agent" && !chatOpenRef.current) {
          setUnreadCount((count) => count + 1);
        }
        setMessages((prev) => mergeIncomingMessage(prev, data.message));
      } else if (data.type === "widget") {
        // Add or update widget in state
        setWidgets((prev) => {
          const exists = prev.some((w) => w.id === data.widget.id);
          return exists
            ? prev.map((w) => (w.id === data.widget.id ? data.widget : w))
            : [...prev, data.widget];
        });
        
        setCanvasConfig((previous) => {
          const id = data.widget.id as string;
          const ids = [...previous.open_app_ids.filter((appId) => appId !== id), id];
          const next: CanvasConfigV3 = {
            ...previous,
            open_app_ids: ids,
            active_app_id: id,
            windows: {
              ...previous.windows,
              [id]: {
                mode: "maximized",
                bounds: previous.windows[id]?.bounds ?? { x: 0.16, y: 0.12, width: 0.68, height: 0.72 },
                restoreBounds: previous.windows[id]?.bounds,
              },
            },
          };
          saveCanvasConfig(next);
          return next;
        });

      } else if (data.type === "graph_query_update") {
        window.dispatchEvent(
          new CustomEvent(`graph_query_update:${data.subscription_id}`, {
            detail: data.data,
          })
        );
      } else if (data.type === "ag_ui_event") {
        window.dispatchEvent(
          new CustomEvent(`ag_ui_event:${data.app_id}`, {
            detail: data.event,
          })
        );
      } else if (data.type === "mcp_call_response") {
        window.dispatchEvent(
          new CustomEvent(`mcp_call_response:${data.app_id}:${data.call_id}`, {
            detail: data,
          })
        );
      } else if (data.type === "mcp_read_response") {
        window.dispatchEvent(
          new CustomEvent(`mcp_read_response:${data.app_id}:${data.call_id}`, {
            detail: data,
          })
        );
      } else if (data.type === "capability_call_response") {
        window.dispatchEvent(
          new CustomEvent(`capability_call_response:${data.catalog_id}:${data.call_id}`, {
            detail: data,
          })
        );
      } else if (typeof data.type === "string" && data.type.startsWith("capability_ui_generation_")) {
        window.dispatchEvent(new CustomEvent("app-store-refresh", { detail: data }));
      } else if (data.type === "permission_request") {
        setPendingPermission(data);
      } else if (data.type === "backend_permission_request") {
        setPendingBackendPermission(data);
      } else if (data.type === "schema_approval_request") {
        setPendingSchemaRequest(data);
      } else if (data.type === "plan_approval_request") {
        setPendingPlanRequest(data);
      } else if (data.type === "verification_approval_request") {
        setPendingVerificationRequest(data);
      } else if (data.type === "active_sessions_list") {
        setRunningSessions(data.active_session_ids);
      } else if (data.type === "session_title_updated") {
        setSessions((previous) => previous.map((session) => session.id === data.session_id ? { ...session, title: data.title } : session));
      } else if (data.type === "session_model_updated") {
        setSessions((previous) => previous.map((session) => session.id === data.session_id ? { ...session, model_selection: data.model_selection } : session));
      } else if (data.type === "llm_error") {
        setMessages((previous) => [...previous, {
          sender: "agent",
          content: localizedLLMError(data.code, language),
          timestamp: new Date().toISOString(),
        }]);
        if (data.action === "open_llm_settings" || data.code === "llm_configuration_required") {
          setIsLLMSettingsOpen(true);
          void refreshLLMConfiguration();
        }
      } else if (data.type === "mutation_preview") {
        setMutationPreview({
          ticket_id: data.ticket_id,
          session_id: data.session_id,
          actions: data.actions || [],
          summary: data.summary || "已更新数据",
          soft_window_seconds: data.soft_window_seconds || 60,
        });
      } else if (data.type === "rollback_mutation_response") {
        // Drop preview when rollback succeeded
        setMutationPreview((curr) => (curr && curr.ticket_id === data.ticket_id ? null : curr));
      } else if (data.type === "pin_mutation_response") {
        // Pin acknowledged; visual stays — soft window may continue, but we keep the preview
      } else if (data.type === "session_status_update") {
        setRunningSessions((prev) => {
          if (data.status === "running") {
            return prev.includes(data.session_id) ? prev : [...prev, data.session_id];
          } else {
            return prev.filter((id) => id !== data.session_id);
          }
        });
      }
    };

    // /ws/chat is now the command/control socket. Durable reducer output is
    // projected from the canonical replayable /ws/runs stream below.
    const wsUrl = `ws://${window.location.hostname}:8000/ws/chat?projection=commands_only`;
    wsService.connect(wsUrl, activeSessionId, handleProjection);
    const projectedTypes = new Set([
      "reply",
      "widget",
      "permission_request",
      "schema_approval_request",
      "plan_approval_request",
      "verification_approval_request",
      "mutation_preview",
      "mutation_committed",
    ]);
    const unsubscribeRunEvents = runService.subscribe((event) => {
      if (event.session_id !== activeSessionId) return;
      const payload = event.payload;
      if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return;
      const type = (payload as Record<string, unknown>).type;
      if (typeof type === "string" && projectedTypes.has(type)) {
        handleProjection(payload);
      }
    });

    return () => {
      unsubscribeRunEvents();
      wsService.disconnect();
    };
  }, [activeSessionId, language, refreshLLMConfiguration, saveCanvasConfig]);

  const handleCreateSession = async () => {
    const newId = Math.random().toString(36).substring(2, 15);
    const newTitle = language === "zh" ? "新对话" : "New conversation";
    try {
      const res = await fetch(`${API_BASE}/api/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: newId, title: newTitle, language }),
      });
      if (res.ok) {
        localStorage.setItem("last_active_session", newId);
        fetchSessions(newId);
      }
    } catch (err) {
      console.error("Error creating session:", err);
    }
  };

  const handleDeleteSession = async (id: string) => {
    if (!confirm("Are you sure you want to delete this conversation?")) return;
    try {
      const res = await fetch(`${API_BASE}/api/sessions/${id}`, { method: "DELETE" });
      if (res.ok) {
        // If we deleted the active session, clear selection to force fallback
        if (activeSessionId === id) {
          localStorage.removeItem("last_active_session");
          setActiveSessionId(null);
        }
        fetchSessions();
      }
    } catch (err) {
      console.error("Error deleting session:", err);
    }
  };

  const handleSendMessage = (text: string) => {
    wsService.sendMessage({
      sender: "user",
      content: text,
    });
  };

  const handleRemoveWidget = (id: string) => {
    setCanvasConfig((previous) => {
      const ids = previous.open_app_ids.filter((appId) => appId !== id);
      const windows = { ...previous.windows };
      delete windows[id];
      const next = { ...previous, open_app_ids: ids, active_app_id: ids.at(-1) ?? null, windows };
      saveCanvasConfig(next);
      return next;
    });
  };

  const handleOpenApp = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/apps/${id}`);
      if (res.ok) {
        const appData = await res.json();
        setWidgets((prev) => {
          const exists = prev.some((w) => w.id === id);
          return exists
            ? prev.map((w) => (w.id === id ? appData : w))
            : [...prev, appData];
        });
        setCanvasConfig((previous) => {
          const ids = [...previous.open_app_ids.filter((appId) => appId !== id), id];
          const existing = previous.windows[id];
          const next: CanvasConfigV3 = {
            ...previous,
            open_app_ids: ids,
            active_app_id: id,
            windows: {
              ...previous.windows,
              [id]: {
                mode: "maximized",
                bounds: existing?.bounds ?? { x: 0.16, y: 0.12, width: 0.68, height: 0.72 },
                restoreBounds: existing?.mode === "floating" ? existing.bounds : existing?.restoreBounds,
              },
            },
          };
          saveCanvasConfig(next);
          return next;
        });
      }
    } catch (err) {
      console.error("Error opening app:", err);
    }
    setIsAppStoreOpen(false);
  };

  const handleCanvasChange = (next: CanvasConfigV3, persist = false) => {
    setCanvasConfig(next);
    if (persist) saveCanvasConfig(next);
  };

  const handleSelectSession = (id: string) => {
    setActiveSessionId(id);
    localStorage.setItem("last_active_session", id);
    const selected = sessions.find((session) => session.id === id);
    setLanguage(selected?.language === "en" ? "en" : "zh");
  };

  const handleSessionModelChange = async (selection: ModelSelection) => {
    if (!activeSessionId) return;
    try {
      await updateSessionModel(API_BASE, activeSessionId, selection);
      setSessions((previous) => previous.map((session) => session.id === activeSessionId
        ? { ...session, model_selection: selection }
        : session));
    } catch (error) {
      console.error("Error updating session model:", error);
      setIsLLMSettingsOpen(true);
    }
  };

  const setAppWindowMode = (id: string, mode: "maximized" | "floating") => {
    setCanvasConfig((previous) => {
      const current = previous.windows[id];
      if (!current) return previous;
      const ids = [...previous.open_app_ids.filter((appId) => appId !== id), id];
      const next: CanvasConfigV3 = {
        ...previous,
        open_app_ids: ids,
        active_app_id: id,
        windows: {
          ...previous.windows,
          [id]: mode === "maximized"
            ? { ...current, mode, restoreBounds: current.mode === "floating" ? current.bounds : current.restoreBounds, snapZone: undefined }
            : { ...current, mode, bounds: current.restoreBounds ?? current.bounds, snapZone: undefined },
        },
      };
      saveCanvasConfig(next);
      return next;
    });
  };

  const uniqueWidgets = widgets.filter((widget, index, list) => list.findIndex((candidate) => candidate.id === widget.id) === index);
  const appCenter = (mode: "home" | "overlay") => (
    <AppCenter
      mode={mode}
      isOpen={mode === "home" || isAppStoreOpen}
      onClose={() => setIsAppStoreOpen(false)}
      pinnedWidgetIds={canvasConfig.open_app_ids}
      onPinWidget={handleOpenApp}
      onUnpinWidget={handleRemoveWidget}
      onRunFullscreen={handleOpenApp}
      onRunCreated={() => setIsTaskDrawerOpen(true)}
      language={language}
      headerActions={<div className="app-center-system-actions" aria-label={language === "zh" ? "系统设置" : "System settings"}>
        <SystemIconButton label={language === "zh" ? "任务中心" : "Task Center"} onClick={() => setIsTaskDrawerOpen(true)}><ListTodo size={17} />{taskCounts.active + taskCounts.attention > 0 ? <span className="system-action-badge">{Math.min(taskCounts.active + taskCounts.attention, 99)}</span> : null}</SystemIconButton>
        <SystemIconButton label={language === "zh" ? "审计日志" : "Audit log"} onClick={() => setIsAuditOpen(true)}><ShieldCheck size={17} /></SystemIconButton>
        <SystemIconButton label={language === "zh" ? "模型与 Provider" : "Models & Providers"} onClick={() => { setIsLLMSettingsOpen(true); void refreshLLMConfiguration(); }}><Settings2 size={17} /></SystemIconButton>
        <SystemIconButton label={language === "zh" ? "切换为英文" : "Switch to Chinese"} onClick={() => handleLanguageChange(language === "zh" ? "en" : "zh")}><Languages size={17} /></SystemIconButton>
        <label className="system-theme-select" aria-label={language === "zh" ? "主题" : "Theme"}>
          {theme.effective === "dark" ? <Moon size={16} /> : <Sun size={16} />}
          <select value={theme.preference} onChange={(event) => themeControllerRef.current!.setPreference(event.target.value as "system" | "light" | "dark")}>
            <option value="system">{language === "zh" ? "跟随系统" : "System"}</option>
            <option value="light">{language === "zh" ? "浅色" : "Light"}</option>
            <option value="dark">{language === "zh" ? "深色" : "Dark"}</option>
          </select>
        </label>
      </div>}
    />
  );

  return (
    <div className="w-screen h-screen overflow-hidden font-sans" data-theme={theme.effective}>
      {canvasConfig.open_app_ids.length === 0 ? appCenter("home") : (
        <>
          <AppWorkspace
            widgets={uniqueWidgets}
            canvas={canvasConfig}
            onCanvasChange={handleCanvasChange}
            renderWidgetContent={(widget) => (
              <ErrorBoundary key={widget.id}>
                <SandboxWidget
                  widget={widget}
                  onFullscreen={(id) => setAppWindowMode(id, "maximized")}
                  onMinimize={(id) => setAppWindowMode(id, "floating")}
                />
              </ErrorBoundary>
            )}
            onOpenAudit={() => setIsAuditOpen(true)}
            onOpenTasks={() => setIsTaskDrawerOpen(true)}
            onOpenLLMSettings={() => { setIsLLMSettingsOpen(true); void refreshLLMConfiguration(); }}
            taskCount={taskCounts.active + taskCounts.attention}
            onOpenAppStore={() => setIsAppStoreOpen(true)}
            language={language}
            onLanguageChange={handleLanguageChange}
            theme={theme}
            onThemeChange={(preference) => themeControllerRef.current!.setPreference(preference)}
          />
          {appCenter("overlay")}
        </>
      )}

      <AgentChatOverlay
        open={isChatOpen}
        unreadCount={unreadCount}
        messages={messages}
        sessions={sessions}
        activeSessionId={activeSessionId}
        runningSessions={runningSessions}
        isConnected={isConnected}
        language={language}
        onOpenChange={handleChatOpenChange}
        onSendMessage={handleSendMessage}
        onSelectSession={handleSelectSession}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
        providers={llmProviders}
        modelSelection={sessions.find((session) => session.id === activeSessionId)?.model_selection ?? llmSettings.default_model}
        onModelChange={handleSessionModelChange}
        onManageModels={() => { setIsLLMSettingsOpen(true); void refreshLLMConfiguration(); }}
      />

      <LLMSettingsDialog
        open={isLLMSettingsOpen}
        language={language}
        catalog={llmCatalog}
        providers={llmProviders}
        settings={llmSettings}
        codingAgents={codingAgents}
        codingAgentSettings={codingAgentSettings}
        onClose={() => setIsLLMSettingsOpen(false)}
        onRefresh={refreshLLMConfiguration}
        onCreateProvider={(profile, credentials) => createProvider(API_BASE, profile, credentials)}
        onUpdateProvider={(providerId, profile, credentials) => updateProvider(API_BASE, providerId, profile, credentials)}
        onDeleteProvider={(providerId) => deleteProvider(API_BASE, providerId)}
        onDiscoverModels={(providerId) => discoverProviderModels(API_BASE, providerId)}
        onTestProvider={(providerId, modelId, mode) => testProviderConnection(API_BASE, providerId, modelId, mode)}
        onUpdateSettings={(patch) => updateLLMSettings(API_BASE, patch)}
        onUpdateCodingAgent={(patch) => updateCodingAgentSettings(API_BASE, patch)}
      />

      {/* Audit Log Panel Overlay */}
      <AuditLogPanel isOpen={isAuditOpen} onClose={() => setIsAuditOpen(false)} />
      <TaskDrawer
        open={isTaskDrawerOpen}
        language={language}
        onClose={() => setIsTaskDrawerOpen(false)}
        onCountsChange={setTaskCounts}
        onOpenSource={(run: AmbientRun) => {
          if (run.source_type === "chat" && run.source_id) {
            handleSelectSession(run.source_id);
            handleChatOpenChange(true);
            setIsTaskDrawerOpen(false);
          }
        }}
      />

      {/* 🧮 Mutation preview / rollback */}
      <MutationPreview
        preview={mutationPreview}
        onRollback={(ticketId) => {
          wsService.sendMessage({ type: "rollback_mutation", ticket_id: ticketId });
        }}
        onPin={(ticketId) => {
          wsService.sendMessage({ type: "pin_mutation_history", ticket_id: ticketId });
        }}
        onDismiss={(ticketId) => {
          setMutationPreview((curr) => (curr && curr.ticket_id === ticketId ? null : curr));
        }}
      />

      {/* 🛡️ OpenCode Permission Request Modal */}
      {pendingPermission && (
        <SystemDialog open blocking size="compact" title={language === "zh" ? "OpenCode 授权请求" : "OpenCode Permission Request"} description={language === "zh" ? "OpenCode 正在请求执行敏感操作。请确认是否允许。" : "OpenCode is requesting a sensitive action. Confirm whether it is allowed."}>
          <div className="system-dialog-body">
            <div className="system-dialog-code">
              <span className="system-dialog-code-label">【{language === "zh" ? "类型" : "Type"}: {pendingPermission.tool_call}】</span>
              {pendingPermission.details}
            </div>
            <div className="system-dialog-actions">
              <button
                onClick={() => handleResolvePermission(false)}
                className="system-button is-danger"
              >
                {language === "zh" ? "拒绝 (Deny)" : "Deny"}
              </button>
              <button
                onClick={() => handleResolvePermission(true)}
                className="system-button is-primary"
              >
                {language === "zh" ? "允许 (Allow)" : "Allow"}
              </button>
            </div>
          </div>
        </SystemDialog>
      )}

      {/* 🛡️ Backend Agent/MCP Permission Request Modal */}
      <AppPermissionModal
        pendingRequest={pendingBackendPermission}
        onResolve={handleResolveBackendPermission}
      />


      {/* 🧠 App 数据 Schema 智能对齐 Modal */}
      {pendingSchemaRequest && editedProposal && (
        <SystemDialog open blocking size="large" title={language === "zh" ? "App 数据 Schema 对齐" : "App Schema Alignment"} description={language === "zh" ? `为应用 ${pendingSchemaRequest.app_id} 规划全局关联与数据结构。` : `Plan global relationships and data structures for app ${pendingSchemaRequest.app_id}.`}>
          <div className="system-dialog-body flex flex-col gap-4">

            {/* Reused Schemas list */}
            {editedProposal.reused_schemas.length > 0 && (
              <div className="flex flex-col gap-3">
                <h4 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">
                  🔄 {language === "zh" ? "复用全局核心 Schema (推荐公共共享)" : "Reuse Global Core Schema (Recommended for Shared Data)"}
                </h4>
                {editedProposal.reused_schemas.map((rs, sIdx) => (
                  <div key={rs.id} className="border border-cyan-500/20 bg-cyan-950/10 rounded-xl p-4 flex flex-col gap-3">
                    <div className="flex items-start justify-between">
                      <div>
                        <span className="bg-cyan-500/10 text-cyan-400 text-[10px] px-2 py-0.5 rounded font-bold font-mono">
                          {rs.id}
                        </span>
                        <p className="text-slate-400 text-[11px] mt-1">{rs.reason}</p>
                      </div>
                    </div>

                    {/* Extended Properties */}
                    <div className="flex flex-col gap-2">
                      <span className="text-[11px] text-slate-400 font-medium">
                        {language === "zh" ? "应用自定义扩展属性:" : "Custom Extended Properties:"}
                      </span>
                      {Object.keys(rs.extended_properties).length === 0 ? (
                        <p className="text-slate-500 text-[11px] italic">
                          {language === "zh" ? "无自定义扩展属性" : "No custom extended properties"}
                        </p>
                      ) : (
                        <div className="flex flex-col gap-2">
                          {Object.entries(rs.extended_properties).map(([key, val]) => (
                            <div key={key} className="flex items-center gap-2">
                              <input
                                type="text"
                                value={key}
                                onChange={(e) => handleUpdateExtendedPropertyKey(sIdx, key, e.target.value)}
                                className="bg-black/30 border border-white/10 px-2.5 py-1 rounded text-xs text-white placeholder-slate-500 w-1/2 focus:outline-none focus:border-cyan-500"
                                placeholder={language === "zh" ? "属性名称" : "Property Name"}
                              />
                              <select
                                value={val}
                                onChange={(e) => handleUpdateExtendedPropertyType(sIdx, key, e.target.value)}
                                className="bg-black/30 border border-white/10 px-2 py-1 rounded text-xs text-slate-300 w-1/3 focus:outline-none focus:border-cyan-500"
                              >
                                <option value="string">String</option>
                                <option value="integer">Integer</option>
                                <option value="number">Number</option>
                                <option value="boolean">Boolean</option>
                              </select>
                              <button
                                onClick={() => handleRemoveExtendedProperty(sIdx, key)}
                                className="text-red-400 hover:text-red-300 p-1 text-xs"
                                title={language === "zh" ? "删除属性" : "Delete property"}
                              >
                                ✕
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      <button
                        onClick={() => handleAddExtendedProperty(sIdx)}
                        className="text-cyan-400 hover:text-cyan-300 text-xs font-semibold self-start flex items-center gap-1 mt-1"
                      >
                        + {language === "zh" ? "扩展新字段" : "Extend New Field"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* New Schemas list */}
            {editedProposal.new_schemas.length > 0 && (
              <div className="flex flex-col gap-3">
                <h4 className="text-xs font-semibold text-indigo-400 uppercase tracking-wider">
                  ✨ {language === "zh" ? "注册全新 Schema (本应用特有概念)" : "Register New Schema (App-specific Concept)"}
                </h4>
                {editedProposal.new_schemas.map((ns, sIdx) => (
                  <div key={sIdx} className="border border-indigo-500/20 bg-indigo-950/10 rounded-xl p-4 flex flex-col gap-3">
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={ns.id}
                        onChange={(e) => handleUpdateNewSchemaMeta(sIdx, "id", e.target.value)}
                        className="bg-black/30 border border-white/10 px-2.5 py-1 rounded text-xs text-white placeholder-slate-500 w-1/2 focus:outline-none focus:border-indigo-500 font-mono font-bold"
                        placeholder="Schema ID (例: Pomodoro)"
                      />
                      <button
                        onClick={() => handleRemoveNewSchema(sIdx)}
                        className="text-red-400 hover:text-red-300 text-xs ml-auto"
                      >
                        {language === "zh" ? "删除此实体" : "Delete Schema"}
                      </button>
                    </div>

                    <input
                      type="text"
                      value={ns.description}
                      onChange={(e) => handleUpdateNewSchemaMeta(sIdx, "description", e.target.value)}
                      className="bg-black/30 border border-white/10 px-2.5 py-1 rounded text-xs text-slate-300 placeholder-slate-500 w-full focus:outline-none focus:border-indigo-500"
                      placeholder={language === "zh" ? "实体说明" : "Schema Description"}
                    />

                    {/* Properties List */}
                    <div className="flex flex-col gap-2">
                      <span className="text-[11px] text-slate-400 font-medium">
                        {language === "zh" ? "属性结构定义:" : "Property Structures:"}
                      </span>
                      {Object.keys(ns.properties).length === 0 ? (
                        <p className="text-slate-500 text-[11px] italic">
                          {language === "zh" ? "未定义属性" : "No properties defined"}
                        </p>
                      ) : (
                        <div className="flex flex-col gap-2">
                          {Object.entries(ns.properties).map(([key, val]) => (
                            <div key={key} className="flex items-center gap-2">
                              <input
                                type="text"
                                value={key}
                                onChange={(e) => handleUpdateNewSchemaPropertyKey(sIdx, key, e.target.value)}
                                className="bg-black/30 border border-white/10 px-2.5 py-1 rounded text-xs text-white placeholder-slate-500 w-1/2 focus:outline-none focus:border-indigo-500"
                                placeholder={language === "zh" ? "属性名称" : "Property Name"}
                              />
                              <select
                                value={val}
                                onChange={(e) => handleUpdateNewSchemaPropertyType(sIdx, key, e.target.value)}
                                className="bg-black/30 border border-white/10 px-2 py-1 rounded text-xs text-slate-300 w-1/3 focus:outline-none focus:border-indigo-500"
                              >
                                <option value="string">String</option>
                                <option value="integer">Integer</option>
                                <option value="number">Number</option>
                                <option value="boolean">Boolean</option>
                              </select>
                              <button
                                onClick={() => handleRemoveNewSchemaProperty(sIdx, key)}
                                className="text-red-400 hover:text-red-300 p-1 text-xs"
                                title={language === "zh" ? "删除属性" : "Delete property"}
                              >
                                ✕
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      <button
                        onClick={() => handleAddNewSchemaProperty(sIdx)}
                        className="text-indigo-400 hover:text-indigo-300 text-xs font-semibold self-start flex items-center gap-1 mt-1"
                      >
                        + {language === "zh" ? "新增属性" : "Add Property"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* 💬 自然语言微调反馈输入 */}
            <div className="flex flex-col gap-2 border-t border-white/10 pt-4 mt-2">
              <span className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5">
                💬 {language === "zh" ? "使用自然语言调整 Schema 定义 (可选):" : "Adjust Schema via Natural Language (Optional):"}
              </span>
              <textarea
                value={schemaFeedback}
                onChange={(e) => setSchemaFeedback(e.target.value)}
                placeholder={
                  language === "zh"
                    ? "例如：'将 PomodoroSession 重命名为 TomatoTimer'、'为 Task 添加 priority 属性并设定为 String 类型'..."
                    : "e.g. 'Rename PomodoroSession to TomatoTimer', 'Add priority field (String) to Task'..."
                }
                className="bg-black/40 border border-white/10 px-3 py-2 rounded-lg text-xs text-white placeholder-slate-500 w-full min-h-[60px] focus:outline-none focus:border-cyan-500 resize-none font-sans"
              />
            </div>

            {/* Bottom Actions */}
            <div className="flex items-center gap-3 pt-2 mt-2 border-t border-white/10 font-medium">
              <button
                onClick={handleAddNewSchema}
                className="px-3 py-1.5 rounded-lg border border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10 transition-colors text-xs"
              >
                + {language === "zh" ? "添加全新自定义 Schema" : "Add New Custom Schema"}
              </button>
              
              <div className="flex items-center gap-3 ml-auto">
                <button
                  onClick={() => handleResolveSchemaRequest("rework_plan")}
                  className="px-3.5 py-1.5 rounded-lg border border-yellow-500/30 text-yellow-400 hover:bg-yellow-500/10 transition-colors text-xs font-sans"
                >
                  {language === "zh" ? "返回开发计划阶段 (Back to Plan)" : "Rework Plan"}
                </button>
                <button
                  onClick={() => handleResolveSchemaRequest(false)}
                  className="px-3.5 py-1.5 rounded-lg text-slate-400 hover:bg-white/5 transition-colors text-xs"
                >
                  {language === "zh" ? "拒绝并取消生成 (Cancel)" : "Cancel"}
                </button>
                {schemaFeedback.trim() && (
                  <button
                    onClick={() => handleResolveSchemaRequest("refine", schemaFeedback)}
                    className="px-4 py-1.5 rounded-lg bg-indigo-900/60 hover:bg-indigo-800/80 border border-indigo-500/30 text-indigo-300 text-xs transition-colors"
                  >
                    {language === "zh" ? "调整 Schema (Refine)" : "Refine"}
                  </button>
                )}
                <button
                  onClick={() => handleResolveSchemaRequest(true)}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 transition-all text-white text-xs shadow-md shadow-cyan-600/10"
                >
                  {language === "zh" ? "确认对齐并编码 (Approve)" : "Approve"}
                </button>
              </div>
            </div>
          </div>
        </SystemDialog>
      )}

      {/* 📝 App 开发计划 智能对齐 Modal */}
      {pendingPlanRequest && (
        <SystemDialog open blocking size="large" title={language === "zh" ? "App 开发计划确认" : "App Development Plan Confirmation"} description={language === "zh" ? `为应用 ${pendingPlanRequest.app_id} 确认最终开发方案。` : `Confirm the final development plan for app ${pendingPlanRequest.app_id}.`}>
          <div className="system-dialog-body flex flex-col gap-4">

            {/* Read-only Plan content */}
            <div className="border border-white/10 bg-white/[0.02] rounded-xl p-4 flex flex-col gap-3 font-sans text-xs">
              <h4 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">
                📋 {language === "zh" ? "开发方案概述" : "Development Plan Summary"}
              </h4>
              <div className="text-slate-300 whitespace-pre-wrap leading-relaxed max-h-[40vh] overflow-y-auto pr-1">
                {pendingPlanRequest.plan}
              </div>
            </div>

            {/* 💬 自然语言微调反馈输入 */}
            <div className="flex flex-col gap-2 border-t border-white/10 pt-4 mt-2">
              <span className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5 font-sans">
                💬 {language === "zh" ? "使用自然语言微调开发计划 (可选):" : "Refine Development Plan via Natural Language (Optional):"}
              </span>
              <textarea
                value={planFeedback}
                onChange={(e) => setPlanFeedback(e.target.value)}
                placeholder={
                  language === "zh"
                    ? "例如：'请使用深蓝色玻璃拟态风格'、'为计算器增加负数输入功能'..."
                    : "e.g. 'Please use dark blue glassmorphism style', 'Add support for negative number inputs'..."
                }
                className="bg-black/40 border border-white/10 px-3 py-2 rounded-lg text-xs text-white placeholder-slate-500 w-full min-h-[60px] focus:outline-none focus:border-cyan-500 resize-none font-sans"
              />
            </div>

            {/* Bottom Actions */}
            <div className="flex items-center gap-3 pt-2 mt-2 border-t border-white/10 font-medium">
              <div className="flex items-center gap-3 ml-auto">
                <button
                  onClick={() => handleResolvePlanRequest(false)}
                  className="px-3.5 py-1.5 rounded-lg text-slate-400 hover:bg-white/5 transition-colors text-xs font-sans"
                >
                  {language === "zh" ? "拒绝并取消生成 (Cancel)" : "Cancel"}
                </button>
                {planFeedback.trim() && (
                  <button
                    onClick={() => handleResolvePlanRequest("refine", planFeedback)}
                    className="px-4 py-1.5 rounded-lg bg-indigo-900/60 hover:bg-indigo-800/80 border border-indigo-500/30 text-indigo-300 text-xs transition-colors font-sans"
                  >
                    {language === "zh" ? "调整开发计划 (Refine)" : "Refine Plan"}
                  </button>
                )}
                <button
                  onClick={() => handleResolvePlanRequest(true)}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 transition-all text-white text-xs shadow-md shadow-cyan-600/10 font-sans"
                >
                  {language === "zh" ? "确认计划并开始开发 (Approve)" : "Approve & Develop"}
                </button>
              </div>
            </div>
          </div>
        </SystemDialog>
      )}
      {/* 🔍 Schema 校验警告与返工 Modal */}
      {pendingVerificationRequest && (
        <SystemDialog open blocking size="large" title={language === "zh" ? "Schema 校验未完全对齐" : "Schema Alignment Warning"} description={language === "zh" ? `应用 ${pendingVerificationRequest.app_id} 的代码在 Graph DB 校验中发现不一致，请选择处理方式。` : `Discrepancies found in Graph DB validation for app ${pendingVerificationRequest.app_id}. Choose an action.`}>
          <div className="system-dialog-body flex flex-col gap-4">

            {/* Verification Report content */}
            <div className="border border-red-500/20 bg-red-950/5 rounded-xl p-4 flex flex-col gap-3 font-sans text-xs">
              <h4 className="text-xs font-semibold text-red-400 uppercase tracking-wider font-sans">
                📋 {language === "zh" ? "校验报告 (Verification Report)" : "Verification Report"}
              </h4>
              <div className="text-slate-300 whitespace-pre-wrap leading-relaxed max-h-[40vh] overflow-y-auto pr-1 font-mono text-[11px]">
                {pendingVerificationRequest.report}
              </div>
            </div>

            {/* Per-field options (Direction A: structured diff → checkbox UI) */}
            {Array.isArray(pendingVerificationRequest.options) && pendingVerificationRequest.options.length > 0 && (
              <div className="border border-orange-500/20 bg-orange-950/5 rounded-xl p-4 flex flex-col gap-3 font-sans text-xs">
                <h4 className="text-xs font-semibold text-orange-400 uppercase tracking-wider font-sans">
                  🧩 {language === "zh" ? "待扩展字段 (Extend Schema For)" : "Extend Schema For"}
                </h4>
                <div className="flex flex-col gap-2">
                  {pendingVerificationRequest.options.map((opt: any, idx: number) => (
                    <label key={idx} className="flex items-start gap-2 cursor-pointer hover:bg-white/5 px-2 py-1.5 rounded transition-colors">
                      <input
                        type="checkbox"
                        defaultChecked
                        className="mt-0.5 accent-orange-500"
                        data-verify-opt={`${opt.node_type}:${opt.property_name}`}
                      />
                      <div className="flex-1">
                        <div className="text-slate-200 font-mono">
                          <span className="text-orange-300 font-bold">{opt.node_type}</span>
                          <span className="text-slate-500">.</span>
                          <span className="text-cyan-300">{opt.property_name}</span>
                          <span className="text-slate-500"> : </span>
                          <span className="text-emerald-300">{opt.detected_type}</span>
                        </div>
                        <div className="text-[10px] text-slate-500 mt-0.5">
                          {language === "zh" ? "风险: " : "Risk: "}{opt.risk === "safe" ? (language === "zh" ? "安全 (可自动扩展)" : "SAFE (auto-extensible)") : (language === "zh" ? "需要审核" : "NEEDS REVIEW")}
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* 💬 返工反馈输入 (可选) */}
            <div className="flex flex-col gap-2 border-t border-white/10 pt-4 mt-2">
              <span className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5 font-sans">
                💬 {language === "zh" ? "返工修改指令 (供智能体修复代码或重新规划):" : "Rework Instructions (for agent to fix code or replan):"}
              </span>
              <textarea
                value={verificationFeedback}
                onChange={(e) => setVerificationFeedback(e.target.value)}
                placeholder={
                  language === "zh"
                    ? "例如：'请修复 mutations 使用的字段名，使其与 schema 严格一致'、'我们将 Schema 属性重新对齐'..."
                    : "e.g. 'Please fix the field names in mutations to strictly match the schema', 'We will realign the schema properties'..."
                }
                className="bg-black/40 border border-white/10 px-3 py-2 rounded-lg text-xs text-white placeholder-slate-500 w-full min-h-[60px] focus:outline-none focus:border-red-500 resize-none font-sans"
              />
            </div>

            {/* Bottom Actions */}
            <div className="flex items-center gap-3 pt-2 mt-2 border-t border-white/10 font-medium">
              <button
                onClick={() => handleResolveVerificationRequest("approve", verificationFeedback, [])}
                className="px-3.5 py-1.5 rounded-lg border border-slate-700 text-slate-300 hover:bg-white/5 transition-colors text-xs font-sans"
              >
                {language === "zh" ? "直接忽略并保存 (Bypass & Save)" : "Bypass & Save"}
              </button>

              <div className="flex items-center gap-2.5 ml-auto">
                <button
                  onClick={() => {
                    const checked = collectCheckedOptions(pendingVerificationRequest);
                    handleResolveVerificationRequest("rework_plan", verificationFeedback, checked);
                  }}
                  className="px-3 py-1.5 rounded-lg border border-yellow-500/20 text-yellow-400 hover:bg-yellow-500/10 transition-colors text-xs font-sans"
                >
                  {language === "zh" ? "返工 Plan" : "Rework Plan"}
                </button>
                <button
                  onClick={() => {
                    const checked = collectCheckedOptions(pendingVerificationRequest);
                    handleResolveVerificationRequest("rework_schema", verificationFeedback, checked);
                  }}
                  className="px-3 py-1.5 rounded-lg border border-orange-500/20 text-orange-400 hover:bg-orange-500/10 transition-colors text-xs font-sans"
                >
                  {language === "zh" ? "扩展勾选字段并重新生成 (Extend Selected)" : "Extend Selected Properties"}
                </button>
                <button
                  onClick={() => {
                    const checked = collectCheckedOptions(pendingVerificationRequest);
                    handleResolveVerificationRequest("rework_code", verificationFeedback, checked);
                  }}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-red-600 to-orange-600 hover:from-red-500 hover:to-orange-500 transition-all text-white text-xs shadow-md shadow-red-600/10 font-sans"
                >
                  {language === "zh" ? "智能修复代码 (Auto-Fix)" : "Auto-Fix Code"}
                </button>
              </div>
            </div>
          </div>
        </SystemDialog>
      )}
    </div>
  );
}

export default App;
