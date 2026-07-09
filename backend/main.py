import re
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import SQLModel, create_engine, Session, select

from backend.models import ChatSession, ChatMessage, LLMAuditLog
from backend.app_manager import AppManager
from backend.agent.harness import AgentOrchestrator
from backend.opencode_service import run_opencode_agent_acp

DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Global registry of active WebSockets for broadcasting app data updates
active_websockets = set()
app_manager = AppManager()

def get_db():
    with Session(engine) as session:
        yield session

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Create database tables if they don't exist
    SQLModel.metadata.create_all(engine)
    
    # 2. Simple SQLite database checks & schema syncs
    import sqlite3
    db_path = "./db.sqlite3"
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            # Check columns in chatmessage
            cursor.execute("PRAGMA table_info(chatmessage)")
            columns = [col[1] for col in cursor.fetchall()]
            
            # Add session_id column if not exists
            if "session_id" not in columns:
                cursor.execute("ALTER TABLE chatmessage ADD COLUMN session_id VARCHAR;")
                conn.commit()
                
            # Add role column if not exists
            if "role" not in columns:
                cursor.execute("ALTER TABLE chatmessage ADD COLUMN role VARCHAR DEFAULT 'user';")
                conn.commit()
                
            # Migrate role for existing agent messages
            cursor.execute("UPDATE chatmessage SET role = 'agent' WHERE sender = 'agent' AND (role = 'user' OR role IS NULL)")
            
            # Purge any corrupted sessions (e.g. callback strings from previous frontend bugs)
            cursor.execute("SELECT id FROM chatsession")
            sessions_in_db = cursor.fetchall()
            for (s_id,) in sessions_in_db:
                if any(x in s_id for x in ("=>", "function", "{", "(")):
                    cursor.execute("DELETE FROM chatmessage WHERE session_id = ?", (s_id,))
                    cursor.execute("DELETE FROM chatsession WHERE id = ?", (s_id,))
            conn.commit()
        except Exception as e:
            print("Migration error:", e)
        finally:
            conn.close()
            
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
async def get_audit_logs(session: Session = Depends(get_db)):
    statement = select(LLMAuditLog).order_by(LLMAuditLog.timestamp.desc())
    results = session.exec(statement).all()
    return results

# --- Multi-Session REST endpoints ---

class SessionCreate(BaseModel):
    id: str
    title: str

@app.get("/api/sessions")
async def get_sessions(session: Session = Depends(get_db)):
    statement = select(ChatSession).order_by(ChatSession.updated_at.desc())
    return session.exec(statement).all()

@app.post("/api/sessions")
async def create_session(data: SessionCreate, session: Session = Depends(get_db)):
    db_sess = session.get(ChatSession, data.id)
    if not db_sess:
        db_sess = ChatSession(id=data.id, title=data.title)
        session.add(db_sess)
        session.commit()
        session.refresh(db_sess)
    return db_sess

@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, session: Session = Depends(get_db)):
    statement = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.timestamp.asc())
    return session.exec(statement).all()

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, session: Session = Depends(get_db)):
    db_sess = session.get(ChatSession, session_id)
    if db_sess:
        # Delete associated messages
        msgs_statement = select(ChatMessage).where(ChatMessage.session_id == session_id)
        msgs = session.exec(msgs_statement).all()
        for m in msgs:
            session.delete(m)
        session.delete(db_sess)
        session.commit()
        return {"status": "ok"}
    return {"status": "error", "message": "Session not found"}

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

@app.get("/api/apps/{app_id}/data")
async def get_app_data(app_id: str):
    return app_manager.get_app_data(app_id)

@app.post("/api/apps/{app_id}/data")
async def save_app_data(app_id: str, data: Dict[str, Any]):
    app_manager.save_app_data(app_id, data)
    # Broadcast data update to all active web socket connections for real-time synchronization
    for ws in list(active_websockets):
        try:
            await ws.send_json({
                "type": "app_data_update",
                "app_id": app_id,
                "data": data
            })
        except Exception:
            pass
    return {"status": "ok"}

# --- WebSocket Chat Handler ---

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, session_id: Optional[str] = None, session: Session = Depends(get_db)):
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
        session.refresh(db_session_obj)
        
    orchestrator = AgentOrchestrator(db_session=session, app_manager=app_manager, run_opencode_agent_acp_fn=run_opencode_agent_acp)
    
    try:
        while True:
            # Receive message from user client
            data = await websocket.receive_json()
            sender = data.get("sender", "user")
            content = data.get("content", "")
            
            # Save user message to database (committed immediately so we get the ID for ack)
            user_msg = ChatMessage(
                session_id=session_id,
                role="user",
                sender=sender,
                content=content
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
            
            # Callback to send incremental updates to client
            async def send_ws_update(text: str):
                try:
                    await websocket.send_json({
                        "type": "reply",
                        "message": {
                            "id": -1,
                            "sender": "agent",
                            "role": "agent",
                            "content": text,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                    })
                except Exception:
                    pass

            # Delegate execution to orchestrator
            agent_msg, widget_to_send = await orchestrator.handle_message(
                session_id=session_id,
                content=content,
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
            
    except WebSocketDisconnect:
        pass
    finally:
        active_websockets.discard(websocket)
