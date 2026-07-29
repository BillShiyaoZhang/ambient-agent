class WebSocketService {
  private socket: WebSocket | null = null;
  private onMessageCallback: ((data: any) => void) | null = null;
  private persistentMessages = new Map<string, any>();

  connect(
    url: string,
    sessionIdOrOnMessage: string | ((data: any) => void),
    onMessage?: (data: any) => void
  ) {
    // Close existing socket if open
    if (this.socket) {
      this.socket.close();
    }

    let wsUrl = url;
    if (typeof sessionIdOrOnMessage === "function") {
      this.onMessageCallback = sessionIdOrOnMessage;
    } else {
      this.onMessageCallback = onMessage || null;
      const separator = url.includes("?") ? "&" : "?";
      wsUrl = `${url}${separator}session_id=${encodeURIComponent(sessionIdOrOnMessage)}`;
    }

    const socket = new WebSocket(wsUrl);
    this.socket = socket;

    socket.onopen = () => {
      if (this.socket !== socket) return;
      console.log("Connected to Ambient Agent WebSocket server.");
      this.persistentMessages.forEach((message) => {
        socket.send(JSON.stringify(message));
      });
    };

    socket.onclose = () => {
      if (this.socket !== socket) return;
      console.log("Disconnected from Ambient Agent WebSocket server.");
    };

    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
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
      if (this.socket !== socket) return;
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
      this.socket.send(JSON.stringify(message));
    } else {
      console.error("WebSocket is not connected.");
    }
  }

  isConnected(): boolean {
    return this.socket !== null && this.socket.readyState === WebSocket.OPEN;
  }

  disconnect() {
    if (this.socket) {
      const socket = this.socket;
      this.socket = null;
      socket.close();
    }
  }
}

const wsService = new WebSocketService();
export default wsService;
