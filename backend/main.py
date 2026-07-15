import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agent.harness import AgentOrchestrator
from backend.app_manager import AppManager
from backend.models import ChatMessage, ChatSession
from backend.opencode_service import run_opencode_agent_acp
from backend.workspace_storage import WorkspaceStorage, migrate_old_data

# Global registry of active WebSockets mapping session_id -> Set[WebSocket]
active_websockets: dict[str, set[WebSocket]] = {}

# Registry of pending interactive requests: session_id -> dict of request_id -> request_payload
pending_requests: dict[str, dict[str, Any]] = {}

# Registry of latest status updates (Thinking/logs): session_id -> payload dict
latest_session_status: dict[str, Any] = {}

# Set of active session IDs currently running generation tasks
active_running_sessions: set[str] = set()


async def send_to_session(session_id: str, data: Any):
    """Sends JSON data to all active websockets connected to a specific session."""
    sockets = active_websockets.get(session_id, set())
    for ws in list(sockets):
        try:
            await ws.send_json(data)
        except Exception:
            pass


async def broadcast_global(data: Any):
    """Sends JSON data to all connected websockets across all sessions."""
    for sockets in list(active_websockets.values()):
        for ws in list(sockets):
            try:
                await ws.send_json(data)
            except Exception:
                pass


app_manager = AppManager()

# Initialize workspace storage
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "workspace")
db_storage = WorkspaceStorage(WORKSPACE_DIR)

from backend.graph_db import GraphDatabase

graph_db = GraphDatabase(WORKSPACE_DIR)


def get_db():
    yield db_storage


from backend.backend_manager import BackendManager

backend_manager = BackendManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Perform automated migration from db.sqlite3 and backend/apps to workspace
    migrate_old_data(WORKSPACE_DIR)
    yield
    await backend_manager.shutdown()


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
    pinned_ids: list[str]
    widget_spans: dict[str, Any]


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
    try:
        files = app_manager.get_app_files(app_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if files:
        return files
    return {"status": "error", "message": "App not found"}


@app.delete("/api/apps/{app_id}")
async def delete_app(app_id: str):
    try:
        success = app_manager.delete_app(app_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if success:
        return {"status": "ok"}
    return {"status": "error", "message": "App not found"}


# --- Graph Mutations endpoint ---


class GraphMutateRequest(BaseModel):
    actions: list[dict[str, Any]]


@app.post("/api/graph/mutate")
async def mutate_graph(data: GraphMutateRequest):
    try:
        for action in data.actions:
            act_type = action.get("action")
            if act_type == "create_node":
                graph_db.create_node(
                    node_id=action.get("id"),
                    node_type=action.get("type", "Generic"),
                    properties=action.get("properties"),
                )
            elif act_type == "update_node_property":
                graph_db.update_node_property(node_id=action.get("id"), properties=action.get("properties"))
            elif act_type == "delete_node":
                graph_db.delete_node(node_id=action.get("id"))
            elif act_type == "create_edge":
                graph_db.create_edge(
                    from_id=action.get("from_id"),
                    to_id=action.get("to_id"),
                    edge_type=action.get("type"),
                    properties=action.get("properties"),
                )
            elif act_type == "delete_edge":
                graph_db.delete_edge(
                    from_id=action.get("from_id"), to_id=action.get("to_id"), edge_type=action.get("type")
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
async def websocket_chat(
    websocket: WebSocket, session_id: str | None = None, session: WorkspaceStorage = Depends(get_db)
):
    await websocket.accept()

    if not session_id:
        session_id = "default-session"

    # Register websocket session mapping
    if session_id not in active_websockets:
        active_websockets[session_id] = set()
    active_websockets[session_id].add(websocket)

    # Ensure session exists in DB
    db_session_obj = session.get(ChatSession, session_id)
    if not db_session_obj:
        db_session_obj = ChatSession(id=session_id, title="Active Chat")
        session.add(db_session_obj)
        session.commit()

    orchestrator = AgentOrchestrator(
        db_session=session, app_manager=app_manager, run_opencode_agent_acp_fn=run_opencode_agent_acp
    )

    # Callback to send incremental updates to client
    async def send_ws_update(data: Any):
        try:
            if isinstance(data, dict):
                payload = data
                # Capture pending requests
                if data.get("type") in ("schema_approval_request", "permission_request", "plan_approval_request"):
                    req_id = data.get("request_id")
                    if req_id:
                        if session_id not in pending_requests:
                            pending_requests[session_id] = {}
                        pending_requests[session_id][req_id] = data
            else:
                payload = {
                    "type": "reply",
                    "message": {
                        "id": -1,
                        "sender": "agent",
                        "role": "agent",
                        "content": data,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                }
                # Store latest status update
                latest_session_status[session_id] = payload

            await send_to_session(session_id, payload)
        except Exception:
            pass

    async def process_user_message(content_str: str, sender_str: str):
        try:
            # Save user message to database (committed immediately so we get the ID for ack)
            user_msg = ChatMessage(session_id=session_id, role="user", sender=sender_str, content=content_str)
            session.add(user_msg)

            # Update session's updated_at timestamp
            db_session_obj.updated_at = datetime.now(UTC)
            session.add(db_session_obj)
            session.commit()
            session.refresh(user_msg)

            # Send acknowledgement back to client
            await send_to_session(
                session_id,
                {
                    "type": "ack",
                    "message": {
                        "id": user_msg.id,
                        "sender": user_msg.sender,
                        "role": user_msg.role,
                        "content": user_msg.content,
                        "timestamp": user_msg.timestamp.isoformat() if user_msg.timestamp else None,
                    },
                },
            )

            # Mark session as running and broadcast globally
            active_running_sessions.add(session_id)
            await broadcast_global({"type": "session_status_update", "session_id": session_id, "status": "running"})

            # Delegate execution to orchestrator
            agent_msg, widget_to_send = await orchestrator.handle_message(
                session_id=session_id, content=content_str, on_update=send_ws_update
            )

            # Send the final agent explanation/execution log back to client
            await send_to_session(
                session_id,
                {
                    "type": "reply",
                    "message": {
                        "id": agent_msg.id,
                        "sender": agent_msg.sender,
                        "role": agent_msg.role,
                        "content": agent_msg.content,
                        "timestamp": agent_msg.timestamp.isoformat() if agent_msg.timestamp else None,
                    },
                },
            )

            # Send widget creation/update to frontend
            if widget_to_send:
                try:
                    await send_to_session(session_id, {"type": "widget", "widget": widget_to_send})
                except Exception:
                    pass
        except Exception as e:
            print("Error in process_user_message:", e)
        finally:
            # Clean up pending requests, latest status, and active session flag
            pending_requests.pop(session_id, None)
            latest_session_status.pop(session_id, None)
            active_running_sessions.discard(session_id)
            await broadcast_global({"type": "session_status_update", "session_id": session_id, "status": "idle"})

    # Restore connection state for the client
    try:
        # Send all active running sessions
        await websocket.send_json({"type": "active_sessions_list", "active_session_ids": list(active_running_sessions)})

        # Send latest status/logs if session is running
        if session_id in latest_session_status:
            await websocket.send_json(latest_session_status[session_id])

        # Send any pending requests
        if session_id in pending_requests:
            for req_data in pending_requests[session_id].values():
                await websocket.send_json(req_data)
    except Exception:
        pass

    async def run_agent_msg(app_id, manifest, agent_msg_content):
        try:

            async def send_ws_payload(payload):
                await websocket.send_json(payload)

            await backend_manager.handle_agent_message(
                app_id=app_id, manifest=manifest, message=agent_msg_content, send_ws_message_func=send_ws_payload
            )
        except Exception as e:
            await websocket.send_json({"type": "error", "app_id": app_id, "message": str(e)})

    async def run_mcp_call(app_id, manifest, tool_name, arguments, call_id):
        try:

            async def send_ws_payload(payload):
                if payload.get("type") == "backend_permission_request":
                    req_id = payload.get("request_id")
                    if req_id:
                        if session_id not in pending_requests:
                            pending_requests[session_id] = {}
                        pending_requests[session_id][req_id] = payload
                await websocket.send_json(payload)

            client = await backend_manager.get_or_start_mcp_client(
                app_id=app_id, manifest=manifest, send_ws_message_func=send_ws_payload
            )
            if client:
                result = await client.call("tools/call", {"name": tool_name, "arguments": arguments})
                await websocket.send_json(
                    {"type": "mcp_call_response", "app_id": app_id, "call_id": call_id, "result": result}
                )
        except Exception as e:
            await websocket.send_json(
                {"type": "mcp_call_response", "app_id": app_id, "call_id": call_id, "error": str(e)}
            )

    async def run_mcp_read(app_id, manifest, uri, call_id):
        try:

            async def send_ws_payload(payload):
                if payload.get("type") == "backend_permission_request":
                    req_id = payload.get("request_id")
                    if req_id:
                        if session_id not in pending_requests:
                            pending_requests[session_id] = {}
                        pending_requests[session_id][req_id] = payload
                await websocket.send_json(payload)

            client = await backend_manager.get_or_start_mcp_client(
                app_id=app_id, manifest=manifest, send_ws_message_func=send_ws_payload
            )
            if client:
                result = await client.call("resources/read", {"uri": uri})
                await websocket.send_json(
                    {"type": "mcp_read_response", "app_id": app_id, "call_id": call_id, "result": result}
                )
        except Exception as e:
            await websocket.send_json(
                {"type": "mcp_read_response", "app_id": app_id, "call_id": call_id, "error": str(e)}
            )

    try:
        while True:
            # Receive message from user client
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "backend_permission_response":
                request_id = data.get("request_id")
                approved = data.get("approved", False)

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                backend_manager.resolve_permission(request_id, approved)
            elif msg_type == "ag_ui_message":
                app_id = data.get("app_id")
                agent_msg_content = data.get("message", {})
                manifest = app_manager.get_manifest(app_id)
                if manifest:
                    asyncio.create_task(run_agent_msg(app_id, manifest, agent_msg_content))
            elif msg_type == "mcp_call_tool":
                app_id = data.get("app_id")
                tool_name = data.get("name")
                arguments = data.get("arguments", {})
                call_id = data.get("call_id")
                manifest = app_manager.get_manifest(app_id)
                if manifest:
                    asyncio.create_task(run_mcp_call(app_id, manifest, tool_name, arguments, call_id))
            elif msg_type == "mcp_read_resource":
                app_id = data.get("app_id")
                uri = data.get("uri")
                call_id = data.get("call_id")
                manifest = app_manager.get_manifest(app_id)
                if manifest:
                    asyncio.create_task(run_mcp_read(app_id, manifest, uri, call_id))
            elif msg_type == "permission_response":
                request_id = data.get("request_id")
                approved = data.get("approved", False)

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                from backend.opencode_service import active_acp_clients

                resolved = False
                for client in active_acp_clients.values():
                    if request_id in client.pending_permissions:
                        client.resolve_permission(request_id, approved)
                        resolved = True
                if not resolved:
                    print(f"Warning: permission request {request_id} not found in active clients.")
            elif msg_type == "schema_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")
                proposal = data.get("proposal", {})
                feedback = data.get("feedback", "")

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                action = "deny"
                response_data = None

                if approved_status is True or approved_status == "approve":
                    action = "approve"
                    response_data = proposal
                elif approved_status == "refine":
                    action = "refine"
                    response_data = {"feedback": feedback, "proposal": proposal}
                elif approved_status == "rework_plan":
                    action = "rework_plan"
                    response_data = {"feedback": feedback}

                from backend.agent.harness import active_schema_requests

                fut = active_schema_requests.get(request_id)
                if fut and not fut.done():
                    fut.set_result((action, response_data))
            elif msg_type == "plan_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")
                plan = data.get("plan", "")
                feedback = data.get("feedback", "")

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                action = "deny"
                response_data = None

                if approved_status is True or approved_status == "approve":
                    action = "approve"
                    response_data = plan
                elif approved_status == "refine":
                    action = "refine"
                    response_data = {"feedback": feedback, "plan": plan}

                from backend.agent.harness import active_plan_requests

                fut = active_plan_requests.get(request_id)
                if fut and not fut.done():
                    fut.set_result((action, response_data))
            elif msg_type == "verification_approval_response":
                request_id = data.get("request_id")
                approved_status = data.get("approved")  # "approve", "rework_code", "rework_schema", "rework_plan"
                feedback = data.get("feedback", "")

                # Remove from pending_requests
                for sess_id, reqs in list(pending_requests.items()):
                    if request_id in reqs:
                        reqs.pop(request_id)
                        if not reqs:
                            pending_requests.pop(sess_id, None)

                action = approved_status or "approve"
                response_data = {"feedback": feedback}

                from backend.agent.harness import active_verification_requests

                fut = active_verification_requests.get(request_id)
                if fut and not fut.done():
                    fut.set_result((action, response_data))
            elif msg_type == "graph_subscribe":
                sub_id = data.get("subscription_id")
                query = data.get("query", {})
                from backend.graph_subscription import subscription_manager

                initial_res = subscription_manager.register(websocket, sub_id, query, graph_db)
                await websocket.send_json(
                    {"type": "graph_query_update", "subscription_id": sub_id, "data": initial_res}
                )
            elif msg_type == "graph_unsubscribe":
                sub_id = data.get("subscription_id")
                from backend.graph_subscription import subscription_manager

                subscription_manager.unregister(websocket, sub_id)
            elif msg_type == "rollback_mutation":
                ticket_id = data.get("ticket_id")
                if ticket_id:
                    from backend.mutation_tickets import MutationTicketManager

                    mgr = MutationTicketManager(graph_db)
                    reverses = await mgr.rollback(session_id, ticket_id)
                    # Apply inverse actions; report success/failure back
                    applied: list[str] = []
                    errors: list[str] = []
                    for r in reverses:
                        try:
                            act = r.get("action")
                            if act == "update_node_property":
                                graph_db.update_node_property(
                                    node_id=r.get("id"),
                                    properties=r.get("properties", {}),
                                )
                                applied.append(r.get("id", "?"))
                            elif act == "delete_node":
                                graph_db.delete_node(node_id=r.get("id"))
                                applied.append(r.get("id", "?"))
                            elif act == "delete_edge":
                                graph_db.delete_edge(
                                    from_id=r.get("from_id"),
                                    to_id=r.get("to_id"),
                                    edge_type=r.get("type"),
                                )
                                applied.append(f"{r.get('from_id')}->{r.get('to_id')}")
                        except Exception as e:
                            errors.append(f"{r}: {e!s}")

                    async def _send_ws(ws, payload):
                        try:
                            await ws.send_json(payload)
                        except Exception:
                            pass

                    from backend.graph_subscription import subscription_manager as _sub_mgr

                    await _sub_mgr.broadcast_updates(graph_db, _send_ws)
                    await send_to_session(
                        session_id,
                        {
                            "type": "rollback_mutation_response",
                            "ticket_id": ticket_id,
                            "applied": applied,
                            "errors": errors,
                        },
                    )
            elif msg_type == "pin_mutation_history":
                ticket_id = data.get("ticket_id")
                if ticket_id:
                    from backend.mutation_tickets import MutationTicketManager

                    mgr = MutationTicketManager(graph_db)
                    await mgr.pin(session_id, ticket_id)
                    await send_to_session(
                        session_id,
                        {
                            "type": "pin_mutation_response",
                            "ticket_id": ticket_id,
                            "pinned": True,
                        },
                    )
            else:
                sender = data.get("sender", "user")
                content = data.get("content", "")
                # Run the orchestrator logic in a concurrent background task to avoid blocking the WS read loop
                asyncio.create_task(process_user_message(content, sender))

    except WebSocketDisconnect:
        pass
    finally:
        if session_id in active_websockets:
            active_websockets[session_id].discard(websocket)
            if not active_websockets[session_id]:
                active_websockets.pop(session_id, None)
        from backend.graph_subscription import subscription_manager

        subscription_manager.unregister_all(websocket)
