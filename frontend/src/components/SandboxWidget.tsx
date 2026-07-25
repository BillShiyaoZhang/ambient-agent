import React, { useCallback, useEffect, useRef, useState } from "react";

import type { Widget } from "./DashboardCanvas";
import wsService from "../services/websocket";


interface SandboxWidgetProps {
  widget: Widget;
  onFullscreen?: (id: string) => void;
  onMinimize?: (id: string) => void;
}

interface RuntimeFrame {
  format: "jpeg" | "png";
  data: string;
  width: number;
  height: number;
}

interface RuntimeFailure {
  code: string;
  message: string;
  classification?: string;
}

interface Viewport {
  width: number;
  height: number;
  device_scale_factor: number;
}


const runtimeWebSocketUrl = (appId: string) => {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.hostname}:8000/ws/widgets/${encodeURIComponent(appId)}/runtime`;
};

const pointerButton = (button: number) => {
  if (button === 0) return "left";
  if (button === 1) return "middle";
  if (button === 2) return "right";
  if (button === 3) return "back";
  if (button === 4) return "forward";
  return "none";
};

const keyboardModifiers = (event: React.KeyboardEvent) =>
  (event.altKey ? 1 : 0) |
  (event.ctrlKey ? 2 : 0) |
  (event.metaKey ? 4 : 0) |
  (event.shiftKey ? 8 : 0);


export const SandboxWidget: React.FC<SandboxWidgetProps> = ({
  widget,
  onFullscreen,
  onMinimize,
}) => {
  const playerRef = useRef<HTMLDivElement>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const viewportRef = useRef<Viewport>({
    width: 640,
    height: 480,
    device_scale_factor: Math.min(window.devicePixelRatio || 1, 2),
  });
  const [frame, setFrame] = useState<RuntimeFrame | null>(null);
  const [failure, setFailure] = useState<RuntimeFailure | null>(null);
  const [status, setStatus] = useState<"connecting" | "ready" | "closed">("connecting");

  const send = useCallback((message: Record<string, unknown>) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
    }
  }, []);

  const sendViewport = useCallback(() => {
    send({ type: "viewport", ...viewportRef.current });
  }, [send]);

  useEffect(() => {
    setFrame(null);
    setFailure(null);
    setStatus("connecting");
    const socket = new WebSocket(runtimeWebSocketUrl(widget.id));
    socketRef.current = socket;

    socket.onopen = () => {
      setStatus("ready");
      sendViewport();
      send({
        type: "visibility",
        visible: document.visibilityState !== "hidden",
      });
    };
    socket.onmessage = (event) => {
      let message: Record<string, any>;
      try {
        message = JSON.parse(String(event.data));
      } catch {
        setFailure({
          code: "runtime_protocol_invalid",
          message: "Widget Runtime returned an invalid message",
          classification: "operator",
        });
        return;
      }
      if (message.type === "frame" && typeof message.data === "string") {
        setFrame({
          format: message.format === "png" ? "png" : "jpeg",
          data: message.data,
          width: Number(message.width) || viewportRef.current.width,
          height: Number(message.height) || viewportRef.current.height,
        });
        setFailure(null);
        return;
      }
      if (message.type === "runtime_error") {
        const error = message.error && typeof message.error === "object" ? message.error : {};
        setFailure({
          code: String(error.code || "widget_runtime_failed"),
          message: String(error.message || "Widget Runtime failed"),
          classification: error.classification ? String(error.classification) : undefined,
        });
        return;
      }
      if (message.type === "host_event") {
        if (message.event === "fullscreen") onFullscreen?.(widget.id);
        if (message.event === "minimize") onMinimize?.(widget.id);
        if (message.event === "send_message" && typeof message.text === "string") {
          wsService.sendMessage({ sender: "user", content: message.text });
        }
      }
    };
    socket.onerror = () => {
      setFailure({
        code: "widget_runtime_unavailable",
        message: "Widget Runtime is unavailable",
        classification: "operator",
      });
    };
    socket.onclose = () => {
      setStatus("closed");
      if (socketRef.current === socket) socketRef.current = null;
    };

    return () => {
      if (socketRef.current === socket) socketRef.current = null;
      socket.close();
    };
  }, [
    onFullscreen,
    onMinimize,
    send,
    sendViewport,
    widget.grants_digest,
    widget.id,
    widget.manifest_revision,
  ]);

  useEffect(() => {
    const player = playerRef.current;
    if (!player) return;
    if (typeof ResizeObserver === "undefined") {
      const measure = () => {
        const bounds = player.getBoundingClientRect();
        viewportRef.current = {
          width: Math.max(64, Math.round(bounds.width || 640)),
          height: Math.max(64, Math.round(bounds.height || 480)),
          device_scale_factor: Math.min(window.devicePixelRatio || 1, 2),
        };
        sendViewport();
      };
      measure();
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries.at(-1);
      if (!entry) return;
      const width = Math.max(64, Math.round(entry.contentRect.width));
      const height = Math.max(64, Math.round(entry.contentRect.height));
      viewportRef.current = {
        width,
        height,
        device_scale_factor: Math.min(window.devicePixelRatio || 1, 2),
      };
      sendViewport();
    });
    observer.observe(player);
    return () => observer.disconnect();
  }, [sendViewport]);

  useEffect(() => {
    const handleVisibility = () => {
      send({
        type: "visibility",
        visible: document.visibilityState !== "hidden",
      });
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, [send]);

  const point = (clientX: number, clientY: number) => {
    const bounds = playerRef.current?.getBoundingClientRect();
    return {
      x: clientX - (bounds?.left ?? 0),
      y: clientY - (bounds?.top ?? 0),
    };
  };

  const sendPointer = (
    event: React.PointerEvent<HTMLDivElement>,
    kind: "mousePressed" | "mouseReleased" | "mouseMoved",
  ) => {
    const coordinates = point(event.clientX, event.clientY);
    send({
      type: "pointer",
      event: kind,
      ...coordinates,
      button: pointerButton(event.button),
      buttons: event.buttons,
      click_count: kind === "mouseMoved" ? 0 : 1,
    });
  };

  return (
    <div
      ref={playerRef}
      id={widget.id}
      data-testid={`sandbox-${widget.id}`}
      className="ambient-widget-root relative w-full h-full overflow-hidden outline-none bg-transparent"
      role="application"
      aria-label={widget.title}
      tabIndex={0}
      onPointerDown={(event) => {
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        sendPointer(event, "mousePressed");
      }}
      onPointerUp={(event) => sendPointer(event, "mouseReleased")}
      onPointerMove={(event) => sendPointer(event, "mouseMoved")}
      onWheel={(event) => {
        const coordinates = point(event.clientX, event.clientY);
        send({
          type: "wheel",
          ...coordinates,
          delta_x: event.deltaX,
          delta_y: event.deltaY,
        });
      }}
      onKeyDown={(event) => {
        send({
          type: "key",
          event: "keyDown",
          key: event.key,
          code: event.code,
          text: event.key.length === 1 ? event.key : "",
          modifiers: keyboardModifiers(event),
        });
      }}
      onKeyUp={(event) => {
        send({
          type: "key",
          event: "keyUp",
          key: event.key,
          code: event.code,
          text: "",
          modifiers: keyboardModifiers(event),
        });
      }}
      onFocus={() => send({ type: "focus", focused: true })}
      onBlur={() => send({ type: "focus", focused: false })}
      onContextMenu={(event) => event.preventDefault()}
    >
      {frame && (
        <img
          src={`data:image/${frame.format};base64,${frame.data}`}
          alt={widget.title}
          draggable={false}
          className="block w-full h-full object-fill pointer-events-none select-none"
          width={frame.width}
          height={frame.height}
        />
      )}

      {!frame && !failure && (
        <div className="absolute inset-0 flex items-center justify-center text-[11px] text-white/45">
          {status === "closed" ? "Widget Runtime disconnected" : "Starting isolated Widget Runtime…"}
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
        </div>
      )}
    </div>
  );
};
