import React, { useEffect, useMemo, useRef, useState } from "react";
import { History, LoaderCircle, Maximize2, MessageCircle, Plus, Send, Trash2, X } from "lucide-react";
import type { Message } from "./ChatPanel";
import { externalSkillMessageLabel } from "./chatMessageProvenance";
import type { Session } from "./SessionSidebar";
import type { LLMProvider, ModelSelection } from "../services/llm";
import type { AgentModelConfig, CodingAgentDefinition } from "../services/codingAgents";
import type { SocketConnectionState } from "../services/socketReconnect";
import {
  interactionsForRun,
  liveStreamsForRun,
  type ChatRunCard as ChatRunCardModel,
  type LiveStreamState,
  type RunInteractionState,
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
import { ChatRunCard, type RunInteractionAction } from "./ChatRunCard";
import { ModelPicker } from "./LLMSettings";
import { SlashCommandInput } from "./SlashCommandInput";
import { SystemIconButton, SystemPopover } from "./system/SystemUI";
import type { SlashCommandCatalog } from "../services/slashCommands";
import "./Workspace.css";

interface AgentChatOverlayProps {
  open: boolean;
  unreadCount: number;
  messages: Message[];
  runCards?: ChatRunCardModel[];
  liveStreams?: Record<string, LiveStreamState>;
  interactions?: Record<string, RunInteractionState>;
  sessions: Session[];
  activeSessionId: string | null;
  runningSessions: string[];
  isConnected: boolean;
  connectionState?: SocketConnectionState;
  deliveryError?: string | null;
  sessionError?: string | null;
  language: "zh" | "en";
  onOpenChange: (open: boolean) => void;
  onSendMessage: (text: string) => boolean | void;
  onRetryConnection?: () => void;
  onSelectSession: (id: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (id: string) => void;
  onCancelRun?: (runId: string) => void;
  onResolveRunInteraction?: (interaction: RunInteractionState, action: RunInteractionAction) => void;
  onInspectRunInteraction?: (interaction: RunInteractionState) => void;
  providers?: LLMProvider[];
  modelSelection?: ModelSelection | null;
  onModelChange?: (selection: ModelSelection) => void;
  onManageModels?: () => void;
  codingAgent?: CodingAgentDefinition;
  codingAgentModel?: AgentModelConfig;
  apiBase?: string;
  slashCommandCatalog?: SlashCommandCatalog;
}

type ConversationItem =
  | { kind: "message"; key: string; sortTime: number; ordinal: number; message: Message }
  | { kind: "run"; key: string; sortTime: number; ordinal: number; run: ChatRunCardModel };

function timeValue(value: string | undefined, fallback: number): number {
  const parsed = value ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
}

export const AgentChatOverlay: React.FC<AgentChatOverlayProps> = ({
  open, unreadCount, messages, runCards = [], liveStreams = {}, interactions = {}, sessions, activeSessionId, runningSessions, isConnected, language,
  connectionState, deliveryError, sessionError, onOpenChange, onSendMessage, onRetryConnection, onSelectSession, onCreateSession, onDeleteSession, onCancelRun, onResolveRunInteraction, onInspectRunInteraction,
  providers = [], modelSelection = null, onModelChange, onManageModels, codingAgent, codingAgentModel, apiBase, slashCommandCatalog,
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
  const interactionsByRun = useMemo(() => Object.fromEntries(
    runCards.map((run) => [run.id, interactionsForRun(interactions, run.id)])
  ), [interactions, runCards]);
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

  const submitInput = () => {
    if (!input.trim()) return;
    if (onSendMessage(input.trim()) === false) return;
    setInput("");
    scrollToLatest("smooth");
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    submitInput();
  };
  const activeSession = sessions.find((session) => session.id === activeSessionId);
  const anyRunning = runningSessions.length > 0 || runCards.some((run) => ["queued", "running", "waiting_user", "cancel_requested"].includes(run.status));
  const effectiveConnectionState = connectionState ?? (isConnected ? "connected" : "connecting");
  const connectionLabels: Record<SocketConnectionState, string> = {
    connected: isZh ? "Ambient 已连接" : "Ambient connected",
    connecting: isZh ? "正在连接…" : "Connecting…",
    retrying: isZh ? "正在重新连接…" : "Reconnecting…",
    unavailable: isZh ? "连接不可用" : "Connection unavailable",
    disconnected: isZh ? "连接已断开" : "Disconnected",
  };
  const connectionNotice = deliveryError
    ?? (["unavailable", "disconnected"].includes(effectiveConnectionState)
      ? (isZh
          ? "响应与新消息会保留在本机，连接恢复后可重试。"
          : "Responses and new messages stay on this device until the connection returns.")
      : null);
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
            <div><strong>{activeSession?.title ?? (isZh ? "新对话" : "New conversation")}</strong><span>{connectionLabels[effectiveConnectionState]}</span></div>
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
                {sessionError && <p className="chat-history-error" role="alert">{sessionError}</p>}
                <div className="chat-history-list">
                  {sessions.map((session) => {
                    const hasActiveTasks = runningSessions.includes(session.id);
                    const deleteLabel = hasActiveTasks
                      ? (isZh
                          ? `请先完成或取消活动任务，再删除 ${session.title}`
                          : `Finish or cancel active tasks before deleting ${session.title}`)
                      : (isZh ? `删除 ${session.title}` : `Delete ${session.title}`);
                    return <div key={session.id} className={`chat-history-item ${session.id === activeSessionId ? "is-active" : ""}`}>
                      <button className="chat-history-select" onClick={() => { onSelectSession(session.id); setHistoryOpen(false); }}>
                        {hasActiveTasks ? <LoaderCircle className="is-spinning" size={14} /> : <MessageCircle size={14} />}
                        <span><strong>{session.title}</strong><small>{session.updated_at ? new Date(session.updated_at).toLocaleDateString(language) : ""}</small></span>
                      </button>
                      <SystemIconButton className="chat-history-delete" label={deleteLabel} tone="danger" disabled={hasActiveTasks} onClick={() => onDeleteSession(session.id)}><Trash2 size={13} /></SystemIconButton>
                    </div>;
                  })}
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
              ? (() => {
                  const externalSkillLabel = externalSkillMessageLabel(item.message, isZh);
                  return <div key={item.key} className={`agent-message ${item.message.sender === "user" ? "is-user" : "is-agent"} ${externalSkillLabel ? "is-external-skill" : ""}`}><div>{item.message.content}</div><span>{externalSkillLabel ?? (item.message.sender === "user" ? (isZh ? "你" : "You") : "Ambient")}</span></div>;
                })()
              : <ChatRunCard
                  key={item.key}
                  run={item.run}
                  language={language}
                  onCancel={onCancelRun}
                  liveStreams={liveStreamsByRun[item.run.id]}
                  interactions={interactionsByRun[item.run.id]}
                  onResolveInteraction={onResolveRunInteraction}
                  onInspectInteraction={onInspectRunInteraction}
                />
          ))}
          <div ref={endRef} />
        </div>
        {newProgress ? <button type="button" className="agent-chat-new-progress" onClick={() => scrollToLatest()}>
          <LoaderCircle size={13} />{isZh ? "查看最新进度" : "View latest progress"}
        </button> : null}
        {connectionNotice ? (
          <div
            className="agent-chat-connection-notice"
            role={deliveryError ? "alert" : "status"}
          >
            <span>{connectionNotice}</span>
            {onRetryConnection && effectiveConnectionState !== "connected" ? (
              <button type="button" onClick={onRetryConnection}>
                {isZh ? "重试连接" : "Retry connection"}
              </button>
            ) : null}
          </div>
        ) : null}
        <form className="agent-chat-composer" onSubmit={submit}>
          <div className="agent-chat-model-row">
            <div className="agent-chat-model-choice"><span>Ambient</span><ModelPicker providers={providers} value={modelSelection} onChange={(selection) => onModelChange?.(selection)} onManage={onManageModels} language={language} disabled={!onModelChange} /></div>
            {codingAgent ? <span className="agent-chat-coding-model">{isZh ? "代码" : "Code"} · {codingAgent.name}{codingModelLabel ? ` · ${codingModelLabel}` : ""}</span> : null}
            {runningSessions.includes(activeSessionId ?? "") ? <span>{isZh ? "切换将从下次请求生效" : "Changes apply to the next request"}</span> : null}
          </div>
          <SlashCommandInput
            autoFocus
            value={input}
            onChange={setInput}
            onSubmit={submitInput}
            placeholder={isZh ? "向 Ambient 发送消息… 输入 / 查看命令" : "Message Ambient… Type / for commands"}
            language={language}
            apiBase={apiBase}
            catalog={slashCommandCatalog}
          />
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
