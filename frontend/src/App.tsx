import { useState, useEffect } from "react";
import wsService from "./services/websocket";
import { ChatPanel, type Message } from "./components/ChatPanel";
import { DashboardCanvas, type Widget } from "./components/DashboardCanvas";
import { SandboxWidget } from "./components/SandboxWidget";
import { AuditLogPanel } from "./components/AuditLogPanel";
import { SessionSidebar, type Session } from "./components/SessionSidebar";
import { AppStoreModal } from "./components/AppStoreModal";
import { AppPermissionModal } from "./components/AppPermissionModal";
import { MutationPreview, type MutationPreviewData } from "./components/MutationPreview";


function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const API_BASE = `http://${window.location.hostname}:8000`;
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [widgetSpans, setWidgetSpans] = useState<Record<string, {cols: number, rows: number}>>({});
  const [isConnected, setIsConnected] = useState(false);

  const saveCanvasConfig = async (ids: string[], spans: Record<string, {cols: number, rows: number}>) => {
    try {
      await fetch(`${API_BASE}/api/canvas`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned_ids: ids, widget_spans: spans, version: 2 }),
      });
    } catch (err) {
      console.error("Error saving canvas configuration:", err);
    }
  };
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isAppStoreOpen, setIsAppStoreOpen] = useState(false);
  const [fullscreenAppId, setFullscreenAppId] = useState<string | null>(null);
  const [isAuditOpen, setIsAuditOpen] = useState(false);
  
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

  const [chatWidth, setChatWidth] = useState<number>(() => {
    const saved = localStorage.getItem("chat_panel_width");
    return saved ? parseInt(saved, 10) : 320;
  });

  const [mutationPreview, setMutationPreview] = useState<MutationPreviewData | null>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = chatWidth;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const deltaX = moveEvent.clientX - startX;
      const newWidth = Math.max(240, Math.min(800, startWidth + deltaX));
      setChatWidth(newWidth);
      localStorage.setItem("chat_panel_width", newWidth.toString());
    };

    const handleMouseUp = () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  };

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

    // Check connection state every second
    const interval = setInterval(() => {
      setIsConnected(wsService.isConnected());
    }, 1000);

    return () => {
      clearInterval(interval);
      wsService.disconnect();
    };
  }, []);

  // 2. Fetch messages & pinned apps when activeSessionId changes
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

    // Load pinned app IDs and fetch their source files from canvas configuration
    const loadCanvasConfig = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/canvas`);
        if (res.ok) {
          const config = await res.json();
          const ids = config.pinned_ids || [];
          let spans = config.widget_spans || {};
          const version = config.version || 1;

          if (version < 2) {
            const migratedSpans: Record<string, {cols: number, rows: number}> = {};
            for (const key of Object.keys(spans)) {
              let cols = spans[key].cols || 1;
              let rows = spans[key].rows || 1;
              // Map old 1-3 cols to 4-12 cols
              cols = Math.min(12, cols * 4);
              // Map old 1-4 rows to 4-16 rows (where 1 old row of 320px = 4 new rows of 80px)
              rows = Math.min(16, rows * 4);
              migratedSpans[key] = { cols, rows };
            }
            spans = migratedSpans;
            saveCanvasConfig(ids, spans);
          }

          setPinnedIds(ids);
          setWidgetSpans(spans);

          const loadedWidgets: Widget[] = [];
          for (const id of ids) {
            try {
              const appRes = await fetch(`${API_BASE}/api/apps/${id}`);
              if (appRes.ok) {
                const appData = await appRes.json();
                loadedWidgets.push(appData);
              }
            } catch (err) {
              console.error(`Error loading pinned widget ${id}:`, err);
            }
          }
          setWidgets(loadedWidgets);
        }
      } catch (err) {
        console.error("Error loading canvas configuration:", err);
      }
    };

    loadSessionHistory();
    loadCanvasConfig();

    // Connect WebSocket
    const wsUrl = `ws://${window.location.hostname}:8000/ws/chat`;
    wsService.connect(wsUrl, activeSessionId, (data) => {
      if (data.type === "ack" || data.type === "reply") {
        setMessages((prev) => {
          const isRealReply = data.message.id !== undefined && data.message.id !== -1;
          const cleanPrev = isRealReply ? prev.filter((m) => m.id !== -1) : prev;
          if (data.message.id === -1) {
            const tempIndex = cleanPrev.map((m) => m.id).lastIndexOf(-1);
            if (tempIndex !== -1) {
              const newMsgs = [...cleanPrev];
              newMsgs[tempIndex] = data.message;
              return newMsgs;
            }
          }
          return [...cleanPrev, data.message];
        });
      } else if (data.type === "widget") {
        // Add or update widget in state
        setWidgets((prev) => {
          const exists = prev.some((w) => w.id === data.widget.id);
          return exists
            ? prev.map((w) => (w.id === data.widget.id ? data.widget : w))
            : [...prev, data.widget];
        });
        
        // Auto-pin newly created widget and sync to backend
        setPinnedIds((prev) => {
          const updated = prev.includes(data.widget.id) ? prev : [...prev, data.widget.id];
          saveCanvasConfig(updated, widgetSpans);
          return updated;
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
    });

    return () => {
      wsService.disconnect();
    };
  }, [activeSessionId]);

  const handleCreateSession = async () => {
    const newId = Math.random().toString(36).substring(2, 15);
    const newTitle = `New Chat ${sessions.length + 1}`;
    try {
      const res = await fetch(`${API_BASE}/api/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: newId, title: newTitle }),
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
    if (!activeSessionId) return;
    setPinnedIds((prev) => {
      const updated = prev.filter((wId) => wId !== id);
      const updatedSpans = { ...widgetSpans };
      delete updatedSpans[id];
      setWidgetSpans(updatedSpans);
      saveCanvasConfig(updated, updatedSpans);
      return updated;
    });
    setWidgets((prev) => prev.filter((w) => w.id !== id));
  };

  const handlePinWidget = async (id: string) => {
    if (!activeSessionId) return;
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
        setPinnedIds((prev) => {
          const updated = prev.includes(id) ? prev : [...prev, id];
          saveCanvasConfig(updated, widgetSpans);
          return updated;
        });
      }
    } catch (err) {
      console.error("Error pinning app:", err);
    }
  };

  const handleRunFullscreen = (id: string) => {
    setFullscreenAppId(id);
    setIsAppStoreOpen(false);
  };



  return (
    <div className="flex w-screen h-screen overflow-hidden text-slate-100 font-sans bg-[#08080a]">
      {/* Session History Sidebar */}
      <SessionSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        runningSessions={runningSessions}
        onSelectSession={(id) => {
          setActiveSessionId(id);
          localStorage.setItem("last_active_session", id);
          if (!isSidebarOpen) {
            setIsSidebarOpen(true);
          }
        }}
        onCreateSession={() => {
          handleCreateSession();
          if (!isSidebarOpen) {
            setIsSidebarOpen(true);
          }
        }}
        onDeleteSession={handleDeleteSession}
        isOpen={isSidebarOpen}
        onToggleOpen={() => setIsSidebarOpen(!isSidebarOpen)}
      />

      {/* Chat Panel */}
      {isSidebarOpen && (
        <ChatPanel
          messages={messages}
          onSendMessage={handleSendMessage}
          isConnected={isConnected}
          width={chatWidth}
          onHideChat={() => setIsSidebarOpen(false)}
        />
      )}

      {/* Drag Splitter Handle */}
      {isSidebarOpen && (
        <div
          onMouseDown={handleMouseDown}
          className="w-1 h-full cursor-col-resize hover:bg-cyan-500/30 active:bg-cyan-600/50 transition-colors bg-white/[0.04] shrink-0"
          title="Drag to resize panels"
        />
      )}

      {/* Workspace Canvas */}
      <DashboardCanvas
        activeSessionId={activeSessionId}
        widgets={widgets}
        onRemoveWidget={handleRemoveWidget}
        renderWidgetContent={(widget) => (
          <SandboxWidget
            widget={widget}
            onFullscreen={(id) => setFullscreenAppId(id)}
          />
        )}
        onOpenAudit={() => setIsAuditOpen(true)}
        onOpenAppStore={() => setIsAppStoreOpen(true)}
        onFullscreenWidget={(id) => setFullscreenAppId(id)}
        fullscreenAppId={fullscreenAppId}
        widgetSpans={widgetSpans}
        onWidgetSpansChange={(updatedSpans) => {
          setWidgetSpans(updatedSpans);
          saveCanvasConfig(pinnedIds, updatedSpans);
        }}
        showChat={isSidebarOpen}
        onToggleChat={() => setIsSidebarOpen(!isSidebarOpen)}
      />

      {/* Audit Log Panel Overlay */}
      <AuditLogPanel isOpen={isAuditOpen} onClose={() => setIsAuditOpen(false)} />

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

      {/* AppStore Floating Modal */}
      <AppStoreModal
        isOpen={isAppStoreOpen}
        onClose={() => setIsAppStoreOpen(false)}
        pinnedWidgetIds={pinnedIds}
        onPinWidget={handlePinWidget}
        onUnpinWidget={handleRemoveWidget}
        onRunFullscreen={handleRunFullscreen}
      />

      {/* 🛡️ OpenCode Permission Request Modal */}
      {pendingPermission && (
        <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/80 backdrop-blur-md">
          <div className="bg-[#0b0b0e] border border-white/10 p-6 rounded-xl max-w-md w-full mx-4 shadow-2xl animate-in fade-in zoom-in-95 duration-200">
            <h3 className="text-base font-semibold text-white mb-1.5 flex items-center gap-2">
              🛡️ OpenCode 授权请求
            </h3>
            <p className="text-slate-400 text-xs mb-4 leading-relaxed">
              OpenCode 正在请求执行以下敏感操作。请确认是否允许此操作：
            </p>
            <div className="bg-black/40 border border-white/5 rounded-lg p-3 mb-5 font-mono text-xs text-cyan-400 break-all select-all">
              <span className="text-slate-500 font-sans block mb-1">【类型: {pendingPermission.tool_call}】</span>
              {pendingPermission.details}
            </div>
            <div className="flex items-center justify-end gap-3 font-medium">
              <button
                onClick={() => handleResolvePermission(false)}
                className="px-3.5 py-1.5 rounded-lg text-slate-400 hover:bg-white/5 transition-colors text-xs"
              >
                拒绝 (Deny)
              </button>
              <button
                onClick={() => handleResolvePermission(true)}
                className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 transition-all text-white text-xs shadow-md shadow-cyan-600/10"
              >
                允许 (Allow)
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 🛡️ Backend Agent/MCP Permission Request Modal */}
      <AppPermissionModal
        pendingRequest={pendingBackendPermission}
        onResolve={handleResolveBackendPermission}
      />


      {/* 🧠 App 数据 Schema 智能对齐 Modal */}
      {pendingSchemaRequest && editedProposal && (
        <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/80 backdrop-blur-md overflow-y-auto py-10">
          <div className="bg-[#0b0b0e] border border-white/10 p-6 rounded-2xl max-w-2xl w-full mx-4 shadow-2xl animate-in fade-in zoom-in-95 duration-200 text-white font-sans max-h-[85vh] overflow-y-auto flex flex-col gap-4">
            <div>
              <h3 className="text-base font-semibold text-white mb-1.5 flex items-center gap-2">
                🧠 App 数据 Schema 对齐
              </h3>
              <p className="text-slate-400 text-xs leading-relaxed">
                为应用 <span className="text-cyan-400 font-mono font-bold">{pendingSchemaRequest.app_id}</span> 规划最规范的全局关联与数据结构，消除数据碎片并确保多 Widget 协同。
              </p>
            </div>

            {/* Reused Schemas list */}
            {editedProposal.reused_schemas.length > 0 && (
              <div className="flex flex-col gap-3">
                <h4 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">
                  🔄 复用全局核心 Schema (推荐公共共享)
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
                      <span className="text-[11px] text-slate-400 font-medium">应用自定义扩展属性:</span>
                      {Object.keys(rs.extended_properties).length === 0 ? (
                        <p className="text-slate-500 text-[11px] italic">无自定义扩展属性</p>
                      ) : (
                        <div className="flex flex-col gap-2">
                          {Object.entries(rs.extended_properties).map(([key, val]) => (
                            <div key={key} className="flex items-center gap-2">
                              <input
                                type="text"
                                value={key}
                                onChange={(e) => handleUpdateExtendedPropertyKey(sIdx, key, e.target.value)}
                                className="bg-black/30 border border-white/10 px-2.5 py-1 rounded text-xs text-white placeholder-slate-500 w-1/2 focus:outline-none focus:border-cyan-500"
                                placeholder="属性名称"
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
                                title="删除属性"
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
                        + 扩展新字段
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
                  ✨ 注册全新 Schema (本应用特有概念)
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
                        删除此实体
                      </button>
                    </div>

                    <input
                      type="text"
                      value={ns.description}
                      onChange={(e) => handleUpdateNewSchemaMeta(sIdx, "description", e.target.value)}
                      className="bg-black/30 border border-white/10 px-2.5 py-1 rounded text-xs text-slate-300 placeholder-slate-500 w-full focus:outline-none focus:border-indigo-500"
                      placeholder="实体说明"
                    />

                    {/* Properties List */}
                    <div className="flex flex-col gap-2">
                      <span className="text-[11px] text-slate-400 font-medium">属性结构定义:</span>
                      {Object.keys(ns.properties).length === 0 ? (
                        <p className="text-slate-500 text-[11px] italic">未定义属性</p>
                      ) : (
                        <div className="flex flex-col gap-2">
                          {Object.entries(ns.properties).map(([key, val]) => (
                            <div key={key} className="flex items-center gap-2">
                              <input
                                type="text"
                                value={key}
                                onChange={(e) => handleUpdateNewSchemaPropertyKey(sIdx, key, e.target.value)}
                                className="bg-black/30 border border-white/10 px-2.5 py-1 rounded text-xs text-white placeholder-slate-500 w-1/2 focus:outline-none focus:border-indigo-500"
                                placeholder="属性名称"
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
                                title="删除属性"
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
                        + 新增属性
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* 💬 自然语言微调反馈输入 */}
            <div className="flex flex-col gap-2 border-t border-white/10 pt-4 mt-2">
              <span className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5">
                💬 使用自然语言调整 Schema 定义 (可选):
              </span>
              <textarea
                value={schemaFeedback}
                onChange={(e) => setSchemaFeedback(e.target.value)}
                placeholder="例如：'将 PomodoroSession 重命名为 TomatoTimer'、'为 Task 添加 priority 属性并设定为 String 类型'..."
                className="bg-black/40 border border-white/10 px-3 py-2 rounded-lg text-xs text-white placeholder-slate-500 w-full min-h-[60px] focus:outline-none focus:border-cyan-500 resize-none font-sans"
              />
            </div>

            {/* Bottom Actions */}
            <div className="flex items-center gap-3 pt-2 mt-2 border-t border-white/10 font-medium">
              <button
                onClick={handleAddNewSchema}
                className="px-3 py-1.5 rounded-lg border border-indigo-500/30 text-indigo-400 hover:bg-indigo-500/10 transition-colors text-xs"
              >
                + 添加全新自定义 Schema
              </button>
              
              <div className="flex items-center gap-3 ml-auto">
                <button
                  onClick={() => handleResolveSchemaRequest("rework_plan")}
                  className="px-3.5 py-1.5 rounded-lg border border-yellow-500/30 text-yellow-400 hover:bg-yellow-500/10 transition-colors text-xs font-sans"
                >
                  返回开发计划阶段 (Back to Plan)
                </button>
                <button
                  onClick={() => handleResolveSchemaRequest(false)}
                  className="px-3.5 py-1.5 rounded-lg text-slate-400 hover:bg-white/5 transition-colors text-xs"
                >
                  拒绝并取消生成 (Cancel)
                </button>
                {schemaFeedback.trim() && (
                  <button
                    onClick={() => handleResolveSchemaRequest("refine", schemaFeedback)}
                    className="px-4 py-1.5 rounded-lg bg-indigo-900/60 hover:bg-indigo-800/80 border border-indigo-500/30 text-indigo-300 text-xs transition-colors"
                  >
                    调整 Schema (Refine)
                  </button>
                )}
                <button
                  onClick={() => handleResolveSchemaRequest(true)}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 transition-all text-white text-xs shadow-md shadow-cyan-600/10"
                >
                  确认对齐并编码 (Approve)
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 📝 App 开发计划 智能对齐 Modal */}
      {pendingPlanRequest && (
        <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/80 backdrop-blur-md overflow-y-auto py-10">
          <div className="bg-[#0b0b0e] border border-white/10 p-6 rounded-2xl max-w-2xl w-full mx-4 shadow-2xl animate-in fade-in zoom-in-95 duration-200 text-white font-sans max-h-[85vh] overflow-y-auto flex flex-col gap-4">
            <div>
              <h3 className="text-base font-semibold text-white mb-1.5 flex items-center gap-2 font-sans">
                📝 App 开发计划确认
              </h3>
              <p className="text-slate-400 text-xs leading-relaxed font-sans">
                为应用 <span className="text-cyan-400 font-mono font-bold">{pendingPlanRequest.app_id}</span> 确认最终开发方案。
              </p>
            </div>

            {/* Read-only Plan content */}
            <div className="border border-white/10 bg-white/[0.02] rounded-xl p-4 flex flex-col gap-3 font-sans text-xs">
              <h4 className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">
                📋 开发方案概述
              </h4>
              <div className="text-slate-300 whitespace-pre-wrap leading-relaxed max-h-[40vh] overflow-y-auto pr-1">
                {pendingPlanRequest.plan}
              </div>
            </div>

            {/* 💬 自然语言微调反馈输入 */}
            <div className="flex flex-col gap-2 border-t border-white/10 pt-4 mt-2">
              <span className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5 font-sans">
                💬 使用自然语言微调开发计划 (可选):
              </span>
              <textarea
                value={planFeedback}
                onChange={(e) => setPlanFeedback(e.target.value)}
                placeholder="例如：'请使用深蓝色玻璃拟态风格'、'为计算器增加负数输入功能'..."
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
                  拒绝并取消生成 (Cancel)
                </button>
                {planFeedback.trim() && (
                  <button
                    onClick={() => handleResolvePlanRequest("refine", planFeedback)}
                    className="px-4 py-1.5 rounded-lg bg-indigo-900/60 hover:bg-indigo-800/80 border border-indigo-500/30 text-indigo-300 text-xs transition-colors font-sans"
                  >
                    调整开发计划 (Refine)
                  </button>
                )}
                <button
                  onClick={() => handleResolvePlanRequest(true)}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 transition-all text-white text-xs shadow-md shadow-cyan-600/10 font-sans"
                >
                  确认计划并开始开发 (Approve)
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {/* 🔍 Schema 校验警告与返工 Modal */}
      {pendingVerificationRequest && (
        <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/80 backdrop-blur-md overflow-y-auto py-10">
          <div className="bg-[#0b0b0e] border border-white/10 p-6 rounded-2xl max-w-2xl w-full mx-4 shadow-2xl animate-in fade-in zoom-in-95 duration-200 text-white font-sans max-h-[85vh] overflow-y-auto flex flex-col gap-4">
            <div>
              <h3 className="text-base font-semibold text-white mb-1.5 flex items-center gap-2 font-sans">
                ⚠️ Schema 校验未完全对齐
              </h3>
              <p className="text-slate-400 text-xs leading-relaxed font-sans">
                应用 <span className="text-cyan-400 font-mono font-bold">{pendingVerificationRequest.app_id}</span> 的代码在 Graph DB 校验中发现不一致，请选择处理方式。
              </p>
            </div>

            {/* Verification Report content */}
            <div className="border border-red-500/20 bg-red-950/5 rounded-xl p-4 flex flex-col gap-3 font-sans text-xs">
              <h4 className="text-xs font-semibold text-red-400 uppercase tracking-wider font-sans">
                📋 校验报告 (Verification Report)
              </h4>
              <div className="text-slate-300 whitespace-pre-wrap leading-relaxed max-h-[40vh] overflow-y-auto pr-1 font-mono text-[11px]">
                {pendingVerificationRequest.report}
              </div>
            </div>

            {/* Per-field options (Direction A: structured diff → checkbox UI) */}
            {Array.isArray(pendingVerificationRequest.options) && pendingVerificationRequest.options.length > 0 && (
              <div className="border border-orange-500/20 bg-orange-950/5 rounded-xl p-4 flex flex-col gap-3 font-sans text-xs">
                <h4 className="text-xs font-semibold text-orange-400 uppercase tracking-wider font-sans">
                  🧩 待扩展字段 (Extend Schema For)
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
                          Risk: {opt.risk === "safe" ? "SAFE (auto-extensible)" : "NEEDS REVIEW"}
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
                💬 返工修改指令 (供智能体修复代码或重新规划):
              </span>
              <textarea
                value={verificationFeedback}
                onChange={(e) => setVerificationFeedback(e.target.value)}
                placeholder="例如：'请修复 mutations 使用的字段名，使其与 schema 严格一致'、'我们将 Schema 属性重新对齐'..."
                className="bg-black/40 border border-white/10 px-3 py-2 rounded-lg text-xs text-white placeholder-slate-500 w-full min-h-[60px] focus:outline-none focus:border-red-500 resize-none font-sans"
              />
            </div>

            {/* Bottom Actions */}
            <div className="flex items-center gap-3 pt-2 mt-2 border-t border-white/10 font-medium">
              <button
                onClick={() => handleResolveVerificationRequest("approve", verificationFeedback, [])}
                className="px-3.5 py-1.5 rounded-lg border border-slate-700 text-slate-300 hover:bg-white/5 transition-colors text-xs font-sans"
              >
                直接忽略并保存 (Bypass & Save)
              </button>

              <div className="flex items-center gap-2.5 ml-auto">
                <button
                  onClick={() => {
                    const checked = collectCheckedOptions(pendingVerificationRequest);
                    handleResolveVerificationRequest("rework_plan", verificationFeedback, checked);
                  }}
                  className="px-3 py-1.5 rounded-lg border border-yellow-500/20 text-yellow-400 hover:bg-yellow-500/10 transition-colors text-xs font-sans"
                >
                  返工 Plan
                </button>
                <button
                  onClick={() => {
                    const checked = collectCheckedOptions(pendingVerificationRequest);
                    handleResolveVerificationRequest("rework_schema", verificationFeedback, checked);
                  }}
                  className="px-3 py-1.5 rounded-lg border border-orange-500/20 text-orange-400 hover:bg-orange-500/10 transition-colors text-xs font-sans"
                >
                  扩展勾选字段并重新生成 (Extend Selected)
                </button>
                <button
                  onClick={() => {
                    const checked = collectCheckedOptions(pendingVerificationRequest);
                    handleResolveVerificationRequest("rework_code", verificationFeedback, checked);
                  }}
                  className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-red-600 to-orange-600 hover:from-red-500 hover:to-orange-500 transition-all text-white text-xs shadow-md shadow-red-600/10 font-sans"
                >
                  智能修复代码 (Auto-Fix)
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
