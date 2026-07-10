import React, { useState, useRef, useEffect } from "react";

export interface Message {
  id?: number;
  sender: "user" | "agent";
  content: string;
  timestamp?: string;
}

interface ChatPanelProps {
  messages: Message[];
  onSendMessage: (text: string) => void;
  isConnected: boolean;
  width?: number;
  onHideChat?: () => void;
}

export const ChatPanel: React.FC<ChatPanelProps> = ({
  messages,
  onSendMessage,
  isConnected,
  width = 320,
  onHideChat,
}) => {
  const [inputText, setInputText] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    onSendMessage(inputText);
    setInputText("");
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div
      style={{ width: `${width}px` }}
      className="h-full flex flex-col glass border-r border-white/[0.06] shrink-0 overflow-hidden"
    >
      {/* Header */}
      <div className="p-4 border-b border-white/[0.06] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-tight text-white/90">
            Ambient Chat
          </h2>
          <div className="flex items-center gap-1.5">
            <span
              className={`w-1.5 h-1.5 rounded-full transition-all duration-300 ${
                isConnected
                  ? "bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.6)] animate-pulse"
                  : "bg-red-400"
              }`}
            />
            <span className="text-[10px] text-white/40">
              {isConnected ? "Connected" : "Offline"}
            </span>
          </div>
        </div>
        
        {onHideChat && (
          <button
            onClick={onHideChat}
            className="p-1 hover:bg-white/5 text-white/40 hover:text-white/80 rounded-lg transition-colors cursor-pointer"
            title="Hide Chat & History"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
        )}
      </div>

      {/* Messages list */}
      <div className="flex-1 overflow-y-auto p-3.5 space-y-3">
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center text-center text-xs text-white/30 p-4">
            Start talking to your Ambient Agent. The agent can spawn custom widgets on your canvas workspace.
          </div>
        ) : (
          messages.map((msg, index) => (
            <div
              key={msg.id || index}
              className={`flex flex-col ${
                msg.sender === "user" ? "items-end" : "items-start"
              }`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-1.5 text-xs leading-relaxed ${
                  msg.sender === "user"
                    ? "bg-[#0f141c] text-white border border-cyan-500/20 shadow-sm"
                    : "bg-white/[0.02] text-white/90 border border-white/[0.06]"
                }`}
              >
                {msg.content}
              </div>
              <span className="text-[9px] text-white/20 mt-0.5 px-0.5">
                {msg.sender === "user" ? "You" : "Agent"}
              </span>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Form */}
      <form onSubmit={handleSend} className="p-3 border-t border-white/[0.06] bg-black/[0.05]">
        <div className="flex gap-2">
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            disabled={!isConnected}
            placeholder={isConnected ? "Send a message..." : "Connecting..."}
            className="flex-1 px-3 py-1.5 text-xs bg-white/[0.02] border border-white/10 rounded-lg text-white placeholder-white/30 focus:outline-none focus:border-cyan-500/40 focus:ring-1 focus:ring-cyan-500/10 transition-all disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!isConnected || !inputText.trim()}
            className="px-3 py-1.5 text-xs font-semibold bg-white/[0.04] hover:bg-white/[0.08] disabled:bg-white/[0.01] disabled:opacity-30 disabled:border-white/5 disabled:hover:shadow-none text-white rounded-lg border border-white/10 hover:border-cyan-500/30 hover:shadow-[0_0_8px_rgba(6,182,212,0.1)] transition-all cursor-pointer"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
};
