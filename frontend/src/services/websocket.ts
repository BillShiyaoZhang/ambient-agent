import {
  isNormalSocketClose,
  MAX_SOCKET_RECONNECT_ATTEMPTS,
  socketReconnectDelay,
  type SocketConnectionState,
} from "./socketReconnect";

class WebSocketService {
  private socket: WebSocket | null = null;
  private onMessageCallback: ((data: any) => void) | null = null;
  private persistentMessages = new Map<string, any>();
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private generation = 0;
  private currentUrl: string | null = null;
  private connectionState: SocketConnectionState = "disconnected";
  private statusListeners = new Set<(state: SocketConnectionState) => void>();

  private setConnectionState(state: SocketConnectionState): void {
    if (state === this.connectionState) return;
    this.connectionState = state;
    this.statusListeners.forEach((listener) => listener(state));
  }

  connect(
    url: string,
    sessionIdOrOnMessage: string | ((data: any) => void),
    onMessage?: (data: any) => void
  ) {
    const generation = ++this.generation;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    const previousSocket = this.socket;
    this.socket = null;
    previousSocket?.close();

    let wsUrl = url;
    if (typeof sessionIdOrOnMessage === "function") {
      this.onMessageCallback = sessionIdOrOnMessage;
    } else {
      this.onMessageCallback = onMessage || null;
      const separator = url.includes("?") ? "&" : "?";
      wsUrl = `${url}${separator}session_id=${encodeURIComponent(sessionIdOrOnMessage)}`;
    }

    this.currentUrl = wsUrl;
    this.reconnectAttempt = 0;
    this.setConnectionState("connecting");
    this.openSocket(wsUrl, generation);
  }

  private openSocket(wsUrl: string, generation: number) {
    if (generation !== this.generation) return;
    const socket = new WebSocket(wsUrl);
    this.socket = socket;

    socket.onopen = () => {
      if (this.socket !== socket || generation !== this.generation) return;
      this.reconnectAttempt = 0;
      this.setConnectionState("connected");
      console.log("Connected to Ambient Agent WebSocket server.");
      this.persistentMessages.forEach((message) => {
        try {
          socket.send(JSON.stringify(message));
        } catch (error) {
          console.error("Unable to restore a persistent WebSocket message:", error);
        }
      });
    };

    socket.onclose = (event) => {
      if (this.socket !== socket || generation !== this.generation) return;
      this.socket = null;
      console.log("Disconnected from Ambient Agent WebSocket server.");
      if (isNormalSocketClose(event)) {
        this.setConnectionState("disconnected");
        return;
      }
      if (this.reconnectAttempt >= MAX_SOCKET_RECONNECT_ATTEMPTS) {
        this.setConnectionState("unavailable");
        return;
      }
      this.reconnectAttempt += 1;
      this.setConnectionState("retrying");
      this.reconnectTimer = window.setTimeout(() => {
        this.reconnectTimer = null;
        this.openSocket(wsUrl, generation);
      }, socketReconnectDelay(this.reconnectAttempt));
    };

    socket.onmessage = (event) => {
      if (this.socket !== socket || generation !== this.generation) return;
      try {
        const data = JSON.parse(event.data);
        if (this.onMessageCallback) {
          this.onMessageCallback(data);
        }
      } catch (err) {
        console.error("Error parsing WebSocket message:", err);
      }
    };

    socket.onerror = (error) => {
      if (this.socket !== socket || generation !== this.generation) return;
      console.error("WebSocket error:", error);
    };
  }

  registerPersistentMessage(key: string, message: any) {
    this.persistentMessages.set(key, message);
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    }
  }

  unregisterPersistentMessage(key: string, finalMessage?: any) {
    const existed = this.persistentMessages.delete(key);
    if (existed && finalMessage && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(finalMessage));
    }
  }

  sendMessage(message: any) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify(message));
        return true;
      } catch (error) {
        console.error("Unable to send WebSocket message:", error);
        return false;
      }
    }
    console.error("WebSocket is not connected.");
    return false;
  }

  isConnected(): boolean {
    return this.socket !== null && this.socket.readyState === WebSocket.OPEN;
  }

  getConnectionState(): SocketConnectionState {
    return this.connectionState;
  }

  subscribeStatus(listener: (state: SocketConnectionState) => void): () => void {
    this.statusListeners.add(listener);
    listener(this.connectionState);
    return () => this.statusListeners.delete(listener);
  }

  retry(): boolean {
    if (!this.currentUrl || this.connectionState === "connected") return false;
    const generation = ++this.generation;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    const previousSocket = this.socket;
    this.socket = null;
    previousSocket?.close();
    this.reconnectAttempt = 0;
    this.setConnectionState("connecting");
    this.openSocket(this.currentUrl, generation);
    return true;
  }

  disconnect() {
    this.generation += 1;
    if (this.reconnectTimer !== null) window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.reconnectAttempt = 0;
    this.currentUrl = null;
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    this.setConnectionState("disconnected");
  }
}

const wsService = new WebSocketService();
export default wsService;
