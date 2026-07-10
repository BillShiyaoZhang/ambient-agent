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
      className="h-full flex flex-col glass border-r border-white/5 shrink-0 overflow-hidden"
    >
      {/* Header */}
      <div className="p-4 border-b border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold tracking-wide text-white/90">
            Ambient Chat
          </h2>
          <div className="flex items-center gap-1.5">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isConnected ? "bg-green-400 animate-pulse" : "bg-red-400"
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
            className="p-1.5 hover:bg-white/10 rounded-lg text-white/40 hover:text-white/80 transition-colors cursor-pointer"
            title="Hide Chat & History"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          </button>
        )}
      </div>

      {/* Messages list */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center text-center text-sm text-white/30 p-4">
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
                className={`max-w-[85%] rounded-2xl px-4 py-2 text-sm leading-relaxed ${
                  msg.sender === "user"
                    ? "bg-purple-600/70 text-white border border-purple-500/30"
                    : "bg-white/5 text-white/90 border border-white/5"
                }`}
              >
                {msg.content}
              </div>
              <span className="text-[10px] text-white/20 mt-1 px-1">
                {msg.sender === "user" ? "You" : "Agent"}
              </span>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Form */}
      <form onSubmit={handleSend} className="p-4 border-t border-white/5 bg-white/[0.01]">
        <div className="flex gap-2">
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            disabled={!isConnected}
            placeholder={isConnected ? "Send a message..." : "Connecting..."}
            className="flex-1 px-4 py-2 text-sm bg-white/5 border border-white/10 rounded-xl text-white placeholder-white/30 focus:outline-none focus:border-purple-500/50 focus:ring-1 focus:ring-purple-500/30 transition-all disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!isConnected || !inputText.trim()}
            className="px-4 py-2 text-sm font-medium bg-purple-600 hover:bg-purple-500 disabled:bg-purple-800 disabled:opacity-50 text-white rounded-xl transition-colors cursor-pointer"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
};
