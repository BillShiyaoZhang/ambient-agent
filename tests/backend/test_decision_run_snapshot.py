from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import backend.main as main
from backend.agent.decisions import DecisionConfig
from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.agent.run_context import RunContext
from backend.llm_config import LLMConfigError
from backend.models import ChatSession
from backend.run_service import AgentRunState, RunStore


@pytest.fixture
def configured_model_stores(monkeypatch):
    llm_store = MagicMock()
    selection = {"provider_id": "fake", "model_id": "snapshot-model"}
    llm_store.get_settings.return_value = {"default_model": selection, "fast_model": selection}
    coding_store = MagicMock()
    coding_store.get_settings.return_value = {
        "default_agent": "opencode",
        "agent_models": {"opencode": {"mode": "independent"}},
    }
    monkeypatch.setattr(main, "llm_config_store", llm_store)
    monkeypatch.setattr(main, "coding_agent_config_store", coding_store)
    return llm_store


def test_run_snapshot_freezes_all_workflow_decision_settings_across_restart(
    tmp_path, monkeypatch, configured_model_stores
):
    settings = {
        "JEV_DECISION_MODE": "shadow",
        "JEV_DECISION_STAGE_MODES": '{"intent_parameters":"cascade","schema_selection":"off"}',
        "JEV_DECISION_MODEL": "jev-1.13.0",
        "JEV_DECISION_TIMEOUT_SECONDS": "2.25",
        "JEV_DECISION_MIN_PROBABILITY": "0.97",
        "JEV_DECISION_MIN_MARGIN": "0.25",
        "JEV_DECISION_MAX_STATE_CHARS": "9000",
        "JEV_DECISION_MAX_CANDIDATES": "32",
        "JEV_DECISION_MAX_QUESTIONS": "40",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TYPESAFE_API_KEY", "snapshot-original-key-never-persist")
    snapshot = main._snapshot_model_config(ChatSession(id="decision-snapshot", title="Snapshot"))
    expected = DecisionConfig(
        mode="shadow",
        stage_modes={"intent_parameters": "cascade", "schema_selection": "off"},
        timeout_s=2.25,
        min_probability=0.97,
        min_margin=0.25,
        max_state_chars=9000,
        max_candidates=32,
        max_questions=40,
    ).snapshot()
    assert snapshot["workflow_decisions"] == expected
    store = RunStore(str(tmp_path))
    run = store.create_run(
        owner_id="session:decision-snapshot",
        action_id="chat",
        action_title="Chat",
        source_type="chat",
        source_id="decision-snapshot",
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": "Create a task App"},
        state=AgentRunState(session_id="decision-snapshot", model_snapshot=snapshot),
    )

    for name in settings:
        monkeypatch.delenv(name)
    monkeypatch.setenv("JEV_DECISION_MODE", "cascade")
    monkeypatch.setenv("TYPESAFE_API_KEY", "snapshot-rotated-key-never-persist")
    restored = RunStore(str(tmp_path)).get_run(run["id"])
    restored_state = AgentRunState.model_validate(restored["state"])
    context = DurableAgentWorkflow._run_context(restored, restored_state)

    assert context.workflow_decisions == expected
    assert context.workflow_decisions is not restored_state.model_snapshot["workflow_decisions"]
    assert (
        main._snapshot_model_config(ChatSession(id="future-run", title="New"))["workflow_decisions"]["mode"]
        == "cascade"
    )
    serialized = json.dumps({"run": restored, "context": context.model_dump(mode="json")})
    assert "snapshot-original-key-never-persist" not in serialized
    assert "snapshot-rotated-key-never-persist" not in serialized
    assert "TYPESAFE_API_KEY" not in serialized
    assert "api_key" not in serialized
    assert "workflow_decisions" not in context.audit_context()


def test_off_workflow_decisions_are_frozen_before_deployment_enables_them(monkeypatch, configured_model_stores):
    snapshot = main._snapshot_model_config(ChatSession(id="off", title="Off"))
    monkeypatch.setenv("JEV_DECISION_MODE", "cascade")
    context = DurableAgentWorkflow._run_context({"id": "off"}, AgentRunState(model_snapshot=snapshot))

    assert context.workflow_decisions == DecisionConfig(mode="off").snapshot()


def test_legacy_workflow_context_stays_off_without_mutating_historical_snapshot(monkeypatch):
    monkeypatch.setenv("JEV_DECISION_MODE", "cascade")
    state = AgentRunState(model_snapshot={"primary": {}, "fast": {}})

    context = DurableAgentWorkflow._run_context({"id": "legacy"}, state)

    assert context.workflow_decisions == {"mode": "off"}
    assert "workflow_decisions" not in state.model_snapshot


def test_new_context_without_decision_snapshot_defaults_off():
    context = RunContext(run_id="run", session_id="session", step_id="plan", attempt=1, trace_id="trace")
    assert context.workflow_decisions == {"mode": "off"}


def test_decision_configuration_is_frozen_without_a_generation_model(monkeypatch, configured_model_stores):
    configured_model_stores.get_settings.return_value = {}
    monkeypatch.setenv("JEV_DECISION_MODE", "shadow")

    snapshot = main._snapshot_model_config(ChatSession(id="unconfigured", title="No model"))

    assert "primary" not in snapshot
    assert snapshot["workflow_decisions"] == DecisionConfig(mode="shadow").snapshot()


@pytest.mark.parametrize(
    "name,value",
    [
        ("JEV_DECISION_MODE", "invalid-secret-marker"),
        ("JEV_DECISION_TIMEOUT_SECONDS", "nan"),
        ("JEV_DECISION_MAX_CANDIDATES", "0"),
    ],
)
def test_invalid_decision_configuration_is_a_safe_error(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(LLMConfigError) as failure:
        main._snapshot_model_config(ChatSession(id="invalid", title="Invalid"))

    assert failure.value.code == "jev_configuration_invalid"
    assert "invalid-secret-marker" not in str(failure.value)
