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
from backend.opencode_service import run_opencode_agent, run_opencode_agent_acp

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
            
            # Determine if this is a coding/app modification task
            is_coding = False
            app_id = None
            instruction = ""
            
            # 1. Check for /app command
            app_match = re.match(r"^/app\s+([a-zA-Z0-9_-]+)(?:\s+(.*))?$", content.strip(), re.IGNORECASE)
            if app_match:
                is_coding = True
                app_id = app_match.group(1).strip()
                instruction = app_match.group(2) or "Refactor or inspect the app."
                instruction = instruction.strip()
            else:
                # 2. Check if user mentioned an existing app to modify
                existing_apps = app_manager.list_apps()
                mentioned_app_id = None
                for app_meta in existing_apps:
                    app_id_clean = app_meta["id"]
                    base_name = app_id_clean.split("-")[0]
                    zh_mappings = {
                        "clock": ["时钟", "秒表", "计时器"],
                        "weather": ["天气"],
                        "todo": ["待办", "任务"],
                        "calculator": ["计算器"],
                        "notes": ["笔记", "便签"],
                        "calendar": ["日历"],
                        "chart": ["图表"],
                    }
                    if (app_id_clean in content.lower() or 
                        base_name in content.lower() or 
                        any(term in content for term in zh_mappings.get(base_name, []))):
                        mentioned_app_id = app_id_clean
                        break
                
                if mentioned_app_id:
                    is_coding = True
                    app_id = mentioned_app_id
                    instruction = content.strip()
                else:
                    # 3. Check for keywords indicating a new app creation
                    creation_patterns_en = [
                        r"\b(?:create|build|make|generate|write|develop)\s+(?:a\s+)?(?:new\s+)?(?:widget|app|gui|dashboard)\b",
                        r"\b(?:modify|update|add|change|fix)\s+(?:the\s+)?(?:widget|app|gui)\b"
                    ]
                    
                    verbs = ["创建", "制作", "生成", "开发", "写", "设计", "做", "修改", "更新", "增加", "改变", "修复", "优化", "调整", "改下", "完善", "加上", "添加", "重构"]
                    app_types = ["计算器", "天气", "时钟", "秒表", "计时器", "待办", "任务", "日历", "日程", "笔记", "便签", "图表", "widget", "app", "gui", "应用", "小程序"]
                    
                    has_en_pattern = any(re.search(pat, content, re.IGNORECASE) for pat in creation_patterns_en)
                    has_zh_pattern = any(v in content for v in verbs) and any(a in content for a in app_types)
                    
                    if has_en_pattern or has_zh_pattern:
                        is_coding = True
                        guessed_name = "new-app"
                        for word in ["calculator", "计算器"]:
                            if word in content.lower():
                                guessed_name = "calculator-app"
                                break
                        for word in ["stopwatch", "clock", "timer", "秒表", "时钟", "计时器"]:
                            if word in content.lower():
                                guessed_name = "clock-app"
                                break
                        for word in ["todo", "task", "待办", "任务"]:
                            if word in content.lower():
                                guessed_name = "todo-app"
                                break
                        for word in ["notes", "笔记", "便签"]:
                            if word in content.lower():
                                guessed_name = "notes-app"
                                break
                        for word in ["calendar", "日历"]:
                            if word in content.lower():
                                guessed_name = "calendar-app"
                                break
                        for word in ["chart", "图表"]:
                            if word in content.lower():
                                guessed_name = "chart-app"
                                break
                        for word in ["weather", "天气"]:
                            if word in content.lower():
                                guessed_name = "weather-app"
                                break
                                
                        import uuid
                        suffix = uuid.uuid4().hex[:4]
                        app_id = f"{guessed_name}-{suffix}"
                        instruction = content.strip()

            if is_coding:
                # Send ack/status to client that OpenCode is starting
                status_msg_content = f"🛠️ Starting OpenCode agent to process request for app '{app_id}'...\nThis might take a moment."
                await websocket.send_json({
                    "type": "reply",
                    "message": {
                        "id": -1,  # Special ID for temporary status updates
                        "sender": "agent",
                        "role": "agent",
                        "content": status_msg_content,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })
                
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

                cli_output = await run_opencode_agent_acp(app_id, instruction, on_update=send_ws_update)
                
                # Check if the widget was successfully created/modified on disk
                widget_to_send = app_manager.get_app_files(app_id)
                
                # Save agent execution log to DB
                agent_msg = ChatMessage(
                    session_id=session_id,
                    role="agent",
                    sender="agent",
                    content=f"OpenCode Execution Log:\n\n```\n{cli_output}\n```"
                )
                session.add(agent_msg)
                
                # Save widget metadata/code to database messages for context recovery in future turns
                if widget_to_send:
                    code_msg = ChatMessage(
                        session_id=session_id,
                        role="code",
                        sender="agent",
                        content=(
                            f'<ambient-widget id="{widget_to_send["id"]}" title="{widget_to_send["title"]}">\n'
                            f'<html-content>\n{widget_to_send["html"]}\n</html-content>\n'
                            f'<css-styles>\n{widget_to_send["css"]}\n</css-styles>\n'
                            f'<js-script>\n{widget_to_send["js"]}\n</js-script>\n'
                            f'</ambient-widget>'
                        )
                    )
                    session.add(code_msg)
                    
                    # Ensure metadata.json is updated/synchronized with a nice title
                    title = app_id.replace("-", " ").title()
                    title_match = re.search(r"<title>(.*?)</title>", widget_to_send["html"], re.IGNORECASE)
                    if title_match:
                        title = title_match.group(1).strip()
                    
                    app_manager.create_or_update_app(
                        app_id=widget_to_send["id"],
                        title=title,
                        html=widget_to_send["html"],
                        css=widget_to_send["css"],
                        js=widget_to_send["js"]
                    )
                    
                    # Re-get the updated widget files (with title updated)
                    widget_to_send = app_manager.get_app_files(app_id)
                    
                session.commit()
                session.refresh(agent_msg)
                
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
                    await websocket.send_json({
                        "type": "widget",
                        "widget": widget_to_send
                    })
            else:
                # Conversational path (standard LLM response)
                await websocket.send_json({
                    "type": "reply",
                    "message": {
                        "id": -1,
                        "sender": "agent",
                        "role": "agent",
                        "content": "🤔 Thinking...",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
                })

                llm_prompt_messages = context_manager.build_llm_prompt(session_id)
                llm_prompt_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
                
                raw_response = await generate_agent_response(
                    messages=llm_prompt_messages,
                    provider=provider,
                    model=model,
                    session=session
                )
                
                widget_to_send = parse_widget_from_text(raw_response)
                
                if widget_to_send:
                    reply_content = re.sub(r"<ambient-widget.*?>.*?</ambient-widget>", "", raw_response, flags=re.DOTALL).strip()
                else:
                    reply_content = raw_response

                agent_msg = ChatMessage(
                    session_id=session_id,
                    role="agent",
                    sender="agent",
                    content=reply_content
                )
                session.add(agent_msg)
                
                if widget_to_send:
                    code_msg = ChatMessage(
                        session_id=session_id,
                        role="code",
                        sender="agent",
                        content=raw_response
                    )
                    session.add(code_msg)
                    
                    app_manager.create_or_update_app(
                        app_id=widget_to_send["id"],
                        title=widget_to_send["title"],
                        html=widget_to_send["html"],
                        css=widget_to_send["css"],
                        js=widget_to_send["js"]
                    )
                    
                session.commit()
                session.refresh(agent_msg)
                
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
                
                if widget_to_send:
                    await websocket.send_json({
                        "type": "widget",
                        "widget": widget_to_send
                    })
            
    except WebSocketDisconnect:
        pass
    finally:
        active_websockets.discard(websocket)
