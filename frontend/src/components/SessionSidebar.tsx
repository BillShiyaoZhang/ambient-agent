import React from "react";

export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface SessionSidebarProps {
  sessions: Session[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (id: string) => void;
  isOpen: boolean;
  onToggleOpen: () => void;
}

export const SessionSidebar: React.FC<SessionSidebarProps> = ({
  sessions,
  activeSessionId,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  isOpen,
  onToggleOpen,
}) => {
  return (
    <div
      className={`h-full flex flex-col glass border-r border-white/5 transition-all duration-300 relative ${
        isOpen ? "w-64" : "w-16"
      }`}
    >
      {/* Sidebar Header */}
      <div className="p-4 border-b border-white/5 flex items-center justify-between overflow-hidden shrink-0">
        {isOpen ? (
          <h2 className="text-sm font-bold uppercase tracking-wider text-purple-300 whitespace-nowrap">
            Conversations
          </h2>
        ) : (
          <div className="w-8 h-8 rounded-lg bg-purple-600/30 flex items-center justify-center text-purple-300 font-bold text-xs mx-auto">
            AA
          </div>
        )}
      </div>

      {/* New Chat Button */}
      <div className="p-3 shrink-0">
        <button
          onClick={onCreateSession}
          className="w-full py-2 px-3 text-xs font-semibold bg-purple-600 hover:bg-purple-500 text-white rounded-xl flex items-center justify-center gap-1.5 transition-colors cursor-pointer"
          title="New Chat"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          {isOpen && <span className="whitespace-nowrap">New Chat</span>}
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-2 pb-4 space-y-1 min-h-0">
        {sessions.map((sess) => {
          const isActive = sess.id === activeSessionId;
          return (
            <div
              key={sess.id}
              className={`group flex items-center justify-between rounded-xl px-3 py-2.5 text-sm transition-all cursor-pointer ${
                isActive
                  ? "bg-purple-600/25 text-white border border-purple-500/20"
                  : "text-white/60 hover:text-white/90 hover:bg-white/5"
              }`}
              onClick={() => onSelectSession(sess.id)}
            >
              <div className="flex items-center gap-3 min-w-0 flex-1">
                {/* Chat bubble icon */}
                <svg className="w-4 h-4 text-purple-400/80 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                {isOpen && (
                  <span className="truncate pr-2 font-medium" title={sess.title}>
                    {sess.title}
                  </span>
                )}
              </div>
              
              {/* Delete button (only visible on hover and if sidebar is open) */}
              {isOpen && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(sess.id);
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 hover:bg-white/10 rounded text-white/30 hover:text-red-400 transition-all cursor-pointer"
                  title="Delete Chat"
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Collapse/Expand Toggle Button at the bottom */}
      <div className="p-3 border-t border-white/5 flex justify-center">
        <button
          onClick={onToggleOpen}
          className="p-2 hover:bg-white/5 rounded-xl text-white/40 hover:text-white/80 transition-colors cursor-pointer"
          title={isOpen ? "Collapse Sidebar" : "Expand Sidebar"}
        >
          {isOpen ? (
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
            </svg>
          ) : (
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
            </svg>
          )}
        </button>
      </div>
    </div>
  );
};
