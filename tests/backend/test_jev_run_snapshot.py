from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.main as main
from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.harness import AgentOrchestrator
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.run_context import RunContext
from backend.llm_config import LLMConfigError
from backend.models import ChatSession
from backend.run_service import AgentRunState, RunStore
from backend.workspace_storage import WorkspaceStorage


@pytest.fixture
def coding_settings(monkeypatch):
    store = MagicMock()
    store.get_settings.return_value = {
        "default_agent": "opencode",
        "agent_models": {"opencode": {"mode": "independent"}},
    }
    monkeypatch.setattr(main, "coding_agent_config_store", store)
    return store


def test_run_snapshot_freezes_jev_configuration_across_restart(tmp_path, monkeypatch, coding_settings):
    monkeypatch.setenv("JEV_ROUTER_MODE", "shadow")
    monkeypatch.setenv("JEV_ROUTER_MIN_PROBABILITY", "0.97")
    monkeypatch.setenv("JEV_ROUTER_MIN_MARGIN", "0.2")
    monkeypatch.setenv("JEV_ROUTER_TIMEOUT_SECONDS", "2.0")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-never-persist")
    snapshot = main._snapshot_model_config(ChatSession(id="jev-snapshot", title="Snapshot"))
    state = AgentRunState(session_id="jev-snapshot", model_snapshot=snapshot)
    store = RunStore(str(tmp_path))
    run = store.create_run(
        owner_id="session:jev-snapshot",
        action_id="chat",
        action_title="Chat",
        source_type="chat",
        source_id="jev-snapshot",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "hello"},
        state=state,
    )

    monkeypatch.setenv("JEV_ROUTER_MODE", "cascade")
    monkeypatch.setenv("JEV_ROUTER_MIN_PROBABILITY", "0.99")
    monkeypatch.setenv("TYPESAFE_API_KEY", "rotated-key-never-persist")
    restored = RunStore(str(tmp_path)).get_run(run["id"])
    restored_state = AgentRunState.model_validate(restored["state"])
    context = DurableAgentWorkflow._run_context(restored, restored_state)

    assert context.jev_router == snapshot["jev_router"]
    assert context.jev_router["mode"] == "shadow"
    assert context.jev_router["model"] == "jev-1.13.0"
    assert context.jev_router["timeout_s"] == 2.0
    assert context.jev_router["min_probability"] == 0.97
    assert context.jev_router["min_margin"] == 0.2
    assert context.jev_router["max_state_chars"] == 48000
    assert context.jev_router["criteria_version"]
    assert main._snapshot_model_config(ChatSession(id="new-run", title="New"))["jev_router"]["mode"] == "cascade"
    serialized = json.dumps({"run": restored, "context": context.model_dump(mode="json")})
    assert "test-key-never-persist" not in serialized
    assert "rotated-key-never-persist" not in serialized
    assert "api_key" not in serialized
    assert "TYPESAFE_API_KEY" not in serialized


def test_off_mode_is_frozen_for_new_runs_before_configuration_change(monkeypatch, coding_settings):
    snapshot = main._snapshot_model_config(ChatSession(id="off-run", title="Off"))
    monkeypatch.setenv("JEV_ROUTER_MODE", "cascade")
    state = AgentRunState(session_id="off-run", model_snapshot=snapshot)

    context = DurableAgentWorkflow._run_context({"id": "off-run"}, state)

    assert context.jev_router["mode"] == "off"


def test_legacy_run_remains_off_when_deployment_enables_jev(monkeypatch):
    monkeypatch.setenv("JEV_ROUTER_MODE", "cascade")
    state = AgentRunState(session_id="legacy", model_snapshot={"primary": {}, "fast": {}})

    context = DurableAgentWorkflow._run_context({"id": "legacy"}, state)

    assert context.jev_router == {"mode": "off"}
    assert "jev_router" not in state.model_snapshot


def test_run_context_without_jev_configuration_defaults_to_off():
    context = RunContext(run_id="legacy", session_id="legacy", step_id="route", attempt=1, trace_id="legacy")
    assert context.jev_router == {"mode": "off"}


def test_jev_configuration_is_frozen_without_a_primary_model(monkeypatch):
    store = MagicMock()
    store.get_settings.return_value = {}
    monkeypatch.setattr(main, "llm_config_store", store)
    monkeypatch.setenv("JEV_ROUTER_MODE", "shadow")

    snapshot = main._snapshot_model_config(ChatSession(id="unconfigured", title="No model"))

    assert snapshot["jev_router"]["mode"] == "shadow"
    assert "primary" not in snapshot


def test_invalid_jev_environment_reports_a_safe_configuration_error(monkeypatch):
    monkeypatch.setenv("JEV_ROUTER_MODE", "invalid-secret-marker")

    with pytest.raises(LLMConfigError) as failure:
        main._snapshot_model_config(ChatSession(id="invalid", title="Invalid"))

    assert failure.value.code == "jev_configuration_invalid"
    assert "invalid-secret-marker" not in str(failure.value)


def test_invalid_jev_configuration_is_rejected_before_run_submission(graph_api_client, monkeypatch):
    monkeypatch.setenv("JEV_ROUTER_MODE", "invalid-secret-marker")
    session_id = "invalid-jev-websocket"
    assert graph_api_client.post("/api/sessions", json={"id": session_id, "title": "Invalid Jev"}).status_code == 200
    before = {run["id"] for run in main.run_store.list_runs(limit=500)}

    with graph_api_client.websocket_connect(f"/ws/chat?session_id={session_id}") as websocket:
        assert websocket.receive_json()["type"] == "active_sessions_list"
        websocket.send_json({"sender": "user", "content": "hello"})
        assert websocket.receive_json()["type"] == "ack"
        error = websocket.receive_json()

    assert error == {
        "type": "error",
        "code": "jev_configuration_invalid",
        "message": "Invalid Jev router configuration",
    }
    assert {run["id"] for run in main.run_store.list_runs(limit=500)} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("with_context", [False, True])
async def test_harness_passes_frozen_jev_configuration_to_router(monkeypatch, with_context):
    db = MagicMock(spec=WorkspaceStorage)
    db.get_messages.return_value = []
    app_manager = MagicMock()
    app_manager.list_apps.return_value = []
    graph = MagicMock()
    graph.routing_snapshot.return_value = {
        "type_counts": {},
        "recent_nodes_by_type": {},
        "schema_manifest": [],
        "node_count": 0,
        "edge_count": 0,
    }
    config = {"mode": "shadow", "model": "jev-1.13.0", "min_probability": 0.97}
    context = (
        RunContext(run_id="run", session_id="session", step_id="route", attempt=1, trace_id="run", jev_router=config)
        if with_context
        else None
    )
    route = AsyncMock(return_value=IntentPlan(kind=IntentKind.CONVERSE, instruction="hello"))
    monkeypatch.setattr("backend.agent.harness.IntentRouter.route", route)
    orchestrator = AgentOrchestrator(db, app_manager, graph, run_context=context)

    await orchestrator._classify_intent("hello", session_id="session")

    assert route.await_args.kwargs["jev_config"] == (config if with_context else None)
