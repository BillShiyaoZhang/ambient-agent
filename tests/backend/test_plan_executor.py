from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.plan_executor import (
    CodingPlanExecutor,
    MutationPlanExecutor,
    PlanExecutor,
    PlanPhaseResult,
)


class _StubExecutor(PlanExecutor):
    """Concrete subclass used to test abstract behavior."""

    async def run_plan(
        self,
        plan: IntentPlan,
        instruction: str,
        on_update: Callable,
    ) -> PlanPhaseResult:
        return PlanPhaseResult(success=True, output="stub-output")


def test_plan_executor_cannot_instantiate_directly():
    with pytest.raises(TypeError):
        PlanExecutor()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_stub_executor_returns_plan_result():
    stub = _StubExecutor()
    result = await stub.run_plan(
        IntentPlan(kind=IntentKind.PLAN_AND_ACT, instruction="x"), "x", AsyncMock()
    )
    assert isinstance(result, PlanPhaseResult)
    assert result.success


@pytest.mark.asyncio
async def test_coding_plan_executor_runs_widget_pipeline(monkeypatch):
    captured: dict = {}

    async def fake_run_opencode(app_id, instruction, on_update):
        captured["app_id"] = app_id
        captured["instruction"] = instruction
        return "ran opencode"

    monkeypatch.setattr(
        "backend.agent.plan_executor.run_opencode_agent_acp", fake_run_opencode
    )

    executor = CodingPlanExecutor(graph_db_factory=lambda: None)
    plan = IntentPlan(
        kind=IntentKind.WIDGET_MODIFY,
        rationale="build widget",
        app_id="executor-app",
        instruction="make it shiny",
    )
    result = await executor.run_plan(plan, plan.instruction, AsyncMock())
    assert result.success
    assert "ran opencode" in result.output
    assert captured["app_id"] == "executor-app"


@pytest.mark.asyncio
async def test_mutation_plan_executor_dispatches_to_handlers(monkeypatch, tmp_path):
    """MutationPlanExecutor should request user approval then apply actions."""
    import os

    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)
    from backend.graph_db import GraphDatabase

    db = GraphDatabase(workspace_dir)
    # Pre-existing node so update actions have something
    db.create_node(node_id="m-1", node_type="Task", properties={"title": "x", "status": "pending"})

    # Provide a fake "approve immediately" future
    fake_future_results: list = []

    class _FakeFuture:
        def __init__(self, result):
            self._result = result
            self._done = False

        def __await__(self):
            async def _coro():
                return self._result

            return _coro().__await__()

    async def fake_await_approval(plan_str, app_id, session_id, on_update):
        fake_future_results.append(plan_str)
        return ("approve", plan_str)

    monkeypatch.setattr(
        "backend.agent.plan_executor._await_plan_approval",
        fake_await_approval,
    )

    plan = IntentPlan(
        kind=IntentKind.PLAN_AND_ACT,
        rationale="multi-step",
        actions=[
            {"action": "create_node", "id": "m-2", "type": "Task", "properties": {"title": "y"}},
            {"action": "update_node_property", "id": "m-1", "properties": {"status": "done"}},
        ],
    )

    on_update = AsyncMock()
    executor = MutationPlanExecutor(graph_db_factory=lambda: db)
    result = await executor.run_plan(plan, "instruction", on_update)

    assert result.success
    assert fake_future_results, "approval should be requested"

    # Verify nodes exist
    assert db.get_node("m-2") is not None
    assert db.get_node("m-1")["properties"]["status"] == "done"
    # Agent reply mentions plan execution
    assert result.output and ("plan" in result.output.lower() or "完成" in result.output)


@pytest.mark.asyncio
async def test_mutation_plan_executor_denied_returns_failure(monkeypatch, tmp_path):
    import os

    workspace_dir = str(tmp_path / "workspace")
    os.makedirs(workspace_dir, exist_ok=True)
    monkeypatch.setenv("WORKSPACE_DIR", workspace_dir)
    from backend.graph_db import GraphDatabase

    db = GraphDatabase(workspace_dir)
    db.create_node(node_id="m-3", node_type="Task", properties={"title": "x"})

    async def fake_await_approval(plan_str, app_id, session_id, on_update):
        return ("deny", None)

    monkeypatch.setattr(
        "backend.agent.plan_executor._await_plan_approval",
        fake_await_approval,
    )

    plan = IntentPlan(
        kind=IntentKind.PLAN_AND_ACT,
        rationale="should be denied",
        actions=[
            {"action": "delete_node", "id": "m-3"},
        ],
    )

    executor = MutationPlanExecutor(graph_db_factory=lambda: db)
    result = await executor.run_plan(plan, "instruction", AsyncMock())
    assert not result.success
    # Node should still exist since deny
    assert db.get_node("m-3") is not None
