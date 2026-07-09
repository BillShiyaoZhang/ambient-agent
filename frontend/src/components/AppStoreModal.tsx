import React, { useEffect, useState } from "react";

interface AppMetadata {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface AppStoreModalProps {
  isOpen: boolean;
  onClose: () => void;
  pinnedWidgetIds: string[];
  onPinWidget: (id: string) => void;
  onUnpinWidget: (id: string) => void;
  onRunFullscreen: (id: string) => void;
}

export const AppStoreModal: React.FC<AppStoreModalProps> = ({
  isOpen,
  onClose,
  pinnedWidgetIds,
  onPinWidget,
  onUnpinWidget,
  onRunFullscreen,
}) => {
  const [apps, setApps] = useState<AppMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const API_BASE = `http://${window.location.hostname}:8000`;

  const fetchApps = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/apps`);
      if (res.ok) {
        const data = await res.json();
        setApps(data);
      }
    } catch (err) {
      console.error("Error fetching apps from AppStore:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchApps();
    }
  }, [isOpen]);

  const handleDeleteApp = async (id: string) => {
    if (!confirm(`Are you sure you want to permanently delete the App '${id}'? This deletes all its source files and state data.`)) {
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/apps/${id}`, { method: "DELETE" });
      if (res.ok) {
        // Clean up references in all sessions' pinned widgets list in localStorage
        for (let i = 0; i < localStorage.length; i++) {
          const key = localStorage.key(i);
          if (key && key.startsWith("pinned_widgets_")) {
            try {
              const val = localStorage.getItem(key);
              if (val) {
                const pinnedIds: string[] = JSON.parse(val);
                if (pinnedIds.includes(id)) {
                  const filtered = pinnedIds.filter((pId) => pId !== id);
                  localStorage.setItem(key, JSON.stringify(filtered));
                }
              }
            } catch (e) {
              console.error(`Error parsing localStorage key ${key}:`, e);
            }
          }
        }

        onUnpinWidget(id);
        fetchApps();
      }
    } catch (err) {
      console.error("Error deleting app:", err);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md p-4 animate-fade-in">
      <div className="glass w-full max-w-4xl max-h-[85vh] rounded-3xl border border-white/10 flex flex-col overflow-hidden shadow-2xl">
        {/* Modal Header */}
        <div className="p-6 border-b border-white/5 flex justify-between items-center bg-white/[0.02]">
          <div>
            <h2 className="text-xl font-bold text-white tracking-wide">
              Ambient App Store
            </h2>
            <p className="text-xs text-white/40 mt-1">
              Browse and manage all MVC applications built dynamically by your agent.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-white/10 rounded-full text-white/40 hover:text-white/80 transition-colors cursor-pointer"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Modal Content */}
        <div className="flex-1 overflow-y-auto p-6 bg-[#0c0817]/30">
          {loading ? (
            <div className="h-48 flex items-center justify-center">
              <div className="w-8 h-8 border-4 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
            </div>
          ) : apps.length === 0 ? (
            <div className="text-center py-16 border-2 border-dashed border-white/5 rounded-2xl p-6">
              <div className="w-12 h-12 rounded-full bg-purple-500/10 flex items-center justify-center mb-4 mx-auto">
                <svg className="w-6 h-6 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 9.172V5L8 4z" />
                </svg>
              </div>
              <h3 className="text-sm font-semibold text-white/80">No apps built yet</h3>
              <p className="text-xs text-white/40 mt-1 max-w-sm mx-auto">
                Chat with the agent and ask it to build a visual utility (e.g. "Build a calculator app") to populate your store!
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {apps.map((app) => {
                const isPinned = pinnedWidgetIds.includes(app.id);
                return (
                  <div
                    key={app.id}
                    className="glass-card rounded-2xl p-5 border border-white/5 bg-white/[0.02] flex flex-col h-[180px] relative group hover:border-purple-500/30 transition-all duration-300"
                  >
                    {/* App Header */}
                    <div className="flex items-start justify-between min-w-0">
                      <div className="min-w-0">
                        <h3 className="text-base font-bold text-white/95 truncate">
                          {app.title}
                        </h3>
                        <p className="text-[10px] text-purple-400 font-semibold uppercase tracking-wider mt-0.5">
                          {app.id}
                        </p>
                      </div>
                      <div className="w-8 h-8 rounded-xl bg-purple-500/10 flex items-center justify-center shrink-0">
                        <svg className="w-4 h-4 text-purple-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                        </svg>
                      </div>
                    </div>

                    <div className="flex-1 mt-3">
                      <p className="text-[10px] text-white/30">
                        Created: {new Date(app.created_at).toLocaleDateString()}
                      </p>
                      <p className="text-[10px] text-white/30 mt-0.5">
                        Updated: {new Date(app.updated_at).toLocaleDateString()}
                      </p>
                    </div>

                    {/* App Action Buttons */}
                    <div className="flex gap-2 mt-4 pt-3 border-t border-white/5 justify-between items-center">
                      <div className="flex gap-2">
                        {isPinned ? (
                          <button
                            onClick={() => onUnpinWidget(app.id)}
                            className="px-3 py-1.5 text-xs font-semibold bg-purple-500/20 hover:bg-purple-500/30 text-purple-200 rounded-lg border border-purple-500/30 transition-colors cursor-pointer"
                            title="Unpin from Canvas"
                          >
                            Unpin
                          </button>
                        ) : (
                          <button
                            onClick={() => onPinWidget(app.id)}
                            className="px-3 py-1.5 text-xs font-semibold bg-white/5 hover:bg-white/10 text-white/80 rounded-lg border border-white/10 transition-colors cursor-pointer"
                            title="Pin to Canvas"
                          >
                            Pin
                          </button>
                        )}
                        <button
                          onClick={() => onRunFullscreen(app.id)}
                          className="px-3 py-1.5 text-xs font-semibold bg-purple-600 hover:bg-purple-500 text-white rounded-lg transition-colors cursor-pointer"
                          title="Run App Fullscreen"
                        >
                          Run
                        </button>
                      </div>
                      
                      <button
                        onClick={() => handleDeleteApp(app.id)}
                        className="p-1.5 hover:bg-red-500/10 rounded-lg text-white/20 hover:text-red-400 transition-colors cursor-pointer"
                        title="Uninstall App"
                      >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
