import React, { useEffect, useRef, useState } from "react";
import { History, LoaderCircle, MessageCircle, Plus, Send, Trash2, X } from "lucide-react";
import type { Message } from "./ChatPanel";
import type { Session } from "./SessionSidebar";
import { SystemIconButton, SystemPopover } from "./system/SystemUI";
import "./Workspace.css";

interface AgentChatOverlayProps {
  open: boolean;
  unreadCount: number;
  messages: Message[];
  sessions: Session[];
  activeSessionId: string | null;
  runningSessions: string[];
  isConnected: boolean;
  language: "zh" | "en";
  onOpenChange: (open: boolean) => void;
  onSendMessage: (text: string) => void;
  onSelectSession: (id: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (id: string) => void;
}

export const AgentChatOverlay: React.FC<AgentChatOverlayProps> = ({
  open, unreadCount, messages, sessions, activeSessionId, runningSessions, isConnected, language,
  onOpenChange, onSendMessage, onSelectSession, onCreateSession, onDeleteSession,
}) => {
  const isZh = language === "zh";
  const [historyOpen, setHistoryOpen] = useState(false);
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const historyTriggerRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (open) endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, open]);
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!input.trim()) return;
    onSendMessage(input.trim());
    setInput("");
  };
  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const anyRunning = runningSessions.length > 0;

  return (
    <>
      {open && <aside className="agent-chat-panel" aria-label={isZh ? "智能助手" : "Agent chat"}>
        <header className="agent-chat-header">
          <div className="agent-chat-identity">
            <span className={`agent-status-dot ${isConnected ? "is-online" : ""}`} />
            <div><strong>{activeSession?.title ?? (isZh ? "新对话" : "New conversation")}</strong><span>{isConnected ? (isZh ? "Ambient 已连接" : "Ambient connected") : (isZh ? "连接中…" : "Connecting…")}</span></div>
          </div>
          <div className="agent-chat-actions">
            <div className="workspace-menu-anchor">
              <SystemIconButton ref={historyTriggerRef} label={isZh ? "聊天历史" : "Chat history"} onClick={() => setHistoryOpen((value) => !value)} aria-expanded={historyOpen}><History size={17} /></SystemIconButton>
              <SystemPopover open={historyOpen} onClose={() => setHistoryOpen(false)} triggerRef={historyTriggerRef} label={isZh ? "聊天记录" : "Conversations"} className="chat-history-popover">
                <div className="chat-history-heading"><span>{isZh ? "聊天记录" : "Conversations"}</span><button onClick={() => { onCreateSession(); setHistoryOpen(false); }}><Plus size={15} />{isZh ? "新建" : "New"}</button></div>
                <div className="chat-history-list">
                  {sessions.map((session) => <div key={session.id} className={`chat-history-item ${session.id === activeSessionId ? "is-active" : ""}`}>
                    <button className="chat-history-select" onClick={() => { onSelectSession(session.id); setHistoryOpen(false); }}>
                      {runningSessions.includes(session.id) ? <LoaderCircle className="is-spinning" size={14} /> : <MessageCircle size={14} />}
                      <span><strong>{session.title}</strong><small>{session.updated_at ? new Date(session.updated_at).toLocaleDateString(language) : ""}</small></span>
                    </button>
                    <SystemIconButton className="chat-history-delete" label={isZh ? `删除 ${session.title}` : `Delete ${session.title}`} tone="danger" onClick={() => onDeleteSession(session.id)}><Trash2 size={13} /></SystemIconButton>
                  </div>)}
                </div>
              </SystemPopover>
            </div>
            <SystemIconButton onClick={onCreateSession} label={isZh ? "新建对话" : "New conversation"}><Plus size={17} /></SystemIconButton>
            <SystemIconButton onClick={() => onOpenChange(false)} label={isZh ? "关闭聊天" : "Close chat"}><X size={17} /></SystemIconButton>
          </div>
        </header>
        <div className="agent-chat-messages">
          {messages.length === 0 ? <div className="agent-chat-empty"><span><MessageCircle size={22} /></span><strong>{isZh ? "需要我做什么？" : "What can I help with?"}</strong><p>{isZh ? "我可以创建 App、整理信息，或协助你操作当前工作区。" : "I can create apps, organize information, or help with your workspace."}</p></div> : messages.map((message, index) => <div key={message.id ?? index} className={`agent-message ${message.sender === "user" ? "is-user" : "is-agent"}`}><div>{message.content}</div><span>{message.sender === "user" ? (isZh ? "你" : "You") : "Ambient"}</span></div>)}
          <div ref={endRef} />
        </div>
        <form className="agent-chat-composer" onSubmit={submit}>
          <textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={isZh ? "向 Ambient 发送消息…" : "Message Ambient…"} rows={1} />
          <SystemIconButton className="agent-chat-send" type="submit" disabled={!input.trim() || !isConnected} label={isZh ? "发送" : "Send"} tone="accent"><Send size={16} /></SystemIconButton>
        </form>
      </aside>}
      <button className={`agent-chat-fab ${open ? "is-open" : ""}`} onClick={() => onOpenChange(!open)} aria-label={open ? (isZh ? "关闭聊天" : "Close chat") : (isZh ? "打开聊天" : "Open chat")}>
        {anyRunning && !open ? <LoaderCircle className="is-spinning" size={21} /> : open ? <X size={21} /> : <MessageCircle size={21} />}
        {!open && unreadCount > 0 && <span className="agent-chat-unread">{Math.min(unreadCount, 9)}</span>}
      </button>
    </>
  );
};
