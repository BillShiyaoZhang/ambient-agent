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
from backend.agent_parser import parse_widget_from_text
from backend.llm_service import generate_agent_response, SYSTEM_PROMPT
from backend.app_manager import AppManager
from backend.context_manager import ContextManager

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
    
    # 2. Simple SQLite migration check & default session migration
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
                
            # Ensure we have a default session in chatsession table
            cursor.execute("SELECT id FROM chatsession WHERE id = 'default-session'")
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO chatsession (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("default-session", "Migrated Chat", datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat())
                )
                conn.commit()
                
            # Migrate any messages that have NULL session_id to default-session
            cursor.execute("UPDATE chatmessage SET session_id = 'default-session' WHERE session_id IS NULL")
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

    # Retrieve LLM settings from environment
    provider = os.getenv("LLM_PROVIDER", "ollama")
    model = os.getenv("LLM_MODEL", "llama3")
    
    # Ensure session exists in DB
    db_session_obj = session.get(ChatSession, session_id)
    if not db_session_obj:
        db_session_obj = ChatSession(id=session_id, title="Active Chat")
        session.add(db_session_obj)
        session.commit()
        session.refresh(db_session_obj)
        
    context_manager = ContextManager(db_session=session, app_manager=app_manager)
    
    try:
        while True:
            # Receive message from user client
            data = await websocket.receive_json()
            sender = data.get("sender", "user")
            content = data.get("content", "")
            
            # Save user message to database
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
            
            # Construct LLM prompt using ContextManager (pruning old codes and injecting disk app codes)
            llm_prompt_messages = context_manager.build_llm_prompt(session_id)
            # Prepend standard system prompt
            llm_prompt_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
            
            # Call the LLM service to generate raw reply containing potential widgets
            raw_response = await generate_agent_response(
                messages=llm_prompt_messages,
                provider=provider,
                model=model,
                session=session
            )
            
            # Parse widget from response text if present
            widget_to_send = parse_widget_from_text(raw_response)
            
            # Clean response text by removing XML block
            if widget_to_send:
                reply_content = re.sub(r"<ambient-widget.*?>.*?</ambient-widget>", "", raw_response, flags=re.DOTALL).strip()
            else:
                reply_content = raw_response

            # Save agent conversational reply to database
            agent_msg = ChatMessage(
                session_id=session_id,
                role="agent",
                sender="agent",
                content=reply_content
            )
            session.add(agent_msg)
            
            # Save widget code block separately if triggered, and update physical files on disk
            if widget_to_send:
                code_msg = ChatMessage(
                    session_id=session_id,
                    role="code",
                    sender="agent",
                    content=raw_response
                )
                session.add(code_msg)
                
                # Write app files to disk
                app_manager.create_or_update_app(
                    app_id=widget_to_send["id"],
                    title=widget_to_send["title"],
                    html=widget_to_send["html"],
                    css=widget_to_send["css"],
                    js=widget_to_send["js"]
                )
                
            session.commit()
            session.refresh(agent_msg)
            
            # Send agent reply back to client
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
            
            # Send widget message to frontend if triggered
            if widget_to_send:
                await websocket.send_json({
                    "type": "widget",
                    "widget": widget_to_send
                })
            
    except WebSocketDisconnect:
        pass
    finally:
        active_websockets.discard(websocket)
