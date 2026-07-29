import { mountController } from "./controller_facade.mjs";
import { normalizePresentationContext } from "./presentation_context.mjs";


export const FRAME_PROTOCOL_VERSION = 1;

const FRAME_PORT_MESSAGE_TYPE = "ambient-widget-port";
const MAX_CONTROLLER_SOURCE_BYTES = 4 * 1024 * 1024;
const MAX_PENDING_REQUESTS = 32;
const MAX_NONCE_LENGTH = 512;
const MAX_HOST_MESSAGE_LENGTH = 16_384;
const MAX_SUSPEND_REQUEST_ID_LENGTH = 200;
const TRUSTED_ACTIVATION_EVENT_TYPES = Object.freeze([
  "pointerdown",
  "keydown",
  "wheel",
]);


function errorMessage(error, fallback) {
  const message =
    typeof error?.message === "string" ? error.message : String(error ?? fallback);
  return message.slice(0, 4096);
}


function runtimeError(code, error, classification = "operator") {
  return {
    type: "runtime_error",
    error: {
      code,
      message: errorMessage(error, code),
      classification,
    },
  };
}


function responseError(error, fallback) {
  const detail =
    typeof error === "object" && error !== null ? error : {};
  return Object.assign(
    new Error(
      typeof detail.message === "string" ? detail.message : fallback,
    ),
    detail,
  );
}


function validNonce(value) {
  return (
    typeof value === "string"
    && value.length > 0
    && value.length <= MAX_NONCE_LENGTH
  );
}


function validateInit(message, nonce) {
  if (
    typeof message !== "object"
    || message === null
    || message.type !== "init"
    || message.protocol_version !== FRAME_PROTOCOL_VERSION
    || message.nonce !== nonce
  ) {
    throw new Error("Widget frame init identity mismatch");
  }
  if (
    typeof message.controller_source !== "string"
    || message.controller_source.length === 0
    || new TextEncoder().encode(message.controller_source).byteLength
      > MAX_CONTROLLER_SOURCE_BYTES
  ) {
    throw new Error("Widget frame init.controller_source is invalid");
  }
  return {
    ...message,
    capability_ids: Array.isArray(message.capability_ids)
      ? message.capability_ids.filter((item) => typeof item === "string")
      : [],
    presentation_context: normalizePresentationContext(
      message.presentation_context,
    ),
  };
}


function createRequestSender(port, pending, prefix, type, phase) {
  let sequence = 0;
  return (payload) => {
    if (!["initializing", "ready"].includes(phase.current)) {
      return Promise.reject(new Error("Widget frame is not initialized"));
    }
    if (pending.size >= MAX_PENDING_REQUESTS) {
      return Promise.reject(
        new Error("Widget frame request concurrency limit reached"),
      );
    }
    const requestId = `${prefix}-${++sequence}`;
    const promise = new Promise((resolve, reject) => {
      pending.set(requestId, { resolve, reject });
    });
    try {
      port.postMessage({
        type,
        request_id: requestId,
        ...payload,
      });
    } catch (error) {
      pending.delete(requestId);
      return Promise.reject(error);
    }
    return promise;
  };
}


function createPortEndpoint(
  port,
  nonce,
  {
    initializeController,
    targetWindow,
    onDisposed,
  },
) {
  const phase = { current: "waiting_init" };
  const pendingRpc = new Map();
  const pendingStorage = new Map();
  const deferredSubscriptions = [];
  let deferredPresentation;
  let controller;
  let activeSuspendRequestId;
  let lastSuspendResponse;
  let disposed = false;

  const sendRpc = createRequestSender(
    port,
    pendingRpc,
    "rpc",
    "rpc_request",
    phase,
  );
  const sendStorage = createRequestSender(
    port,
    pendingStorage,
    "storage",
    "storage_request",
    phase,
  );

  const transport = Object.freeze({
    rpc(method, params) {
      if (
        typeof method !== "string"
        || typeof params !== "object"
        || params === null
      ) {
        return Promise.reject(new Error("Invalid ambient RPC request"));
      }
      return sendRpc({ method, params });
    },
    storageRequest(operation, payload = {}) {
      if (!["get", "set", "delete", "clear", "list"].includes(operation)) {
        return Promise.reject(new Error("Invalid Widget storage operation"));
      }
      const envelope = { operation };
      if (operation === "get" || operation === "delete" || operation === "set") {
        envelope.key = payload.key;
      }
      if (operation === "set") {
        envelope.value = payload.value;
      }
      return sendStorage(envelope);
    },
    hostEvent(event) {
      if (disposed || !["initializing", "ready"].includes(phase.current)) {
        return false;
      }
      const kind = event?.event;
      if (!["fullscreen", "minimize", "send_message"].includes(kind)) {
        return false;
      }
      port.postMessage({
        type: "host_event",
        event: kind,
        ...(kind === "send_message"
          ? {
              text: String(event.text ?? "").slice(0, MAX_HOST_MESSAGE_LENGTH),
            }
          : {}),
      });
      return true;
    },
  });

  const rejectPending = (message) => {
    for (const pending of [...pendingRpc.values(), ...pendingStorage.values()]) {
      pending.reject(new Error(message));
    }
    pendingRpc.clear();
    pendingStorage.clear();
  };

  const reportTrustedActivation = (event) => {
    if (disposed || event?.isTrusted !== true) return;
    try {
      port.postMessage({
        type: "host_event",
        event: "focus",
      });
    } catch {
      dispose();
    }
  };

  const dispose = (reason = "Widget frame was disposed") => {
    if (disposed) return;
    disposed = true;
    phase.current = "disposed";
    for (const eventType of TRUSTED_ACTIVATION_EVENT_TYPES) {
      targetWindow.removeEventListener(
        eventType,
        reportTrustedActivation,
        true,
      );
    }
    rejectPending(reason);
    activeSuspendRequestId = undefined;
    lastSuspendResponse = undefined;
    controller?.dispose?.();
    controller = undefined;
    port.onmessage = null;
    port.onmessageerror = null;
    port.close();
    onDisposed();
  };

  const sendRuntimeError = (code, error, classification) => {
    if (disposed) return;
    try {
      port.postMessage(runtimeError(code, error, classification));
    } catch {
      dispose();
    }
  };

  const settleResponse = (pendingRequests, message, fallback) => {
    const pending = pendingRequests.get(message.request_id);
    if (!pending) return;
    pendingRequests.delete(message.request_id);
    if (message.error) {
      pending.reject(responseError(message.error, fallback));
    } else {
      pending.resolve(message.result);
    }
  };

  const postSuspendResponse = (response) => {
    if (disposed || phase.current !== "ready") return false;
    try {
      port.postMessage(response);
      return true;
    } catch {
      dispose();
      return false;
    }
  };

  const finishBeforeSuspend = async (requestId) => {
    let response;
    try {
      await controller?.beforeSuspend?.();
      response = {
        type: "suspend_ready",
        request_id: requestId,
        ok: true,
      };
    } catch (error) {
      response = {
        type: "suspend_ready",
        request_id: requestId,
        ok: false,
        error: {
          message: errorMessage(
            error,
            "Widget pre-suspend flush failed",
          ),
        },
      };
    }
    if (disposed || activeSuspendRequestId !== requestId) return;
    activeSuspendRequestId = undefined;
    lastSuspendResponse = response;
    postSuspendResponse(response);
  };

  const handleBeforeSuspend = (message) => {
    if (phase.current !== "ready") return;
    const requestId = message.request_id;
    if (
      typeof requestId !== "string"
      || requestId.length === 0
      || requestId.length > MAX_SUSPEND_REQUEST_ID_LENGTH
    ) {
      return;
    }
    if (activeSuspendRequestId !== undefined) {
      if (activeSuspendRequestId === requestId) return;
      postSuspendResponse({
        type: "suspend_ready",
        request_id: requestId,
        ok: false,
        error: {
          message: "Widget pre-suspend flush is already in progress",
        },
      });
      return;
    }
    if (lastSuspendResponse?.request_id === requestId) {
      postSuspendResponse(lastSuspendResponse);
      return;
    }
    activeSuspendRequestId = requestId;
    void finishBeforeSuspend(requestId);
  };

  const finishInitialization = async (message) => {
    let init;
    try {
      init = validateInit(message, nonce);
    } catch (error) {
      sendRuntimeError("runtime_protocol_invalid", error, "abuse_or_budget");
      dispose();
      return;
    }

    phase.current = "initializing";
    try {
      const nextController = await initializeController(
        init,
        transport,
        targetWindow,
      );
      if (disposed) {
        nextController?.dispose?.();
        return;
      }
      controller = nextController;
      if (deferredPresentation) {
        controller?.updatePresentation?.(deferredPresentation);
        deferredPresentation = undefined;
      }
      for (const event of deferredSubscriptions.splice(0)) {
        controller?.deliverSubscription?.(
          event.subscription_id,
          event.data,
          event.error,
        );
      }
      phase.current = "ready";
      port.postMessage({ type: "ready" });
    } catch (error) {
      phase.current = "failed";
      sendRuntimeError("controller_load_failed", error, "code_only");
      rejectPending("Widget controller failed to initialize");
    }
  };

  const receive = (message) => {
    if (disposed || typeof message !== "object" || message === null) return;
    if (message.type === "before_suspend" && phase.current === "ready") {
      handleBeforeSuspend(message);
      return;
    }
    if (message.type === "rpc_response") {
      settleResponse(
        pendingRpc,
        message,
        "Widget capability request failed",
      );
      return;
    }
    if (message.type === "storage_response") {
      settleResponse(
        pendingStorage,
        message,
        "Widget storage request failed",
      );
      return;
    }
    if (message.type === "dispose") {
      dispose();
      return;
    }
    if (message.type === "session_invalidated") {
      const reason =
        typeof message.reason === "string" && message.reason.length > 0
          ? message.reason.slice(0, 4096)
          : "Widget Runtime session was invalidated";
      dispose(reason);
      return;
    }
    if (message.type === "init") {
      if (phase.current !== "waiting_init") {
        sendRuntimeError(
          "runtime_protocol_invalid",
          "Widget frame was initialized more than once",
          "abuse_or_budget",
        );
        dispose();
        return;
      }
      phase.current = "validating_init";
      void finishInitialization(message);
      return;
    }
    if (message.type === "subscription_event") {
      if (phase.current === "initializing") {
        if (deferredSubscriptions.length < 64) {
          deferredSubscriptions.push(message);
        }
        return;
      }
      controller?.deliverSubscription?.(
        message.subscription_id,
        message.data,
        message.error,
      );
      return;
    }
    if (message.type === "presentation_context") {
      const next = normalizePresentationContext(
        message.presentation_context ?? message,
      );
      if (phase.current === "initializing") {
        deferredPresentation = next;
      } else {
        controller?.updatePresentation?.(next);
      }
      return;
    }
    if (phase.current === "waiting_init") {
      sendRuntimeError(
        "runtime_protocol_invalid",
        "Widget frame expected an init message",
        "abuse_or_budget",
      );
      dispose();
    }
  };

  port.onmessage = (event) => receive(event.data);
  port.onmessageerror = () => {
    sendRuntimeError(
      "runtime_protocol_invalid",
      "Widget frame received an unreadable port message",
      "abuse_or_budget",
    );
    dispose();
  };
  for (const eventType of TRUSTED_ACTIVATION_EVENT_TYPES) {
    targetWindow.addEventListener(eventType, reportTrustedActivation, true);
  }
  port.start?.();
  port.postMessage({ type: "port_ready", nonce });

  return Object.freeze({
    dispose,
    reportControllerError(error) {
      sendRuntimeError("controller_runtime_error", error, "code_only");
    },
  });
}


async function initializeBrowserController(message, transport, targetWindow) {
  return mountController({
    babel: targetWindow.Babel,
    runtime: targetWindow.htmPreact,
    root: targetWindow.document.getElementById("root"),
    controllerSource: message.controller_source,
    capabilityIds: message.capability_ids,
    presentationContext: message.presentation_context,
    transport,
  });
}


export function installFrameShell(
  targetWindow,
  { initializeController = initializeBrowserController } = {},
) {
  if (
    !targetWindow
    || typeof targetWindow.addEventListener !== "function"
    || typeof targetWindow.removeEventListener !== "function"
  ) {
    throw new TypeError("Widget frame requires a Window-like target");
  }

  let endpoint;
  let disposed = false;

  const reportWindowError = (event) => {
    endpoint?.reportControllerError(
      event?.error ?? event?.reason ?? event?.message ?? "Controller failed",
    );
  };

  const removeRuntimeListeners = () => {
    targetWindow.removeEventListener("error", reportWindowError);
    targetWindow.removeEventListener("unhandledrejection", reportWindowError);
  };

  const acceptPort = (event) => {
    if (disposed || endpoint) return;
    const message = event?.data;
    const offeredPort = event?.ports?.[0];
    if (
      event?.source !== targetWindow.parent
      || targetWindow.parent === targetWindow
      || typeof message !== "object"
      || message === null
      || message.type !== FRAME_PORT_MESSAGE_TYPE
      || message.protocol_version !== FRAME_PROTOCOL_VERSION
      || !validNonce(message.nonce)
      || event.ports.length !== 1
      || !offeredPort
      || typeof offeredPort.postMessage !== "function"
      || typeof offeredPort.close !== "function"
    ) {
      return;
    }

    targetWindow.removeEventListener("message", acceptPort);
    targetWindow.addEventListener("error", reportWindowError);
    targetWindow.addEventListener("unhandledrejection", reportWindowError);
    endpoint = createPortEndpoint(offeredPort, message.nonce, {
      initializeController,
      targetWindow,
      onDisposed: removeRuntimeListeners,
    });
  };

  targetWindow.addEventListener("message", acceptPort);

  return Object.freeze({
    dispose() {
      if (disposed) return;
      disposed = true;
      targetWindow.removeEventListener("message", acceptPort);
      removeRuntimeListeners();
      endpoint?.dispose();
      endpoint = undefined;
    },
  });
}


if (typeof window !== "undefined" && typeof document !== "undefined") {
  installFrameShell(window);
}
