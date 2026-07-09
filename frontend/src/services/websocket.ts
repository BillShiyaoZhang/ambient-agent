class WebSocketService {
  private socket: WebSocket | null = null;
  private onMessageCallback: ((data: any) => void) | null = null;

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
      wsUrl = `${url}?session_id=${sessionIdOrOnMessage}`;
    }

    this.socket = new WebSocket(wsUrl);

    this.socket.onopen = () => {
      console.log("Connected to Ambient Agent WebSocket server.");
    };

    this.socket.onclose = () => {
      console.log("Disconnected from Ambient Agent WebSocket server.");
    };

    this.socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (this.onMessageCallback) {
          this.onMessageCallback(data);
        }
      } catch (err) {
        console.error("Error parsing WebSocket message:", err);
      }
    };

    this.socket.onerror = (error) => {
      console.error("WebSocket error:", error);
    };
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
      this.socket.close();
      this.socket = null;
    }
  }
}

const wsService = new WebSocketService();
export default wsService;
