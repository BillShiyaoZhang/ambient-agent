import { useState, useEffect } from "react";
import wsService from "./services/websocket";
import { ChatPanel, type Message } from "./components/ChatPanel";
import { DashboardCanvas, type Widget } from "./components/DashboardCanvas";
import { SandboxWidget } from "./components/SandboxWidget";
import { AuditLogPanel } from "./components/AuditLogPanel";
import { SessionSidebar, type Session } from "./components/SessionSidebar";
import { AppStoreModal } from "./components/AppStoreModal";


function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const API_BASE = `http://${window.location.hostname}:8000`;
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [pinnedIds, setPinnedIds] = useState<string[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isAppStoreOpen, setIsAppStoreOpen] = useState(false);
  const [fullscreenAppId, setFullscreenAppId] = useState<string | null>(null);
  const [isAuditOpen, setIsAuditOpen] = useState(false);

  const [chatWidth, setChatWidth] = useState<number>(() => {
    const saved = localStorage.getItem("chat_panel_width");
    return saved ? parseInt(saved, 10) : 320;
  });

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

    // Load pinned app IDs and fetch their source files
    const loadPinnedApps = async () => {
      const savedPinned = localStorage.getItem("pinned_widgets_global");
      const ids: string[] = savedPinned ? JSON.parse(savedPinned) : [];
      setPinnedIds(ids);

      const loadedWidgets: Widget[] = [];
      for (const id of ids) {
        try {
          const res = await fetch(`${API_BASE}/api/apps/${id}`);
          if (res.ok) {
            const data = await res.json();
            loadedWidgets.push(data);
          }
        } catch (err) {
          console.error(`Error loading pinned widget ${id}:`, err);
        }
      }
      setWidgets(loadedWidgets);
    };

    loadSessionHistory();
    loadPinnedApps();

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
        
        // Auto-pin newly created widget
        setPinnedIds((prev) => {
          const updated = prev.includes(data.widget.id) ? prev : [...prev, data.widget.id];
          localStorage.setItem("pinned_widgets_global", JSON.stringify(updated));
          return updated;
        });
      } else if (data.type === "app_data_update") {
        // Dispatch data change event to SandboxWidget listeners
        window.dispatchEvent(
          new CustomEvent(`app_data_update:${data.app_id}`, {
            detail: data.data,
          })
        );
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
    // Unpin from current session
    if (!activeSessionId) return;
    setPinnedIds((prev) => {
      const updated = prev.filter((wId) => wId !== id);
      localStorage.setItem("pinned_widgets_global", JSON.stringify(updated));
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
          localStorage.setItem("pinned_widgets_global", JSON.stringify(updated));
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
    <div className="flex w-screen h-screen overflow-hidden text-slate-100 font-sans bg-[#0c081c]">
      {/* Session History Sidebar */}
      <SessionSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={(id) => {
          setActiveSessionId(id);
          localStorage.setItem("last_active_session", id);
        }}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
        isOpen={isSidebarOpen}
        onToggleOpen={() => setIsSidebarOpen(!isSidebarOpen)}
      />

      {/* Chat Panel */}
      <ChatPanel
        messages={messages}
        onSendMessage={handleSendMessage}
        isConnected={isConnected}
        width={chatWidth}
      />

      {/* Drag Splitter Handle */}
      <div
        onMouseDown={handleMouseDown}
        className="w-1.5 h-full cursor-col-resize hover:bg-purple-500/40 active:bg-purple-600/60 transition-colors bg-white/5 shrink-0"
        title="Drag to resize panels"
      />

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
      />

      {/* Audit Log Panel Overlay */}
      <AuditLogPanel isOpen={isAuditOpen} onClose={() => setIsAuditOpen(false)} />

      {/* AppStore Floating Modal */}
      <AppStoreModal
        isOpen={isAppStoreOpen}
        onClose={() => setIsAppStoreOpen(false)}
        pinnedWidgetIds={pinnedIds}
        onPinWidget={handlePinWidget}
        onUnpinWidget={handleRemoveWidget}
        onRunFullscreen={handleRunFullscreen}
      />
    </div>
  );
}

export default App;
