import React, { useState, useEffect, useRef } from "react";
import { FileClock, Network, RotateCw, X } from "lucide-react";
import type { GraphDataset } from "../lib/graphVisualization";
import { dataMapToGraph } from "../lib/graphScenes";
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
  onClose: () => void;
}

export const AuditLogPanel: React.FC<AuditLogPanelProps> = ({ isOpen, onClose }) => {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [view, setView] = useState<"log" | "map">("log");
  const [dataMap, setDataMap] = useState<GraphDataset | null>(null);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapError, setMapError] = useState("");
  const [mapRequested, setMapRequested] = useState(false);
  const mapRequestGeneration = useRef(0);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const port = 8000;
      const response = await fetch(`http://${window.location.hostname}:${port}/api/audit-logs`);
      if (response.ok) {
        const data = await response.json();
        setLogs(data);
      }
    } catch (error) {
      console.error("Error fetching audit logs:", error);
    } finally {
      setLoading(false);
    }
  };

  const fetchDataMap = async () => {
    const generation = ++mapRequestGeneration.current;
    setMapRequested(true);
    setMapLoading(true);
    setMapError("");
    try {
      const response = await fetch(`http://${window.location.hostname}:8000/api/data-map`);
      if (!response.ok) throw new Error(`Data map request failed (${response.status})`);
      const nextDataMap = dataMapToGraph(await response.json());
      if (generation === mapRequestGeneration.current) setDataMap(nextDataMap);
    } catch (error) {
      if (generation === mapRequestGeneration.current) {
        setMapError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (generation === mapRequestGeneration.current) setMapLoading(false);
    }
  };

  useEffect(() => {
    mapRequestGeneration.current += 1;
    setDataMap(null);
    setMapRequested(false);
    setMapLoading(false);
    setMapError("");
    if (isOpen) {
      void fetchLogs();
    }
  }, [isOpen]);

  useEffect(() => {
    if (isOpen && view === "map" && !dataMap && !mapRequested) {
      void fetchDataMap();
    }
  }, [dataMap, isOpen, mapRequested, view]);

  return (
    <SystemDrawer open={isOpen} onClose={onClose} label="Data Transmission Audit Log" className="audit-panel flex flex-col">
        {/* Header */}
        <div className="p-5 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-white tracking-wide">
              Data Transmission Audit Log
            </h2>
            <p className="text-xs text-white/40 mt-0.5">
              Review and audit all prompt payloads transmitted to LLM engines.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <SystemIconButton
              onClick={() => view === "log" ? void fetchLogs() : void fetchDataMap()}
              disabled={view === "log" ? loading : mapLoading}
              label={view === "log" ? "Refresh logs" : "Refresh data map"}
            >
              <RotateCw className={loading || mapLoading ? "animate-spin" : ""} size={17} />
            </SystemIconButton>
            <SystemIconButton onClick={onClose} label="Close audit log"><X size={18} /></SystemIconButton>
          </div>
        </div>

        <nav className="audit-panel-tabs" aria-label="Audit views">
          <button
            aria-pressed={view === "log"}
            className={view === "log" ? "is-active" : ""}
            onClick={() => setView("log")}
            type="button"
          >
            <FileClock size={14} />
            Audit log
          </button>
          <button
            aria-pressed={view === "map"}
            className={view === "map" ? "is-active" : ""}
            onClick={() => setView("map")}
            type="button"
          >
            <Network size={14} />
            Data map
          </button>
        </nav>

        {/* Content list */}
        {view === "map" ? (
          <div className="audit-data-map">
            {mapLoading && !dataMap ? (
              <div className="audit-map-message" role="status">Loading privacy data map…</div>
            ) : mapError ? (
              <div className="audit-map-message is-error" role="alert">{mapError}</div>
            ) : dataMap ? (
              <DeferredGraphExplorer
                ariaLabel="Privacy data map"
                className="audit-data-map-explorer"
                dataset={dataMap}
                loadingLabel="Privacy data map loading"
              />
            ) : null}
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {loading && logs.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-sm text-white/30">
              Loading logs...
            </div>
          ) : logs.length === 0 ? (
            <div className="h-48 flex flex-col items-center justify-center text-center text-sm text-white/30 border border-dashed border-white/5 rounded-xl p-4">
              No data transfers recorded yet.
            </div>
          ) : (
            logs.map((log) => (
              <div
                key={log.id}
                className="border border-white/5 bg-white/[0.01] rounded-xl overflow-hidden"
              >
                {/* Collapsed Header Summary */}
                <div
                  onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
                  className="p-4 flex items-center justify-between cursor-pointer hover:bg-white/[0.03] transition-colors"
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
                      Prompt: {log.prompt}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-[10px] text-white/30">
                      {new Date(log.timestamp).toLocaleTimeString()}
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
                </div>

                {/* Expanded Details */}
                {expandedId === log.id && (
                  <div className="p-4 border-t border-white/5 bg-black/20 space-y-3 text-xs">
                    <div>
                      <div className="font-semibold text-white/40 mb-1">PROMPT SEND PAYLOAD:</div>
                      <pre className="p-3 bg-black/40 border border-white/5 rounded-lg overflow-x-auto text-[11px] text-slate-300 font-mono whitespace-pre-wrap">
                        {log.prompt}
                      </pre>
                    </div>
                    <div>
                      <div className="font-semibold text-white/40 mb-1">RAW LLM RESPONSE RECEIVED:</div>
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
