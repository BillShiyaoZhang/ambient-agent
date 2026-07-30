import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileClock, Network, RotateCw, X } from "lucide-react";
import { dataMapToGraph } from "../lib/graphScenes";
import { apiUrl } from "../services/apiBase";
import { DeferredGraphExplorer } from "./graph/DeferredGraph";
import { SystemDrawer, SystemIconButton } from "./system/SystemUI";
import "./AuditLogPanel.css";

export interface AuditLogEntry {
  id: number;
  timestamp: string;
  provider: string;
  model: string;
  prompt: string;
  response: string;
}

interface AuditLogPanelProps {
  isOpen: boolean;
  language: "zh" | "en";
  onClose: () => void;
}

type AuditRequestError = { status?: number; detail?: string };

export const AuditLogPanel: React.FC<AuditLogPanelProps> = ({ isOpen, language, onClose }) => {
  const isZh = language === "zh";
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [logError, setLogError] = useState<AuditRequestError | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [view, setView] = useState<"log" | "map">("log");
  const [dataMapPayload, setDataMapPayload] = useState<Record<string, unknown> | null>(null);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapError, setMapError] = useState<AuditRequestError | null>(null);
  const [mapRequested, setMapRequested] = useState(false);
  const mapRequestGeneration = useRef(0);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setLogError(null);
    try {
      const response = await fetch(apiUrl("/api/audit-logs"));
      if (!response.ok) {
        setLogError({ status: response.status });
        return;
      }
      const data = await response.json();
      setLogs(data);
    } catch (error) {
      console.error("Error fetching audit logs:", error);
      setLogError({
        detail: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchDataMap = useCallback(async () => {
    const generation = ++mapRequestGeneration.current;
    setMapRequested(true);
    setMapLoading(true);
    setMapError(null);
    try {
      const response = await fetch(apiUrl("/api/data-map"));
      if (!response.ok) {
        if (generation === mapRequestGeneration.current) {
          setMapError({ status: response.status });
        }
        return;
      }
      const nextDataMap = await response.json() as Record<string, unknown>;
      if (generation === mapRequestGeneration.current) setDataMapPayload(nextDataMap);
    } catch (error) {
      if (generation === mapRequestGeneration.current) {
        setMapError({ detail: error instanceof Error ? error.message : String(error) });
      }
    } finally {
      if (generation === mapRequestGeneration.current) setMapLoading(false);
    }
  }, []);

  useEffect(() => {
    mapRequestGeneration.current += 1;
    setDataMapPayload(null);
    setMapRequested(false);
    setMapLoading(false);
    setMapError(null);
    if (isOpen) {
      void fetchLogs();
    }
  }, [fetchLogs, isOpen]);

  useEffect(() => {
    if (isOpen && view === "map" && !dataMapPayload && !mapRequested) {
      void fetchDataMap();
    }
  }, [dataMapPayload, fetchDataMap, isOpen, mapRequested, view]);

  const dataMap = useMemo(
    () => dataMapPayload ? dataMapToGraph(dataMapPayload, language) : null,
    [dataMapPayload, language],
  );

  return (
    <SystemDrawer
      open={isOpen}
      onClose={onClose}
      label={isZh ? "数据传输审计日志" : "Data Transmission Audit Log"}
      closeLabel={isZh ? "关闭审计日志" : "Close audit log"}
      className="audit-panel flex flex-col"
    >
        {/* Header */}
        <div className="p-5 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white tracking-wide">
              {isZh ? "数据传输审计日志" : "Data Transmission Audit Log"}
            </h2>
            <p className="text-xs text-white/40 mt-0.5">
              {isZh
                ? "检查与审计发送至 LLM 引擎的所有提示词载荷。"
                : "Review and audit all prompt payloads transmitted to LLM engines."}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <SystemIconButton
              onClick={() => view === "log" ? void fetchLogs() : void fetchDataMap()}
              disabled={view === "log" ? loading : mapLoading}
              label={view === "log"
                ? (isZh ? "刷新日志" : "Refresh logs")
                : (isZh ? "刷新数据地图" : "Refresh data map")}
            >
              <RotateCw className={loading || mapLoading ? "animate-spin" : ""} size={17} />
            </SystemIconButton>
            <SystemIconButton onClick={onClose} label={isZh ? "关闭审计日志" : "Close audit log"}><X size={18} /></SystemIconButton>
          </div>
        </div>

        <nav className="audit-panel-tabs" aria-label={isZh ? "审计视图" : "Audit views"}>
          <button
            aria-pressed={view === "log"}
            className={view === "log" ? "is-active" : ""}
            onClick={() => setView("log")}
            type="button"
          >
            <FileClock size={14} />
            {isZh ? "审计日志" : "Audit log"}
          </button>
          <button
            aria-pressed={view === "map"}
            className={view === "map" ? "is-active" : ""}
            onClick={() => setView("map")}
            type="button"
          >
            <Network size={14} />
            {isZh ? "数据地图" : "Data map"}
          </button>
        </nav>

        {/* Content list */}
        {view === "map" ? (
          <div className="audit-data-map">
            {mapLoading && !dataMap ? (
              <div className="audit-map-message" role="status">
                {isZh ? "正在加载隐私数据地图…" : "Loading privacy data map…"}
              </div>
            ) : mapError ? (
              <div className="audit-map-message is-error" role="alert">
                {`${isZh ? "数据地图请求失败" : "Data map request failed"}${mapError.status !== undefined
                  ? ` (${mapError.status})`
                  : mapError.detail
                    ? `: ${mapError.detail}`
                    : ""}`}
              </div>
            ) : dataMap ? (
              <DeferredGraphExplorer
                ariaLabel={isZh ? "隐私数据地图" : "Privacy data map"}
                className="audit-data-map-explorer"
                dataset={dataMap}
                language={language}
                loadingLabel={isZh ? "正在加载隐私数据地图" : "Privacy data map loading"}
                loadingMessage={isZh ? "正在加载交互式隐私数据地图…" : "Loading interactive privacy data map…"}
              />
            ) : null}
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {logError && (
            <div className="audit-log-error" role="alert">
              <span>
                {`${isZh ? "审计日志请求失败" : "Audit log request failed"}${logError.status !== undefined
                  ? ` (${logError.status})`
                  : logError.detail
                    ? `: ${logError.detail}`
                    : ""}`}
              </span>
              <button type="button" onClick={() => void fetchLogs()}>
                {isZh ? "重试" : "Try again"}
              </button>
            </div>
          )}
          {loading && logs.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-sm text-white/30">
              {isZh ? "正在加载日志…" : "Loading logs..."}
            </div>
          ) : logs.length === 0 && !logError ? (
            <div className="h-48 flex flex-col items-center justify-center text-center text-sm text-white/30 border border-dashed border-white/5 rounded-xl p-4">
              {isZh ? "暂未记录数据传输。" : "No data transfers recorded yet."}
            </div>
          ) : (
            logs.map((log) => (
              <div
                key={log.id}
                className="border border-white/5 bg-white/[0.01] rounded-xl overflow-hidden"
              >
                {/* Collapsed Header Summary */}
                <button
                  type="button"
                  onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                  className="audit-log-toggle p-4 flex items-center justify-between cursor-pointer hover:bg-white/[0.03] transition-colors"
                  aria-expanded={expandedId === log.id}
                  aria-controls={`audit-log-details-${log.id}`}
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-cyan-500/10 text-cyan-300 border border-cyan-500/20">
                        {log.provider}
                      </span>
                      <span className="text-xs font-semibold text-white/80">
                        {log.model}
                      </span>
                    </div>
                    <div className="text-xs text-white/50 truncate max-w-[360px]">
                      {isZh ? "提示词" : "Prompt"}: {log.prompt}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[10px] text-white/30">
                      {new Date(log.timestamp).toLocaleTimeString(isZh ? "zh-CN" : "en-US")}
                    </span>
                    <svg
                      className={`w-4 h-4 text-white/30 transform transition-transform ${
                        expandedId === log.id ? "rotate-180" : ""
                      }`}
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 9l-7 7-7-7"
                      />
                    </svg>
                  </div>
                </button>

                {/* Expanded Details */}
                {expandedId === log.id && (
                  <div id={`audit-log-details-${log.id}`} className="p-4 border-t border-white/5 bg-black/20 space-y-3 text-xs">
                    <div>
                      <div className="font-semibold text-white/40 mb-1">
                        {isZh ? "发送的提示词载荷：" : "PROMPT SEND PAYLOAD:"}
                      </div>
                      <pre className="p-3 bg-black/40 border border-white/5 rounded-lg overflow-x-auto text-[11px] text-slate-300 font-mono whitespace-pre-wrap">
                        {log.prompt}
                      </pre>
                    </div>
                    <div>
                      <div className="font-semibold text-white/40 mb-1">
                        {isZh ? "收到的原始 LLM 响应：" : "RAW LLM RESPONSE RECEIVED:"}
                      </div>
                      <pre className="p-3 bg-black/40 border border-white/5 rounded-lg overflow-x-auto text-[11px] text-cyan-300 font-mono whitespace-pre-wrap">
                        {log.response}
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
          </div>
        )}
    </SystemDrawer>
  );
};
