import React, { useEffect, useMemo, useRef, useState } from "react";
import { History, LoaderCircle, Maximize2, MessageCircle, Plus, Send, Trash2, X } from "lucide-react";
import type { Message } from "./ChatPanel";
import type { Session } from "./SessionSidebar";
import type { LLMProvider, ModelSelection } from "../services/llm";
import type { AgentModelConfig, CodingAgentDefinition } from "../services/codingAgents";
import {
  liveStreamsForRun,
  type ChatRunCard as ChatRunCardModel,
  type LiveStreamState,
} from "../lib/chatProjection";
import {
  CHAT_SIZE_PRESETS,
  clampChatSize,
  loadChatSize,
  normalizeChatSize,
  saveChatSize,
  type ChatSize,
  type ChatSizePreset,
} from "../lib/chatLayout";
import { ChatRunCard } from "./ChatRunCard";
import { ModelPicker } from "./LLMSettings";
import { SystemIconButton, SystemPopover } from "./system/SystemUI";
import "./Workspace.css";

interface AgentChatOverlayProps {
  open: boolean;
  unreadCount: number;
  messages: Message[];
  runCards?: ChatRunCardModel[];
  liveStreams?: Record<string, LiveStreamState>;
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
  onCancelRun?: (runId: string) => void;
  providers?: LLMProvider[];
  modelSelection?: ModelSelection | null;
  onModelChange?: (selection: ModelSelection) => void;
  onManageModels?: () => void;
  codingAgent?: CodingAgentDefinition;
  codingAgentModel?: AgentModelConfig;
}

type ConversationItem =
  | { kind: "message"; key: string; sortTime: number; ordinal: number; message: Message }
  | { kind: "run"; key: string; sortTime: number; ordinal: number; run: ChatRunCardModel };

function timeValue(value: string | undefined, fallback: number): number {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

export const AgentChatOverlay: React.FC<AgentChatOverlayProps> = ({
  open, unreadCount, messages, runCards = [], liveStreams = {}, sessions, activeSessionId, runningSessions, isConnected, language,
  onOpenChange, onSendMessage, onSelectSession, onCreateSession, onDeleteSession, onCancelRun,
  providers = [], modelSelection = null, onModelChange, onManageModels, codingAgent, codingAgentModel,
}) => {
  const isZh = language === "zh";
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sizeMenuOpen, setSizeMenuOpen] = useState(false);
  const [input, setInput] = useState("");
  const initialSizeRef = useRef<ChatSize | null>(null);
  if (!initialSizeRef.current) initialSizeRef.current = loadChatSize();
  const preferredSizeRef = useRef<ChatSize>(initialSizeRef.current);
  const [chatSize, setChatSize] = useState<ChatSize>(() => clampChatSize(preferredSizeRef.current));
  const [newProgress, setNewProgress] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const autoFollowRef = useRef(true);
  const historyTriggerRef = useRef<HTMLButtonElement>(null);
  const sizeTriggerRef = useRef<HTMLButtonElement>(null);
  const sizeRef = useRef(chatSize);
  sizeRef.current = chatSize;

  const visibleMessages = useMemo(() => messages.filter((message) => message.id !== -1), [messages]);
  const conversationItems = useMemo<ConversationItem[]>(() => {
    const messageItems = visibleMessages.map((message, index): ConversationItem => ({
      kind: "message",
      key: `${message.id ?? "local"}:${message.sender}:${message.timestamp ?? ""}:${index}`,
      sortTime: timeValue(message.timestamp, index),
      ordinal: index,
      message,
    }));
    const runItems = runCards.map((run, index): ConversationItem => ({
      kind: "run",
      key: `run:${run.id}`,
      sortTime: timeValue(run.createdAt, visibleMessages.length + index),
      ordinal: visibleMessages.length + index,
      run,
    }));
    return [...messageItems, ...runItems].sort((left, right) => (
      left.sortTime - right.sortTime || left.ordinal - right.ordinal
    ));
  }, [runCards, visibleMessages]);
  const liveStreamsByRun = useMemo(() => Object.fromEntries(
    runCards.map((run) => [run.id, liveStreamsForRun(liveStreams, run.id)])
  ), [liveStreams, runCards]);
  const streamRevision = `${visibleMessages.length}:${runCards.map((run) => `${run.id}:${run.updatedAt}:${run.status}`).join("|")}:${Object.values(liveStreams).map((stream) => `${stream.streamId}:${stream.lastSequence}`).join("|")}`;

  const scrollToLatest = (behavior: ScrollBehavior = "smooth") => {
    autoFollowRef.current = true;
    setNewProgress(false);
    endRef.current?.scrollIntoView?.({ behavior });
  };

  useEffect(() => {
    if (!open) return;
    if (autoFollowRef.current) scrollToLatest("auto");
    else setNewProgress(true);
    // streamRevision intentionally captures incremental progress without
    // depending on every object identity in the projection.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [open, streamRevision]);

  useEffect(() => {
    const handleResize = () => setChatSize(clampChatSize(preferredSizeRef.current));
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const handleMessagesScroll = () => {
    const element = messagesRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    autoFollowRef.current = distance <= 72;
    if (autoFollowRef.current) setNewProgress(false);
  };

  const applySize = (size: ChatSize) => {
    const preferred = normalizeChatSize(size);
    preferredSizeRef.current = preferred;
    setChatSize(clampChatSize(preferred));
    saveChatSize(preferred);
    setSizeMenuOpen(false);
  };

  const applyPreset = (preset: ChatSizePreset) => applySize(CHAT_SIZE_PRESETS[preset]);

  const startResize = (axes: "x" | "y" | "xy", event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const start = sizeRef.current;
    const handle = event.currentTarget;
    handle.setPointerCapture?.(event.pointerId);
    let frame = 0;
    let latest = start;
    const handleMove = (moveEvent: PointerEvent) => {
      latest = clampChatSize({
        width: axes.includes("x") ? start.width + startX - moveEvent.clientX : start.width,
        height: axes.includes("y") ? start.height + startY - moveEvent.clientY : start.height,
      });
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setChatSize(latest));
    };
    const handleUp = (upEvent: PointerEvent) => {
      cancelAnimationFrame(frame);
      preferredSizeRef.current = latest;
      setChatSize(latest);
      saveChatSize(latest);
      if (handle.hasPointerCapture?.(upEvent.pointerId)) handle.releasePointerCapture(upEvent.pointerId);
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  };

  const resizeWithKeyboard = (axes: "x" | "y" | "xy", event: React.KeyboardEvent<HTMLButtonElement>) => {
    const delta = event.shiftKey ? 48 : 16;
    let width = chatSize.width;
    let height = chatSize.height;
    if (axes.includes("x") && event.key === "ArrowLeft") width += delta;
    else if (axes.includes("x") && event.key === "ArrowRight") width -= delta;
    else if (axes.includes("y") && event.key === "ArrowUp") height += delta;
    else if (axes.includes("y") && event.key === "ArrowDown") height -= delta;
    else return;
    event.preventDefault();
    applySize({ width, height });
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!input.trim()) return;
    onSendMessage(input.trim());
    setInput("");
    scrollToLatest("smooth");
  };
  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const anyRunning = runningSessions.length > 0 || runCards.some((run) => ["queued", "running", "waiting_user", "cancel_requested"].includes(run.status));
  const codingModelLabel = codingAgent?.id === "codex"
    ? (codingAgentModel?.native_model || (isZh ? "Agent 默认" : "Agent default"))
    : codingAgentModel?.inherit
      ? (isZh ? "跟随 Ambient" : "Follows Ambient")
      : codingAgentModel?.model_id;

  return (
    <>
      {open && <aside
        className="agent-chat-panel"
        aria-label={isZh ? "智能助手" : "Agent chat"}
        style={{ width: chatSize.width, height: chatSize.height }}
      >
        <button className="agent-chat-resize is-left" type="button" aria-label={isZh ? "调整聊天宽度" : "Resize chat width"} onPointerDown={(event) => startResize("x", event)} onKeyDown={(event) => resizeWithKeyboard("x", event)} />
        <button className="agent-chat-resize is-top" type="button" aria-label={isZh ? "调整聊天高度" : "Resize chat height"} onPointerDown={(event) => startResize("y", event)} onKeyDown={(event) => resizeWithKeyboard("y", event)} />
        <button className="agent-chat-resize is-corner" type="button" aria-label={isZh ? "调整聊天窗口大小" : "Resize chat window"} onPointerDown={(event) => startResize("xy", event)} onKeyDown={(event) => resizeWithKeyboard("xy", event)} />
        <header className="agent-chat-header">
          <div className="agent-chat-identity">
            <span className={`agent-status-dot ${isConnected ? "is-online" : ""}`} />
            <div><strong>{activeSession?.title ?? (isZh ? "新对话" : "New conversation")}</strong><span>{isConnected ? (isZh ? "Ambient 已连接" : "Ambient connected") : (isZh ? "连接中…" : "Connecting…")}</span></div>
          </div>
          <div className="agent-chat-actions">
            <div className="workspace-menu-anchor">
              <SystemIconButton ref={sizeTriggerRef} className="agent-chat-size-trigger" label={isZh ? "聊天窗口大小" : "Chat window size"} onClick={() => setSizeMenuOpen((value) => !value)} aria-expanded={sizeMenuOpen}><Maximize2 size={16} /></SystemIconButton>
              <SystemPopover open={sizeMenuOpen} onClose={() => setSizeMenuOpen(false)} triggerRef={sizeTriggerRef} label={isZh ? "窗口大小" : "Window size"} className="chat-size-popover">
                <button type="button" onClick={() => applyPreset("compact")}><span>{isZh ? "紧凑" : "Compact"}</span><small>380 × 520</small></button>
                <button type="button" onClick={() => applyPreset("default")}><span>{isZh ? "默认" : "Default"}</span><small>432 × 600</small></button>
                <button type="button" onClick={() => applyPreset("wide")}><span>{isZh ? "宽屏" : "Wide"}</span><small>620 × 720</small></button>
                <button type="button" onClick={() => applyPreset("default")}><span>{isZh ? "恢复默认大小" : "Reset size"}</span></button>
              </SystemPopover>
            </div>
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
        <div ref={messagesRef} className="agent-chat-messages" onScroll={handleMessagesScroll}>
          {conversationItems.length === 0 ? <div className="agent-chat-empty"><span><MessageCircle size={22} /></span><strong>{isZh ? "需要我做什么？" : "What can I help with?"}</strong><p>{isZh ? "我可以创建 App、整理信息，或协助你操作当前工作区。" : "I can create apps, organize information, or help with your workspace."}</p></div> : conversationItems.map((item) => (
            item.kind === "message"
              ? <div key={item.key} className={`agent-message ${item.message.sender === "user" ? "is-user" : "is-agent"}`}><div>{item.message.content}</div><span>{item.message.sender === "user" ? (isZh ? "你" : "You") : "Ambient"}</span></div>
              : <ChatRunCard key={item.key} run={item.run} language={language} onCancel={onCancelRun} liveStreams={liveStreamsByRun[item.run.id]} />
          ))}
          <div ref={endRef} />
        </div>
        {newProgress ? <button type="button" className="agent-chat-new-progress" onClick={() => scrollToLatest()}>
          <LoaderCircle size={13} />{isZh ? "查看最新进度" : "View latest progress"}
        </button> : null}
        <form className="agent-chat-composer" onSubmit={submit}>
          <div className="agent-chat-model-row">
            <div className="agent-chat-model-choice"><span>Ambient</span><ModelPicker providers={providers} value={modelSelection} onChange={(selection) => onModelChange?.(selection)} onManage={onManageModels} language={language} disabled={!onModelChange} /></div>
            {codingAgent ? <span className="agent-chat-coding-model">{isZh ? "代码" : "Code"} · {codingAgent.name}{codingModelLabel ? ` · ${codingModelLabel}` : ""}</span> : null}
            {runningSessions.includes(activeSessionId ?? "") ? <span>{isZh ? "切换将从下次请求生效" : "Changes apply to the next request"}</span> : null}
          </div>
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
