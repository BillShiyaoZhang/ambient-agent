"""Tests for the WidgetDAG runtime (Direction B)."""

from __future__ import annotations

import pytest

from backend.agent.dag import TaskContext, TaskNode, TaskResult, WidgetDAG


@pytest.mark.asyncio
async def test_dag_runs_linear_tasks_in_order():
    dag = WidgetDAG()
    log: list[str] = []

    async def plan(ctx):
        log.append("plan")
        return TaskResult(outputs={"plan": "v1"})

    async def schema(ctx):
        log.append("schema")
        return TaskResult(outputs={"schema": "v1"})

    async def code(ctx):
        log.append("code")
        return TaskResult(outputs={"code": "v1"})

    async def verify(ctx):
        log.append("verify")
        return TaskResult(outputs={"verify": "v1"})

    dag.register(TaskNode("plan", plan))
    dag.register(TaskNode("align_schemas", schema))
    dag.register(TaskNode("code", code))
    dag.register(TaskNode("verify", verify))

    ctx = TaskContext(session_id="s", app_id="a", plan_input=None)

    while not dag.idle():
        r = await dag.step(ctx)
        assert r.success
    assert log == ["plan", "schema", "code", "verify"]


@pytest.mark.asyncio
async def test_dag_invalidates_propagate_to_downstream_nodes():
    dag = WidgetDAG()
    log: list[str] = []

    async def plan(ctx):
        log.append("plan")
        return TaskResult()

    async def schema(ctx):
        log.append("schema")
        return TaskResult(invalidates_if_redo={"regen_code", "verify"})

    async def code(ctx):
        log.append("code")
        return TaskResult(invalidates_if_redo={"verify"})

    async def verify(ctx):
        log.append("verify")
        return TaskResult()

    dag.register(TaskNode("plan", plan))
    dag.register(TaskNode("align_schemas", schema, invalidates={"regen_code", "verify"}))
    dag.register(TaskNode("regen_code", code))
    dag.register(TaskNode("verify", verify))

    ctx = TaskContext(session_id="s", app_id="a", plan_input=None)
    while not dag.idle():
        await dag.step(ctx)
    assert log == ["plan", "schema", "code", "verify"]

    log.clear()
    dag.dirty("align_schemas")
    while not dag.idle():
        await dag.step(ctx)
    assert log == ["schema", "code", "verify"]


def test_dag_dirty_unknown_node_raises():
    dag = WidgetDAG()
    with pytest.raises(KeyError):
        dag.dirty("nonexistent")


@pytest.mark.asyncio
async def test_dag_idle_when_no_dirty():
    dag = WidgetDAG()
    assert dag.idle()

    async def noop(ctx):
        return TaskResult()

    dag.register(TaskNode("a", noop))
    assert not dag.idle()
    await dag.step(TaskContext("s", "a", None))
    assert dag.idle()


@pytest.mark.asyncio
async def test_dag_ask_user_payload_propagates():
    dag = WidgetDAG()

    async def plan(ctx):
        return TaskResult(outputs={"plan": "v1"})

    async def verify(ctx):
        return TaskResult(
            outputs={"diff": "needs_review"},
            ask_user={"payload": {"type": "x"}, "future": None},
        )

    dag.register(TaskNode("plan", plan))
    dag.register(TaskNode("verify", verify))

    ctx = TaskContext("s", "a", None)
    await dag.step(ctx)
    r = await dag.step(ctx)
    assert r.ask_user is not None
    assert "verify" not in dag.pending()
