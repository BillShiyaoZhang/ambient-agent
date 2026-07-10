import re
import os
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.models import ChatSession, ChatMessage, LLMAuditLog
from backend.app_manager import AppManager
from backend.agent.harness import AgentOrchestrator
from backend.opencode_service import run_opencode_agent_acp
from backend.workspace_storage import WorkspaceStorage, migrate_old_data

# Global registry of active WebSockets for broadcasting app data updates
active_websockets = set()
app_manager = AppManager()

# Initialize workspace storage
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "workspace")
db_storage = WorkspaceStorage(WORKSPACE_DIR)

from backend.graph_db import GraphDatabase
graph_db = GraphDatabase(WORKSPACE_DIR)

def get_db():
    yield db_storage

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Perform automated migration from db.sqlite3 and backend/apps to workspace
    migrate_old_data(WORKSPACE_DIR)
    yield

app = FastAPI(title="Ambient Agent API", lifespan=lifespan)

# Allow CORS for local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Ambient Agent is running"}

@app.get("/api/audit-logs")
async def get_audit_logs(session: WorkspaceStorage = Depends(get_db)):
    return session.get_audit_logs()

# --- Multi-Session REST endpoints ---

class SessionCreate(BaseModel):
    id: str
    title: str

@app.get("/api/sessions")
async def get_sessions(session: WorkspaceStorage = Depends(get_db)):
    return session.get_sessions()

@app.post("/api/sessions")
async def create_session(data: SessionCreate, session: WorkspaceStorage = Depends(get_db)):
    db_sess = session.get(ChatSession, data.id)
    if not db_sess:
        db_sess = ChatSession(id=data.id, title=data.title)
        session.add(db_sess)
        session.commit()
    return db_sess

@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, session: WorkspaceStorage = Depends(get_db)):
    return session.get_messages(session_id)

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, session: WorkspaceStorage = Depends(get_db)):
    success = session.delete_session(session_id)
    if success:
        return {"status": "ok"}
    return {"status": "error", "message": "Session not found"}

# --- Canvas Config REST endpoints ---

class CanvasConfig(BaseModel):
    pinned_ids: List[str]
    widget_spans: Dict[str, Any]

@app.get("/api/canvas")
async def get_canvas(session: WorkspaceStorage = Depends(get_db)):
    return session.get_canvas_config()

@app.post("/api/canvas")
async def save_canvas(data: CanvasConfig, session: WorkspaceStorage = Depends(get_db)):
    session.save_canvas_config(data.model_dump())
    return {"status": "ok"}

# --- AppStore REST endpoints ---

@app.get("/api/apps")
async def list_apps():
    return app_manager.list_apps()

@app.get("/api/apps/{app_id}")
async def get_app_files(app_id: str):
    files = app_manager.get_app_files(app_id)
    if files:
        return files
    return {"status": "error", "message": "App not found"}

@app.delete("/api/apps/{app_id}")
async def delete_app(app_id: str):
    success = app_manager.delete_app(app_id)
    if success:
        return {"status": "ok"}
    return {"status": "error", "message": "App not found"}



# --- Graph Mutations endpoint ---

class GraphMutateRequest(BaseModel):
    actions: List[Dict[str, Any]]

@app.post("/api/graph/mutate")
async def mutate_graph(data: GraphMutateRequest):
    try:
        for action in data.actions:
            act_type = action.get("action")
            if act_type == "create_node":
                graph_db.create_node(
                    node_id=action.get("id"),
                    node_type=action.get("type", "Generic"),
                    properties=action.get("properties")
                )
            elif act_type == "update_node_property":
                graph_db.update_node_property(
                    node_id=action.get("id"),
                    properties=action.get("properties")
                )
            elif act_type == "delete_node":
                graph_db.delete_node(node_id=action.get("id"))
            elif act_type == "create_edge":
                graph_db.create_edge(
                    from_id=action.get("from_id"),
                    to_id=action.get("to_id"),
                    edge_type=action.get("type"),
                    properties=action.get("properties")
                )
            elif act_type == "delete_edge":
                graph_db.delete_edge(
                    from_id=action.get("from_id"),
                    to_id=action.get("to_id"),
                    edge_type=action.get("type")
                )
        # Broadcast changes to all websocket subscribers
        from backend.graph_subscription import subscription_manager
        async def send_ws(ws, payload):
            try:
                await ws.send_json(payload)
            except Exception:
                pass
        await subscription_manager.broadcast_updates(graph_db, send_ws)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- WebSocket Chat Handler ---

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, session_id: Optional[str] = None, session: WorkspaceStorage = Depends(get_db)):
    await websocket.accept()
    active_websockets.add(websocket)
    
    if not session_id:
        session_id = "default-session"

    # Ensure session exists in DB
    db_session_obj = session.get(ChatSession, session_id)
    if not db_session_obj:
        db_session_obj = ChatSession(id=session_id, title="Active Chat")
        session.add(db_session_obj)
        session.commit()
        
    orchestrator = AgentOrchestrator(db_session=session, app_manager=app_manager, run_opencode_agent_acp_fn=run_opencode_agent_acp)
    
    # Callback to send incremental updates to client
    async def send_ws_update(data: Any):
        try:
            if isinstance(data, dict):
                await websocket.send_json(data)
            else:
                await websocket.send_json({
                    "type": "reply",
                    "message": {
                        "id": -1,
                        "sender": "agent",
                        "role": "agent",
                        "content": data,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })
        except Exception:
            pass

    async def process_user_message(content_str: str, sender_str: str):
        try:
            # Save user message to database (committed immediately so we get the ID for ack)
            user_msg = ChatMessage(
                session_id=session_id,
                role="user",
                sender=sender_str,
                content=content_str
            )
            session.add(user_msg)
            
            # Update session's updated_at timestamp
            db_session_obj.updated_at = datetime.now(timezone.utc)
            session.add(db_session_obj)
            session.commit()
            session.refresh(user_msg)
            
            # Send acknowledgement back to client
            await websocket.send_json({
                "type": "ack",
                "message": {
                    "id": user_msg.id,
                    "sender": user_msg.sender,
                    "role": user_msg.role,
                    "content": user_msg.content,
                    "timestamp": user_msg.timestamp.isoformat() if user_msg.timestamp else None
                }
            })

            # Delegate execution to orchestrator
            agent_msg, widget_to_send = await orchestrator.handle_message(
                session_id=session_id,
                content=content_str,
                on_update=send_ws_update
            )

            # Send the final agent explanation/execution log back to client
            await websocket.send_json({
                "type": "reply",
                "message": {
                    "id": agent_msg.id,
                    "sender": agent_msg.sender,
                    "role": agent_msg.role,
                    "content": agent_msg.content,
                    "timestamp": agent_msg.timestamp.isoformat() if agent_msg.timestamp else None
                }
            })

            # Send widget creation/update to frontend
            if widget_to_send:
                try:
                    await websocket.send_json({
                        "type": "widget",
                        "widget": widget_to_send
                    })
                except Exception:
                    pass
        except Exception as e:
            print("Error in process_user_message:", e)

    try:
        while True:
            # Receive message from user client
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "permission_response":
                request_id = data.get("request_id")
                approved = data.get("approved", False)
                
                from backend.opencode_service import active_acp_clients
                resolved = False
                for client in active_acp_clients.values():
                    if request_id in client.pending_permissions:
                        client.resolve_permission(request_id, approved)
                        resolved = True
                if not resolved:
                    print(f"Warning: permission request {request_id} not found in active clients.")
            elif msg_type == "graph_subscribe":
                sub_id = data.get("subscription_id")
                query = data.get("query", {})
                from backend.graph_subscription import subscription_manager
                initial_res = subscription_manager.register(websocket, sub_id, query, graph_db)
                await websocket.send_json({
                    "type": "graph_query_update",
                    "subscription_id": sub_id,
                    "data": initial_res
                })
            elif msg_type == "graph_unsubscribe":
                sub_id = data.get("subscription_id")
                from backend.graph_subscription import subscription_manager
                subscription_manager.unregister(websocket, sub_id)
            else:
                sender = data.get("sender", "user")
                content = data.get("content", "")
                # Run the orchestrator logic in a concurrent background task to avoid blocking the WS read loop
                asyncio.create_task(process_user_message(content, sender))
            
    except WebSocketDisconnect:
        pass
    finally:
        active_websockets.discard(websocket)
        from backend.graph_subscription import subscription_manager
        subscription_manager.unregister_all(websocket)
