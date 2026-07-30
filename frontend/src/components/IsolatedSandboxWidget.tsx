import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  SandboxWidgetProps,
  WidgetPresentationContext,
} from "./PixelSandboxWidget";
import wsService from "../services/websocket";
import {
  WidgetStorageBroker,
  type WidgetStorageRequest,
} from "../services/widgetStorage";
import { apiUrl, webSocketUrl } from "../services/apiBase";
import {
  MAX_SOCKET_RECONNECT_ATTEMPTS,
  socketReconnectDelay,
} from "../services/socketReconnect";

const CLIENT_RUNTIME_PROTOCOL = "ambient-widget-client-v1";
const CLIENT_RUNTIME_PROTOCOL_VERSION = 1;
const MAX_RPC_MESSAGE_BYTES = 1024 * 1024;
const MAX_RPC_FIELD_LENGTH = 200;
const MAX_HOST_MESSAGE_LENGTH = 16_384;
const RUNTIME_HANDSHAKE_TIMEOUT_MS = 15_000;
const SUSPEND_FLUSH_TIMEOUT_MS = 1_000;
const IDENTITY_FIELDS = new Set([
  "app_id",
  "widget_id",
  "session_id",
  "manifest_revision",
  "grants_digest",
  "artifact_digest",
]);

const DEFAULT_PRESENTATION_CONTEXT: WidgetPresentationContext = {
  theme: { preference: "system", effective: "dark" },
  locale: "en-US",
  reduced_motion: false,
};

interface RuntimeTicket {
  ticket: string;
  expires_at: string;
  frame_url: string;
  protocol: typeof CLIENT_RUNTIME_PROTOCOL;
}

interface RuntimeBootstrap {
  controller_source: string;
  capability_ids: string[];
}

interface RuntimeFailure {
  code: string;
  message: string;
  classification?: string;
}

interface ClientRuntimeSession {
  disposed: boolean;
  socket: WebSocket | null;
  port: MessagePort | null;
  nonce: string | null;
  framePortOffered: boolean;
  portReady: boolean;
  initialized: boolean;
  ready: boolean;
  handshakeTimer: number | null;
  suspendRequestId: string | null;
  suspendTimer: number | null;
  closingReason: string | null;
  bootstrap: RuntimeBootstrap | null;
}

const runtimeWebSocketUrl = (appId: string) => {
  return webSocketUrl(
    `/ws/widgets/${encodeURIComponent(appId)}/client-runtime`,
  );
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const boundedString = (
  value: unknown,
  maximum: number,
): value is string => typeof value === "string"
  && value.length > 0
  && value.length <= maximum;

const runtimeFailure = (
  value: unknown,
  fallbackCode: string,
  fallbackMessage: string,
): RuntimeFailure => {
  const error = isRecord(value) ? value : {};
  return {
    code: boundedString(error.code, 200) ? error.code : fallbackCode,
    message: typeof error.message === "string" && error.message.length > 0
      ? error.message.slice(0, 4096)
      : fallbackMessage,
    classification: boundedString(error.classification, 200)
      ? error.classification
      : undefined,
  };
};

const parseTicket = (value: unknown): RuntimeTicket => {
  if (
    !isRecord(value)
    || !boundedString(value.ticket, 4096)
    || !boundedString(value.expires_at, 200)
    || !boundedString(value.frame_url, 4096)
    || value.protocol !== CLIENT_RUNTIME_PROTOCOL
  ) {
    throw new Error("Invalid Widget Runtime ticket");
  }
  return {
    ticket: value.ticket,
    expires_at: value.expires_at,
    frame_url: value.frame_url,
    protocol: value.protocol,
  };
};

const parseBootstrap = (value: Record<string, unknown>): RuntimeBootstrap | null => {
  if (
    value.protocol_version !== CLIENT_RUNTIME_PROTOCOL_VERSION
    || typeof value.controller_source !== "string"
    || !Array.isArray(value.capability_ids)
    || !value.capability_ids.every((item) => typeof item === "string")
  ) {
    return null;
  }
  return {
    controller_source: value.controller_source,
    capability_ids: value.capability_ids,
  };
};

const secureNonce = () => {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }
  throw new Error("Secure randomness is unavailable");
};

const encodedSize = (value: unknown) => {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).byteLength;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
};

const sanitizedRpcRequest = (
  message: Record<string, unknown>,
): Record<string, unknown> | null => {
  if (
    !boundedString(message.request_id, MAX_RPC_FIELD_LENGTH)
    || !boundedString(message.method, MAX_RPC_FIELD_LENGTH)
    || !isRecord(message.params)
  ) {
    return null;
  }
  const params = Object.fromEntries(
    Object.entries(message.params)
      .filter(([key]) => !IDENTITY_FIELDS.has(key)),
  );
  const request = {
    type: "rpc_request",
    request_id: message.request_id,
    method: message.method,
    params,
  };
  return encodedSize(request) <= MAX_RPC_MESSAGE_BYTES ? request : null;
};

const ticketFailureMessage = async (response: Response) => {
  try {
    const payload = await response.json() as unknown;
    if (isRecord(payload)) {
      const detail = isRecord(payload.detail) ? payload.detail : payload;
      if (typeof detail.message === "string") return detail.message.slice(0, 4096);
      if (typeof payload.detail === "string") return payload.detail.slice(0, 4096);
    }
  } catch {
    // A structured error is optional at this boundary.
  }
  return `Widget Runtime ticket request failed (${response.status})`;
};


export const IsolatedSandboxWidget: React.FC<SandboxWidgetProps> = ({
  widget,
  presentationContext = DEFAULT_PRESENTATION_CONTEXT,
  suspendRequested = false,
  onActivate,
  onFullscreen,
  onMinimize,
  onSuspendReady,
}) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const sessionRef = useRef<ClientRuntimeSession | null>(null);
  const hostCallbacksRef = useRef({
    onActivate,
    onFullscreen,
    onMinimize,
    onSuspendReady,
  });
  hostCallbacksRef.current = {
    onActivate,
    onFullscreen,
    onMinimize,
    onSuspendReady,
  };
  const suspendRequestedRef = useRef(suspendRequested);
  suspendRequestedRef.current = suspendRequested;
  const suspendCycleCompletedRef = useRef(false);
  const presentationContextRef = useRef(presentationContext);
  presentationContextRef.current = presentationContext;
  const storageBroker = useMemo(
    () => new WidgetStorageBroker(widget.id),
    [widget.id],
  );
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [failure, setFailure] = useState<RuntimeFailure | null>(null);
  const [hostMessageError, setHostMessageError] = useState<string | null>(null);
  const [status, setStatus] = useState<"connecting" | "ready" | "closed">("connecting");
  const [sessionGeneration, setSessionGeneration] = useState(0);
  const reconnectAttemptRef = useRef(0);

  const retryRuntime = useCallback(() => {
    reconnectAttemptRef.current = 0;
    setFailure(null);
    setStatus("connecting");
    setSessionGeneration((generation) => generation + 1);
  }, []);

  const isCurrentSession = useCallback(
    (session: ClientRuntimeSession) =>
      !session.disposed && sessionRef.current === session,
    [],
  );

  const clearSuspendRequest = useCallback((session: ClientRuntimeSession) => {
    if (session.suspendTimer !== null) {
      window.clearTimeout(session.suspendTimer);
      session.suspendTimer = null;
    }
    session.suspendRequestId = null;
  }, []);

  const completeSuspendCycle = useCallback(() => {
    if (
      !suspendRequestedRef.current
      || suspendCycleCompletedRef.current
    ) {
      return;
    }
    suspendCycleCompletedRef.current = true;
    hostCallbacksRef.current.onSuspendReady?.();
  }, []);

  const completeSuspendRequest = useCallback((
    session: ClientRuntimeSession,
    requestId: string,
  ) => {
    if (
      !isCurrentSession(session)
      || session.suspendRequestId !== requestId
      || !suspendRequestedRef.current
    ) {
      return;
    }
    clearSuspendRequest(session);
    completeSuspendCycle();
  }, [clearSuspendRequest, completeSuspendCycle, isCurrentSession]);

  const initializeFrame = useCallback((session: ClientRuntimeSession) => {
    if (
      !isCurrentSession(session)
      || session.initialized
      || !session.port
      || !session.portReady
      || !session.nonce
      || !session.bootstrap
    ) {
      return;
    }
    session.initialized = true;
    session.port.postMessage({
      type: "init",
      protocol_version: CLIENT_RUNTIME_PROTOCOL_VERSION,
      nonce: session.nonce,
      controller_source: session.bootstrap.controller_source,
      capability_ids: session.bootstrap.capability_ids,
      presentation_context: presentationContextRef.current,
    });
  }, [isCurrentSession]);

  const handleHostEvent = useCallback((message: Record<string, unknown>) => {
    if (message.event === "focus") {
      hostCallbacksRef.current.onActivate?.();
      return;
    }
    if (message.event === "fullscreen") {
      hostCallbacksRef.current.onFullscreen?.(widget.id);
      return;
    }
    if (message.event === "minimize") {
      hostCallbacksRef.current.onMinimize?.(widget.id);
      return;
    }
    if (message.event === "send_message" && typeof message.text === "string") {
      const delivered = wsService.sendMessage({
        sender: "user",
        content: message.text.slice(0, MAX_HOST_MESSAGE_LENGTH),
      });
      setHostMessageError(delivered
        ? null
        : (presentationContextRef.current.locale.startsWith("zh")
            ? "消息尚未发送。请恢复 Ambient 连接后在聊天中重试。"
            : "Message not sent. Restore the Ambient connection and retry in chat."));
    }
  }, [widget.id]);

  const handleFrameMessage = useCallback((
    session: ClientRuntimeSession,
    sourcePort: MessagePort,
    value: unknown,
  ) => {
    if (
      !isCurrentSession(session)
      || session.port !== sourcePort
      || !isRecord(value)
    ) {
      return;
    }
    if (value.type === "port_ready") {
      if (value.nonce !== session.nonce) return;
      session.portReady = true;
      initializeFrame(session);
      return;
    }
    if (!session.portReady || !session.initialized) return;
    if (value.type === "ready") {
      session.ready = true;
      reconnectAttemptRef.current = 0;
      if (session.handshakeTimer !== null) {
        window.clearTimeout(session.handshakeTimer);
        session.handshakeTimer = null;
      }
      setStatus("ready");
      setFailure(null);
      return;
    }
    if (value.type === "suspend_ready") {
      if (!boundedString(value.request_id, MAX_RPC_FIELD_LENGTH)) return;
      completeSuspendRequest(session, value.request_id);
      return;
    }
    if (value.type === "runtime_error" || value.type === "error") {
      setFailure(runtimeFailure(
        value.error,
        "controller_runtime_error",
        "Widget Controller failed",
      ));
      return;
    }
    if (value.type === "rpc_request") {
      const request = sanitizedRpcRequest(value);
      if (!request) {
        if (boundedString(value.request_id, MAX_RPC_FIELD_LENGTH)) {
          sourcePort.postMessage({
            type: "rpc_response",
            request_id: value.request_id,
            error: {
              code: "runtime_rpc_invalid",
              message: "Invalid or oversized Widget capability request",
            },
          });
        }
        return;
      }
      if (session.socket?.readyState !== WebSocket.OPEN) {
        sourcePort.postMessage({
          type: "rpc_response",
          request_id: request.request_id,
          error: {
            code: "runtime_rpc_unavailable",
            message: "Widget Runtime connection is unavailable",
          },
        });
        return;
      }
      session.socket.send(JSON.stringify(request));
      return;
    }
    if (value.type === "storage_request") {
      const requestPort = sourcePort;
      void storageBroker.handle(value as unknown as WidgetStorageRequest)
        .then((response) => {
          if (
            isCurrentSession(session)
            && session.port === requestPort
          ) {
            requestPort.postMessage(response);
          }
        });
      return;
    }
    if (value.type === "host_event") {
      handleHostEvent(value);
    }
  }, [
    handleHostEvent,
    completeSuspendRequest,
    initializeFrame,
    isCurrentSession,
    storageBroker,
  ]);

  const handleFrameLoad = useCallback((event: React.SyntheticEvent<HTMLIFrameElement>) => {
    const session = sessionRef.current;
    const targetWindow = event.currentTarget.contentWindow;
    if (!session || !isCurrentSession(session) || !targetWindow) return;

    if (session.framePortOffered) {
      const reason = "Widget frame navigated or reloaded unexpectedly";
      session.closingReason = reason;
      setFailure({
        code: "runtime_frame_navigation_blocked",
        message: reason,
        classification: "abuse_or_budget",
      });
      setStatus("closed");
      if (session.handshakeTimer !== null) {
        window.clearTimeout(session.handshakeTimer);
        session.handshakeTimer = null;
      }
      if (session.port) {
        session.port.onmessage = null;
        session.port.close();
        session.port = null;
      }
      if (session.socket?.readyState === WebSocket.CONNECTING) {
        session.socket.onopen = () => session.socket?.close();
      } else {
        session.socket?.close();
      }
      if (session.suspendRequestId) {
        completeSuspendRequest(session, session.suspendRequestId);
      }
      session.disposed = true;
      return;
    }
    session.framePortOffered = true;
    if (session.port) {
      session.port.onmessage = null;
      session.port.close();
    }
    const channel = new MessageChannel();
    const nonce = secureNonce();
    session.port = channel.port1;
    session.nonce = nonce;
    session.portReady = false;
    session.initialized = false;
    const sourcePort = channel.port1;
    sourcePort.onmessage = (message) => {
      handleFrameMessage(session, sourcePort, message.data);
    };
    sourcePort.start();
    targetWindow.postMessage(
      {
        type: "ambient-widget-port",
        protocol_version: CLIENT_RUNTIME_PROTOCOL_VERSION,
        nonce,
      },
      "*",
      [channel.port2],
    );
  }, [completeSuspendRequest, handleFrameMessage, isCurrentSession]);

  useEffect(() => {
    const session: ClientRuntimeSession = {
      disposed: false,
      socket: null,
      port: null,
      nonce: null,
      framePortOffered: false,
      portReady: false,
      initialized: false,
      ready: false,
      handshakeTimer: null,
      suspendRequestId: null,
      suspendTimer: null,
      closingReason: null,
      bootstrap: null,
    };
    sessionRef.current = session;
    setFrameUrl(null);
    setFailure(null);
    setStatus("connecting");
    const abortController = new AbortController();
    let reconnectTimer: number | null = null;

    const handleServerMessage = (value: unknown) => {
      if (!isCurrentSession(session) || !isRecord(value)) return;
      if (value.type === "bootstrap") {
        const bootstrap = parseBootstrap(value);
        if (!bootstrap) {
          setFailure({
            code: "runtime_protocol_invalid",
            message: "Widget Runtime returned an invalid bootstrap",
            classification: "operator",
          });
          return;
        }
        session.bootstrap = bootstrap;
        initializeFrame(session);
        return;
      }
      if (value.type === "rpc_response" || value.type === "subscription_event") {
        session.port?.postMessage(value);
        return;
      }
      if (value.type === "host_event") {
        handleHostEvent(value);
        return;
      }
      if (value.type === "runtime_error") {
        setFailure(runtimeFailure(
          value.error,
          "widget_runtime_failed",
          "Widget Runtime failed",
        ));
        session.port?.postMessage(value);
        return;
      }
      if (value.type === "session_invalidated") {
        const reason = typeof value.reason === "string"
          ? value.reason.slice(0, 4096)
          : "Widget Runtime session was invalidated";
        setFailure({
          code: "runtime_session_invalidated",
          message: reason,
          classification: "operator",
        });
        session.port?.postMessage({
          type: "session_invalidated",
          reason,
        });
      }
    };

    const start = async () => {
      try {
        const response = await fetch(
          apiUrl(
            `/api/apps/${encodeURIComponent(widget.id)}/client-runtime-ticket`,
          ),
          {
            method: "POST",
            signal: abortController.signal,
          },
        );
        if (!response.ok) throw new Error(await ticketFailureMessage(response));
        const ticket = parseTicket(await response.json());
        if (!isCurrentSession(session)) return;
        setFrameUrl(ticket.frame_url);

        const socket = new WebSocket(
          runtimeWebSocketUrl(widget.id),
          [CLIENT_RUNTIME_PROTOCOL, `ticket.${ticket.ticket}`],
        );
        session.socket = socket;
        socket.onopen = () => {
          if (!isCurrentSession(session)) return;
          setFailure(null);
          setStatus("connecting");
          session.handshakeTimer = window.setTimeout(() => {
            if (!isCurrentSession(session) || session.ready) return;
            const reason = "Widget Runtime handshake timed out";
            session.closingReason = reason;
            setFailure({
              code: "runtime_handshake_timeout",
              message: reason,
              classification: "operator",
            });
            setStatus("closed");
            session.port?.postMessage({
              type: "session_invalidated",
              reason,
            });
            if (session.socket?.readyState === WebSocket.CONNECTING) {
              session.socket.onopen = () => session.socket?.close();
            } else {
              session.socket?.close();
            }
          }, RUNTIME_HANDSHAKE_TIMEOUT_MS);
        };
        socket.onmessage = (event) => {
          if (!isCurrentSession(session)) return;
          try {
            handleServerMessage(JSON.parse(String(event.data)) as unknown);
          } catch {
            setFailure({
              code: "runtime_protocol_invalid",
              message: "Widget Runtime returned an invalid message",
              classification: "operator",
            });
          }
        };
        socket.onerror = () => {
          if (!isCurrentSession(session)) return;
          setFailure({
            code: "widget_runtime_unavailable",
            message: "Widget Runtime is unavailable",
            classification: "operator",
          });
        };
        socket.onclose = (event) => {
          if (!isCurrentSession(session)) return;
          if (session.handshakeTimer !== null) {
            window.clearTimeout(session.handshakeTimer);
            session.handshakeTimer = null;
          }
          setStatus("closed");
          const reason =
            event?.reason
            || session.closingReason
            || "Widget Runtime disconnected";
          session.port?.postMessage({
            type: "session_invalidated",
            reason: reason.slice(0, 4096),
          });
          if (event?.code === 1000 || session.closingReason) return;
          if (reconnectAttemptRef.current >= MAX_SOCKET_RECONNECT_ATTEMPTS) {
            setFailure({
              code: "widget_runtime_unavailable",
              message: "Widget Runtime connection could not be restored",
              classification: "operator",
            });
            return;
          }
          reconnectAttemptRef.current += 1;
          setFailure(null);
          setStatus("connecting");
          reconnectTimer = window.setTimeout(() => {
            if (isCurrentSession(session)) {
              setSessionGeneration((generation) => generation + 1);
            }
          }, socketReconnectDelay(reconnectAttemptRef.current));
        };
      } catch (error) {
        if (
          !isCurrentSession(session)
          || (error instanceof DOMException && error.name === "AbortError")
        ) {
          return;
        }
        setFailure({
          code: error instanceof Error
            && error.message === "Invalid Widget Runtime ticket"
            ? "runtime_ticket_invalid"
            : "runtime_ticket_unavailable",
          message: error instanceof Error
            ? error.message.slice(0, 4096)
            : "Widget Runtime ticket request failed",
          classification: "operator",
        });
        setStatus("closed");
      }
    };

    const startTimer = window.setTimeout(() => {
      void start();
    }, 0);

    return () => {
      session.disposed = true;
      window.clearTimeout(startTimer);
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (session.handshakeTimer !== null) {
        window.clearTimeout(session.handshakeTimer);
        session.handshakeTimer = null;
      }
      clearSuspendRequest(session);
      abortController.abort();
      if (sessionRef.current === session) sessionRef.current = null;
      if (session.port) {
        session.port.postMessage({ type: "dispose" });
        session.port.onmessage = null;
        session.port.close();
        session.port = null;
      }
      if (session.socket) {
        const socket = session.socket;
        session.socket = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        if (socket.readyState === WebSocket.CONNECTING) {
          socket.onopen = () => socket.close();
        } else {
          socket.onopen = null;
          socket.close();
        }
      }
    };
  }, [
    clearSuspendRequest,
    handleHostEvent,
    initializeFrame,
    isCurrentSession,
    widget.grants_digest,
    widget.id,
    widget.manifest_revision,
    sessionGeneration,
  ]);

  useEffect(() => {
    const session = sessionRef.current;
    if (!suspendRequested) {
      suspendCycleCompletedRef.current = false;
      if (session) clearSuspendRequest(session);
      return;
    }
    if (
      suspendCycleCompletedRef.current
      || session?.suspendRequestId
    ) {
      return;
    }
    if (
      !session
      || !isCurrentSession(session)
      || !session.ready
      || !session.port
    ) {
      completeSuspendCycle();
      return;
    }

    let requestId: string;
    try {
      requestId = secureNonce();
    } catch {
      completeSuspendCycle();
      return;
    }
    if (!boundedString(requestId, MAX_RPC_FIELD_LENGTH)) {
      completeSuspendCycle();
      return;
    }

    session.suspendRequestId = requestId;
    session.suspendTimer = window.setTimeout(() => {
      completeSuspendRequest(session, requestId);
    }, SUSPEND_FLUSH_TIMEOUT_MS);
    try {
      session.port.postMessage({
        type: "before_suspend",
        request_id: requestId,
      });
    } catch {
      completeSuspendRequest(session, requestId);
    }
  }, [
    clearSuspendRequest,
    completeSuspendCycle,
    completeSuspendRequest,
    isCurrentSession,
    suspendRequested,
    widget.grants_digest,
    widget.id,
    widget.manifest_revision,
  ]);

  useEffect(() => {
    const session = sessionRef.current;
    if (!session?.initialized || !session.port) return;
    session.port.postMessage({
      type: "presentation_context",
      presentation_context: presentationContextRef.current,
    });
  }, [
    presentationContext.locale,
    presentationContext.reduced_motion,
    presentationContext.theme.effective,
    presentationContext.theme.preference,
  ]);

  return (
    <div
      id={widget.id}
      data-testid={`sandbox-${widget.id}`}
      className="ambient-widget-root relative w-full h-full overflow-hidden bg-transparent"
      role="application"
      aria-label={widget.title}
    >
      {frameUrl && (
        <iframe
          ref={iframeRef}
          src={frameUrl}
          title={widget.title}
          sandbox="allow-scripts"
          allow=""
          referrerPolicy="no-referrer"
          className="block w-full h-full border-0 bg-transparent"
          onLoad={handleFrameLoad}
        />
      )}

      {status !== "ready" && !failure && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-[11px] text-white/45">
          {status === "closed"
            ? "Widget Runtime disconnected"
            : "Starting isolated Widget Runtime…"}
        </div>
      )}

      {failure && (
        <div className="absolute inset-0 overflow-auto p-4 bg-red-950/55 text-red-200 text-xs">
          <strong className="block mb-1">Widget Runtime Error</strong>
          <span>{failure.message}</span>
          <span className="block mt-2 font-mono text-[10px] text-red-300/70">
            {failure.code}
            {failure.classification ? ` · ${failure.classification}` : ""}
          </span>
          {status === "closed" && failure.classification === "operator" && (
            <button
              type="button"
              className="mt-3 rounded-md border border-red-300/35 px-2 py-1 text-[11px] hover:bg-red-100/10"
              onClick={retryRuntime}
            >
              {presentationContext.locale.startsWith("zh") ? "重试 Widget Runtime" : "Retry Widget Runtime"}
            </button>
          )}
        </div>
      )}
      {hostMessageError && (
        <div
          className="absolute right-3 bottom-3 left-3 rounded-lg border border-red-400/30 bg-red-950/90 p-2 text-[11px] text-red-200"
          role="alert"
        >
          {hostMessageError}
        </div>
      )}
    </div>
  );
};
