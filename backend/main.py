import re
import os
from dotenv import load_dotenv
load_dotenv()
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, create_engine, Session, select
from backend.models import ChatMessage, LLMAuditLog
from backend.agent_parser import parse_widget_from_text
from backend.llm_service import generate_agent_response

DATABASE_URL = "sqlite:///./db.sqlite3"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

def get_db():
    with Session(engine) as session:
        yield session

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create database tables if they don't exist
    SQLModel.metadata.create_all(engine)
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

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket, session: Session = Depends(get_db)):
    await websocket.accept()
    
    # Retrieve LLM settings from environment
    provider = os.getenv("LLM_PROVIDER", "ollama")
    model = os.getenv("LLM_MODEL", "llama3")
    
    try:
        while True:
            # Receive message from user client
            data = await websocket.receive_json()
            sender = data.get("sender", "user")
            content = data.get("content", "")
            
            # Save user message to database
            user_msg = ChatMessage(sender=sender, content=content)
            session.add(user_msg)
            session.commit()
            session.refresh(user_msg)
            
            # Send acknowledgement back to client
            await websocket.send_json({
                "type": "ack",
                "message": {
                    "id": user_msg.id,
                    "sender": user_msg.sender,
                    "content": user_msg.content,
                    "timestamp": user_msg.timestamp.isoformat() if user_msg.timestamp else None
                }
            })
            
            # Call the LLM service to generate raw reply containing potential widgets
            raw_response = await generate_agent_response(
                user_message=content,
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

            # Save agent reply to database
            agent_msg = ChatMessage(sender="agent", content=reply_content)
            session.add(agent_msg)
            session.commit()
            session.refresh(agent_msg)
            
            # Send agent reply back to client
            await websocket.send_json({
                "type": "reply",
                "message": {
                    "id": agent_msg.id,
                    "sender": agent_msg.sender,
                    "content": agent_msg.content,
                    "timestamp": agent_msg.timestamp.isoformat() if agent_msg.timestamp else None
                }
            })
            
            # Send widget if triggered
            if widget_to_send:
                await websocket.send_json({
                    "type": "widget",
                    "widget": widget_to_send
                })
            
    except WebSocketDisconnect:
        pass
