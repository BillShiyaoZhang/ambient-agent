import React, { useEffect, useState } from "react";
import { getTranslation } from "../services/i18n";

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
  language?: "zh" | "en";
}

export const AppStoreModal: React.FC<AppStoreModalProps> = ({
  isOpen,
  onClose,
  pinnedWidgetIds,
  onPinWidget,
  onUnpinWidget,
  onRunFullscreen,
  language = "zh",
}) => {
  const [apps, setApps] = useState<AppMetadata[]>([]);
  const [loading, setLoading] = useState(false);
  const API_BASE = `http://${window.location.hostname}:8000`;
  const isZh = language === "zh";

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
    const confirmMsg = isZh
      ? `您确定要永久删除应用 '${id}' 吗？这将会删除其所有源文件和状态数据。`
      : `Are you sure you want to permanently delete the App '${id}'? This deletes all its source files and state data.`;
    if (!confirm(confirmMsg)) {
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
      <div className="glass w-full max-w-4xl max-h-[85vh] rounded-xl border border-white/10 flex flex-col overflow-hidden shadow-2xl">
        {/* Modal Header */}
        <div className="p-4 border-b border-white/[0.06] flex justify-between items-center bg-white/[0.01]">
          <div>
            <h2 className="text-sm font-semibold text-white tracking-wide">
              {getTranslation("appStoreTitle", language)}
            </h2>
            <p className="text-[10px] text-white/40 mt-0.5">
              {isZh
                ? "浏览并管理由您的智能体动态构建的所有 MVC 应用。"
                : "Browse and manage all MVC applications built dynamically by your agent."}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-white/5 rounded-lg text-white/40 hover:text-white/80 transition-colors cursor-pointer"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Modal Content */}
        <div className="flex-1 overflow-y-auto p-5 bg-[#08080a]/30">
          {loading ? (
            <div className="h-48 flex items-center justify-center">
              <div className="w-6 h-6 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin"></div>
            </div>
          ) : apps.length === 0 ? (
            <div className="text-center py-16 border border-dashed border-white/10 rounded-xl p-5 bg-white/[0.005]">
              <div className="w-10 h-10 rounded-full bg-cyan-500/10 flex items-center justify-center mb-3 mx-auto">
                <svg className="w-5 h-5 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 9.172V5L8 4z" />
                </svg>
              </div>
              <h3 className="text-sm font-semibold text-white/80">
                {isZh ? "暂无已构建的应用" : "No apps built yet"}
              </h3>
              <p className="text-xs text-white/40 mt-1 max-w-sm mx-auto">
                {isZh
                  ? "与智能体交谈并让它构建可视化工具（例如：“制作一个计算器组件”）来充实您的商店！"
                  : "Chat with the agent and ask it to build a visual utility (e.g. \"Build a calculator app\") to populate your store!"}
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {apps.map((app) => {
                const isPinned = pinnedWidgetIds.includes(app.id);
                return (
                  <div
                    key={app.id}
                    className="glass-card rounded-lg p-4 border border-white/[0.06] bg-white/[0.015] flex flex-col h-[160px] relative group hover:border-cyan-500/30 transition-all duration-350"
                  >
                    {/* App Header */}
                    <div className="flex items-start justify-between min-w-0">
                      <div className="min-w-0">
                        <h3 className="text-xs font-semibold text-white/95 truncate">
                          {app.title}
                        </h3>
                        <p className="text-[9px] text-cyan-400 font-medium uppercase tracking-wider mt-0.5">
                          {app.id}
                        </p>
                      </div>
                      <div className="w-7 h-7 rounded-lg bg-cyan-500/10 flex items-center justify-center shrink-0">
                        <svg className="w-3.5 h-3.5 text-cyan-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                        </svg>
                      </div>
                    </div>

                    <div className="flex-1 mt-3">
                      <p className="text-[10px] text-white/30">
                        {isZh ? "创建于: " : "Created: "}{new Date(app.created_at).toLocaleDateString()}
                      </p>
                      <p className="text-[10px] text-white/30 mt-0.5">
                        {isZh ? "更新于: " : "Updated: "}{new Date(app.updated_at).toLocaleDateString()}
                      </p>
                    </div>

                    {/* App Action Buttons */}
                    <div className="flex gap-2 mt-auto pt-2 border-t border-white/5 justify-between items-center">
                      <div className="flex gap-1.5">
                        {isPinned ? (
                          <button
                            onClick={() => onUnpinWidget(app.id)}
                            className="px-2.5 py-1 text-[10px] font-semibold bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-300 rounded border border-cyan-500/20 transition-all cursor-pointer shadow-sm"
                            title={isZh ? "从画布上取消固定" : "Unpin from Canvas"}
                          >
                            {isZh ? "取消固定" : "Unpin"}
                          </button>
                        ) : (
                          <button
                            onClick={() => onPinWidget(app.id)}
                            className="px-2.5 py-1 text-[10px] font-semibold bg-white/[0.02] hover:bg-white/[0.06] text-white/80 rounded border border-white/10 transition-all cursor-pointer shadow-sm"
                            title={isZh ? "固定到画布" : "Pin to Canvas"}
                          >
                            {isZh ? "固定" : "Pin"}
                          </button>
                        )}
                        <button
                          onClick={() => onRunFullscreen(app.id)}
                          className="px-2.5 py-1 text-[10px] font-semibold bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 text-white rounded border border-white/10 transition-all shadow-sm shadow-cyan-600/10 cursor-pointer"
                          title={isZh ? "全屏运行应用" : "Run App Fullscreen"}
                        >
                          {isZh ? "运行" : "Run"}
                        </button>
                      </div>
                      
                      <button
                        onClick={() => handleDeleteApp(app.id)}
                        className="p-1.5 hover:bg-red-500/10 rounded-lg text-white/20 hover:text-red-400 transition-colors cursor-pointer"
                        title={isZh ? "卸载应用" : "Uninstall App"}
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
