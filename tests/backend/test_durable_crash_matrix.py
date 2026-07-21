import multiprocessing
import os
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agent.durable_workflow import DurableAgentWorkflow
from backend.graph_db import GraphDatabase
from backend.run_service import (
    AgentRunState,
    Continue,
    RunStore,
    StaleLeaseError,
    StepOutcomeValue,
    Succeeded,
)


PHASE_CRASH_MATRIX: list[Any] = [
    pytest.param("route", Continue(next_phase="converse"), "queued", "converse", id="converse-route"),
    pytest.param(
        "converse",
        Succeeded(result={"message": "done"}),
        "succeeded",
        "done",
        id="converse-tool-loop",
    ),
    pytest.param(
        "graph_preflight",
        Continue(next_phase="wait_graph_approval"),
        "queued",
        "wait_graph_approval",
        id="graph-preflight",
    ),
    pytest.param(
        "wait_graph_approval",
        Continue(next_phase="graph_commit"),
        "queued",
        "graph_commit",
        id="graph-wait-approval",
    ),
    pytest.param(
        "graph_commit",
        Succeeded(result={"ticket_id": "ticket-1"}),
        "succeeded",
        "done",
        id="graph-atomic-commit",
    ),
    pytest.param("plan", Continue(next_phase="wait_plan"), "queued", "wait_plan", id="widget-plan"),
    pytest.param(
        "wait_plan",
        Continue(next_phase="align_schema"),
        "queued",
        "align_schema",
        id="widget-wait-plan",
    ),
    pytest.param(
        "align_schema",
        Continue(next_phase="wait_schema"),
        "queued",
        "wait_schema",
        id="widget-align-schema",
    ),
    pytest.param(
        "wait_schema",
        Continue(next_phase="stage_code"),
        "queued",
        "stage_code",
        id="widget-wait-schema",
    ),
    pytest.param(
        "stage_code",
        Continue(next_phase="verify"),
        "queued",
        "verify",
        id="widget-stage-code",
    ),
    pytest.param(
        "verify",
        Continue(next_phase="promote"),
        "queued",
        "promote",
        id="widget-verify",
    ),
    pytest.param(
        "wait_override",
        Continue(next_phase="stage_code", summary="rework"),
        "queued",
        "stage_code",
        id="widget-rework",
    ),
    pytest.param(
        "promote",
        Succeeded(result={"app_id": "durable-app"}),
        "succeeded",
        "done",
        id="widget-promote",
    ),
]


def _abrupt_phase_worker(workspace_dir: str) -> None:
    store = RunStore(workspace_dir)
    claimed = store.claim_next("killed-worker", global_limit=1, owner_limit=1, lease_seconds=-1)
    if claimed is None:
        os._exit(41)
    state = AgentRunState.model_validate(claimed["state"])
    attempt = store.begin_step_attempt(
        claimed["id"],
        state.phase,
        lease_owner="killed-worker",
        lease_epoch=claimed["lease_epoch"],
    )
    os._exit(17 if attempt == 1 else 42)


def _abrupt_after_graph_effect_worker(workspace_dir: str, run_id: str) -> None:
    store = RunStore(workspace_dir)
    claimed = store.claim_next("effect-worker", global_limit=1, owner_limit=1, lease_seconds=-1)
    if claimed is None or claimed["id"] != run_id:
        os._exit(51)
    state = AgentRunState.model_validate(claimed["state"])
    attempt = store.begin_step_attempt(
        run_id,
        "graph_commit",
        lease_owner="effect-worker",
        lease_epoch=claimed["lease_epoch"],
    )
    if attempt != 1:
        os._exit(52)
    graph_db = GraphDatabase(workspace_dir)
    graph_db.apply_actions_atomic(
        state.data["graph_actions"],
        session_id=state.session_id,
        idempotency_key=f"agent-run:{run_id}:graph_commit:0",
    )
    # Simulate SIGKILL in the exact external-effect/checkpoint gap.
    os._exit(23)


def _create_checkpointed_run(store: RunStore, phase: str) -> dict[str, Any]:
    """Commit a real reducer checkpoint whose next phase is the matrix target."""

    session_id = f"session-{phase}"
    state = AgentRunState(
        workflow_type="agent_chat",
        workflow_version=2,
        session_id=session_id,
        phase="seed_checkpoint",
        data={"checkpoint_marker": phase},
    )
    run = store.create_run(
        owner_id=f"ambient-agent:{session_id}",
        action_id="chat",
        action_title="Crash matrix",
        source_type="chat",
        source_id=session_id,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"content": f"exercise {phase}"},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )
    claimed = store.claim_next("checkpoint-worker", global_limit=4, owner_limit=4)
    assert claimed is not None and claimed["id"] == run["id"]
    attempt = store.begin_step_attempt(
        run["id"],
        "seed_checkpoint",
        lease_owner="checkpoint-worker",
        lease_epoch=claimed["lease_epoch"],
    )
    assert attempt == 1
    store.commit_step(
        run["id"],
        "seed_checkpoint",
        attempt=attempt,
        lease_owner="checkpoint-worker",
        lease_epoch=claimed["lease_epoch"],
        state=state,
        outcome=Continue(next_phase=phase, summary=f"checkpoint before {phase}"),
    )
    checkpointed = store.get_run(run["id"])
    assert checkpointed is not None
    assert checkpointed["status"] == "queued"
    assert checkpointed["state"]["phase"] == phase
    assert checkpointed["checkpoint"]["last_step"] == "seed_checkpoint"
    assert checkpointed["checkpoint"]["state"]["phase"] == phase
    return checkpointed


@pytest.mark.parametrize(
    ("phase", "new_outcome", "expected_status", "expected_phase"),
    PHASE_CRASH_MATRIX,
)
def test_each_durable_phase_restarts_from_last_checkpoint_and_fences_old_worker(
    tmp_path,
    phase: str,
    new_outcome: StepOutcomeValue,
    expected_status: str,
    expected_phase: str,
):
    original_store = RunStore(str(tmp_path))
    checkpointed = _create_checkpointed_run(original_store, phase)
    run_id = checkpointed["id"]

    # The first phase worker starts from the committed checkpoint, then loses
    # its lease before it can commit its in-memory state.
    old_claim = original_store.claim_next(
        "old-worker",
        global_limit=4,
        owner_limit=4,
        lease_seconds=-1,
    )
    assert old_claim is not None and old_claim["id"] == run_id
    assert old_claim["state"]["phase"] == phase
    old_attempt = original_store.begin_step_attempt(
        run_id,
        phase,
        lease_owner="old-worker",
        lease_epoch=old_claim["lease_epoch"],
    )
    assert old_attempt == 1
    stale_state = AgentRunState.model_validate(old_claim["state"])
    stale_state.data["uncommitted_worker"] = "old-worker"

    # Opening a fresh store models process restart. The periodic lease reaper
    # returns restart-safe work to the queue without advancing its checkpoint.
    restarted_store = RunStore(str(tmp_path))
    assert restarted_store.recover_orphaned("new-worker") == 1
    recovered = restarted_store.get_run(run_id)
    assert recovered is not None
    assert recovered["status"] == "queued"
    assert recovered["state"]["phase"] == phase
    assert recovered["checkpoint"]["last_step"] == "seed_checkpoint"
    assert "uncommitted_worker" not in recovered["state"]["data"]
    assert recovered["steps"][-1]["status"] == "interrupted"

    new_claim = restarted_store.claim_next("new-worker", global_limit=4, owner_limit=4)
    assert new_claim is not None and new_claim["id"] == run_id
    assert new_claim["lease_epoch"] > old_claim["lease_epoch"]
    assert new_claim["state"]["phase"] == phase
    new_attempt = restarted_store.begin_step_attempt(
        run_id,
        phase,
        lease_owner="new-worker",
        lease_epoch=new_claim["lease_epoch"],
    )
    assert new_attempt == old_attempt + 1

    # A late callback from the dead worker cannot overwrite state, checkpoint,
    # step attempt, or terminal status after the new lease epoch exists.
    with pytest.raises(StaleLeaseError, match="no longer owns active run"):
        original_store.commit_step(
            run_id,
            phase,
            attempt=old_attempt,
            lease_owner="old-worker",
            lease_epoch=old_claim["lease_epoch"],
            state=stale_state,
            outcome=Continue(next_phase="stale-worker-corruption"),
        )

    still_owned_by_new_worker = restarted_store.get_run(run_id)
    assert still_owned_by_new_worker is not None
    assert still_owned_by_new_worker["status"] == "running"
    assert still_owned_by_new_worker["state"]["phase"] == phase
    assert "uncommitted_worker" not in still_owned_by_new_worker["state"]["data"]

    resumed_state = AgentRunState.model_validate(new_claim["state"])
    resumed_state.attempt = new_attempt
    resumed_state.data["resumed_worker"] = "new-worker"
    restarted_store.commit_step(
        run_id,
        phase,
        attempt=new_attempt,
        lease_owner="new-worker",
        lease_epoch=new_claim["lease_epoch"],
        state=resumed_state,
        outcome=new_outcome,
    )

    completed = restarted_store.get_run(run_id, include_events=True)
    assert completed is not None
    assert completed["status"] == expected_status
    assert completed["state"]["phase"] == expected_phase
    assert completed["state"]["data"]["resumed_worker"] == "new-worker"
    assert "uncommitted_worker" not in completed["state"]["data"]
    assert completed["checkpoint"]["last_step"] == phase
    assert completed["checkpoint"]["attempt"] == new_attempt
    phase_attempts = [step for step in completed["steps"] if step["step_key"] == phase]
    assert [(step["attempt"], step["status"]) for step in phase_attempts] == [
        (old_attempt, "interrupted"),
        (new_attempt, "succeeded"),
    ]


def test_abrupt_process_exit_is_reaped_from_last_sqlite_checkpoint(tmp_path):
    store = RunStore(str(tmp_path))
    checkpointed = _create_checkpointed_run(store, "graph_commit")

    process = multiprocessing.get_context("spawn").Process(
        target=_abrupt_phase_worker,
        args=(str(tmp_path),),
    )
    process.start()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        raise AssertionError("abrupt worker did not exit")
    assert process.exitcode == 17

    restarted = RunStore(str(tmp_path))
    killed = restarted.get_run(checkpointed["id"])
    assert killed is not None
    assert killed["status"] == "running"
    assert killed["state"]["phase"] == "graph_commit"
    assert restarted.recover_orphaned("replacement-worker") == 1
    recovered = restarted.get_run(checkpointed["id"])
    assert recovered is not None
    assert recovered["status"] == "queued"
    assert recovered["state"]["phase"] == "graph_commit"
    assert recovered["checkpoint"]["last_step"] == "seed_checkpoint"
    assert recovered["steps"][-1]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_graph_effect_committed_before_process_death_replays_exactly_once(tmp_path):
    workspace_dir = str(tmp_path)
    store = RunStore(workspace_dir)
    action = {
        "action": "create_node",
        "id": "crash-window-node",
        "type": "Task",
        "properties": {"title": "exactly once"},
    }
    state = AgentRunState(
        workflow_type="graph_mutation",
        workflow_version=DurableAgentWorkflow.VERSION,
        session_id="crash-effect-session",
        phase="graph_commit",
        data={"graph_actions": [action], "language": "en"},
    )
    run = store.create_run(
        owner_id="graph:crash-effect-session",
        action_id="mutate",
        action_title="Crash-window graph mutation",
        source_type="api",
        source_id=state.session_id,
        adapter_type="internal_agent",
        runtime_id="internal:agent",
        input_data={"actions": [action]},
        recovery="restart_safe",
        state=state,
        workflow_type=state.workflow_type,
        workflow_version=state.workflow_version,
    )

    process = multiprocessing.get_context("spawn").Process(
        target=_abrupt_after_graph_effect_worker,
        args=(workspace_dir, run["id"]),
    )
    process.start()
    process.join(timeout=10)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        raise AssertionError("effect worker did not exit")
    assert process.exitcode == 23

    graph_db = GraphDatabase(workspace_dir)
    assert graph_db.get_node("crash-window-node") is not None
    with sqlite3.connect(graph_db.db_path) as connection:
        first_effect = connection.execute(
            "SELECT result_json FROM graph_effects WHERE idempotency_key=?",
            (f"agent-run:{run['id']}:graph_commit:0",),
        ).fetchone()
        assert first_effect is not None

    restarted = RunStore(workspace_dir)
    assert restarted.recover_orphaned("replacement-worker") == 1
    claimed = restarted.claim_next("replacement-worker", global_limit=1, owner_limit=1)
    assert claimed is not None and claimed["id"] == run["id"]
    resumed_state = AgentRunState.model_validate(claimed["state"])
    attempt = restarted.begin_step_attempt(
        run["id"],
        "graph_commit",
        lease_owner="replacement-worker",
        lease_epoch=claimed["lease_epoch"],
    )
    workflow = DurableAgentWorkflow(
        workspace_dir=workspace_dir,
        run_store=restarted,
        app_manager=SimpleNamespace(),
        graph_db=graph_db,
        llm_config_store=SimpleNamespace(),
        opencode_runner=lambda **_kwargs: None,
    )
    outcome = await workflow(claimed, resumed_state)
    assert isinstance(outcome, Succeeded)
    restarted.commit_step(
        run["id"],
        "graph_commit",
        attempt=attempt,
        lease_owner="replacement-worker",
        lease_epoch=claimed["lease_epoch"],
        state=resumed_state,
        outcome=outcome,
    )

    completed = restarted.get_run(run["id"])
    assert completed is not None and completed["status"] == "succeeded"
    assert completed["result"]["ticket_id"]
    assert graph_db.get_node("crash-window-node")["properties"]["title"] == "exactly once"
    with sqlite3.connect(graph_db.db_path) as connection:
        effect_count = connection.execute(
            "SELECT COUNT(*) FROM graph_effects WHERE idempotency_key=?",
            (f"agent-run:{run['id']}:graph_commit:0",),
        ).fetchone()[0]
        node_count = connection.execute("SELECT COUNT(*) FROM graph_nodes WHERE id='crash-window-node'").fetchone()[0]
    assert effect_count == 1
    assert node_count == 1
