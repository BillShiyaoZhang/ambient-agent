"""Test real purpose-specific compilers and gates with scripted decision evidence."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from backend.agent.decisions import DecisionBundle, DecisionConfig, DecisionService
from backend.agent.errors import BudgetExhaustedError
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntent, SubIntentKind
from backend.agent.providers import ToolLoopBudget
from backend.agent.workflow_decisions import (
    resolve_decision_config,
    review_composite,
    review_development_plan,
    try_query_template,
)
from backend.router_context import GraphSnapshot, RouterContext


def _noul(probability: float = 0.99) -> dict[str, Any]:
    return {"type": "noul", "noul": probability}


def _choice(choice: str = "q_0", probability: float = 0.98) -> dict[str, Any]:
    options = ("q_0", "q_1", "none")
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": {option: probability if option == choice else (1 - probability) / 2 for option in options},
        "confidence": 0.91,
    }


def _score(level: int = 2) -> dict[str, Any]:
    return {
        "type": "score",
        "score": float(level),
        "probabilities": {str(index): float(index == level) for index in range(3)},
        "confidence": 1.0,
        "legend": {"0": "Missing", "1": "Partial", "2": "Complete"},
    }


def _evaluate(monkeypatch: pytest.MonkeyPatch, bundle: DecisionBundle) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def evaluate(
        purpose: str, state: dict[str, Any], questions: dict[str, Any], config: DecisionConfig, **kwargs: Any
    ) -> DecisionBundle:
        calls.append(
            {
                "purpose": purpose,
                "state": copy.deepcopy(state),
                "questions": copy.deepcopy(questions),
                "config": config,
                **kwargs,
            }
        )
        return bundle

    monkeypatch.setattr(DecisionService, "evaluate", evaluate)
    return calls


def _context() -> RouterContext:
    return RouterContext(
        app_manifests=[{"id": "private-app", "description": "private App metadata"}],
        graph_snapshot=GraphSnapshot(
            schema_manifest=[
                {"id": "Task", "description": "Canonical tasks"},
                {"id": "Event", "description": "Canonical events"},
            ],
            recent_nodes_by_type={"Task": [{"id": "private-row", "properties": {"title": "private Graph row"}}]},
            node_count=200,
        ),
        session_recent=[{"role": "user", "content": "We were discussing tasks"}],
        session_summary="The user is looking at the task list",
    )


def _composite(*, converse: bool = True) -> IntentPlan:
    steps = [
        SubIntent(kind=SubIntentKind.GRAPH_QUERY, query={"type": "Task", "limit": 10}),
        SubIntent(
            kind=SubIntentKind.GRAPH_MUTATION,
            actions=[{"action": "create_node", "type": "Task", "properties": {"title": "Ship"}}],
        ),
        SubIntent(kind=SubIntentKind.WIDGET_CREATE, app_id="new-app", instruction="Build an App"),
        SubIntent(kind=SubIntentKind.WIDGET_MODIFY, app_id="planner", instruction="Add a week view"),
    ]
    if converse:
        steps.append(SubIntent(kind=SubIntentKind.CONVERSE, instruction="Explain the results"))
    return IntentPlan(kind=IntentKind.MULTI_INTENT, sub_intents=steps)


def _composite_answers(plan: IntentPlan) -> dict[str, Any]:
    return {
        **{
            f"needs_{kind}": _noul()
            for kind in ("graph_query", "graph_mutation", "widget_create", "widget_modify", "converse")
        },
        **{f"step_{index}": _noul() for index in range(len(plan.sub_intents))},
        "all_objectives": _noul(),
        "order": _noul(),
        "coverage": _score(),
    }


def test_resolve_decision_config_uses_snapshot_instead_of_changed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JEV_DECISION_MODE", "cascade")
    monkeypatch.setenv("JEV_DECISION_MIN_PROBABILITY", "0.99")
    assert resolve_decision_config(None).mode == "cascade"
    frozen = resolve_decision_config({"mode": "off", "min_probability": 0.8})
    assert frozen.mode == "off"
    assert frozen.min_probability == 0.8
    with pytest.raises(ValidationError):
        resolve_decision_config({"mode": "unknown"})


@pytest.mark.asyncio
async def test_query_template_compiles_exact_existing_id_and_projects_no_private_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = DecisionBundle(answers={"template": _choice(), "unfiltered": _noul()})
    calls = _evaluate(monkeypatch, bundle)
    context = _context()
    db = object()
    audit = {"run_id": "run-template"}
    budget = ToolLoopBudget()

    plan = await try_query_template(
        "列出所有任务", context, DecisionConfig(mode="cascade"), db_session=db, audit_context=audit, budget=budget
    )

    assert plan is not None and plan.kind == IntentKind.GRAPH_QUERY
    assert plan.query == {"type": "Task", "limit": 500}
    assert plan.instruction == "列出所有任务"
    assert len(calls) == 1
    call = calls[0]
    assert call["purpose"] == "graph_query_template"
    assert call["db_session"] is db and call["budget"] is budget and call["audit_context"] is audit
    assert call["state"]["history"] == context.session_recent
    assert call["state"]["summary"] == context.session_summary
    assert [candidate["id"] for candidate in call["state"]["candidates"]] == ["Task", "Event"]
    projected = json.dumps(call["state"])
    assert "private Graph row" not in projected and "private-row" not in projected and "private-app" not in projected
    assert set(call["questions"]["template"]["criteria"]) == {"q_0", "q_1", "none"}
    assert "without property filters" in call["questions"]["unfiltered"]["instructions"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bundle, mode",
    [
        pytest.param(
            DecisionBundle(answers={"template": _choice(), "unfiltered": _noul(0.01)}),
            "cascade",
            id="confident-not-unfiltered",
        ),
        pytest.param(
            DecisionBundle(answers={"template": _choice("none"), "unfiltered": _noul()}), "cascade", id="no-template"
        ),
        pytest.param(
            DecisionBundle(answers={"template": _choice(probability=0.7), "unfiltered": _noul()}),
            "cascade",
            id="uncertain-choice",
        ),
        pytest.param(
            DecisionBundle(answers={"template": _choice(), "unfiltered": _noul(0.5)}),
            "cascade",
            id="uncertain-unfiltered",
        ),
        pytest.param(DecisionBundle(answers={"template": _choice()}), "cascade", id="missing-noul"),
        pytest.param(
            DecisionBundle(answers={"template": _choice(), "unfiltered": _noul()}, error="jev_timeout"),
            "cascade",
            id="service-error",
        ),
        pytest.param(
            DecisionBundle(answers={"template": _choice(), "unfiltered": _noul()}),
            "shadow",
            id="shadow-keeps-generator",
        ),
    ],
)
async def test_query_template_declines_uncertain_nonmatching_or_shadow_evidence(
    monkeypatch: pytest.MonkeyPatch, bundle: DecisionBundle, mode: str
) -> None:
    calls = _evaluate(monkeypatch, bundle)
    assert await try_query_template("List tasks", _context(), DecisionConfig(mode=mode)) is None
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["off", "empty", "overcap", "duplicate", "missing-id"])
async def test_query_template_does_not_classify_an_incomplete_or_overlimit_inventory(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    calls = _evaluate(monkeypatch, DecisionBundle())
    context = _context()
    config = DecisionConfig(mode="off" if case == "off" else "cascade", max_candidates=2)
    if case == "empty":
        context.graph_snapshot.schema_manifest = []
    elif case == "overcap":
        context.graph_snapshot.schema_manifest.append({"id": "Note"})
    elif case == "duplicate":
        context.graph_snapshot.schema_manifest = [{"id": "Task"}, {"id": "Task"}]
    elif case == "missing-id":
        context.graph_snapshot.schema_manifest = [{"description": "Unknown entity"}]
    assert await try_query_template("List records", context, config) is None
    assert calls == []


@pytest.mark.asyncio
async def test_composite_acceptance_preserves_the_complete_ordered_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _composite()
    original = plan.to_dict()
    calls = _evaluate(monkeypatch, DecisionBundle(answers=_composite_answers(plan)))

    assert (
        await review_composite("Do every listed action in order and explain", plan, DecisionConfig(mode="cascade"))
        is True
    )

    assert plan.to_dict() == original
    call = calls[0]
    assert call["purpose"] == "composite_review"
    assert call["state"]["plan"] == original
    assert call["questions"]["all_objectives"]["type"] == "noul"
    assert call["questions"]["order"]["type"] == "noul"
    assert "prerequisite" in call["questions"]["order"]["instructions"]
    assert call["questions"]["coverage"]["type"] == "score"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate, probability",
    [
        ("all_objectives", 0.01),
        ("all_objectives", 0.5),
        ("order", 0.01),
        ("order", 0.5),
        ("needs_graph_mutation", 0.5),
        ("needs_converse", 0.5),
        ("step_0", 0.01),
        ("step_0", 0.5),
    ],
)
async def test_high_score_cannot_override_a_rejected_or_uncertain_composite_gate(
    monkeypatch: pytest.MonkeyPatch, gate: str, probability: float
) -> None:
    plan = _composite()
    answers = _composite_answers(plan)
    answers[gate] = _noul(probability)
    _evaluate(monkeypatch, DecisionBundle(answers=answers))
    assert await review_composite("Do all actions and explain", plan, DecisionConfig(mode="cascade")) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["all_objectives", "order", "needs_widget_modify", "step_1"])
async def test_missing_composite_evidence_preserves_refinement(monkeypatch: pytest.MonkeyPatch, gate: str) -> None:
    plan = _composite()
    answers = _composite_answers(plan)
    del answers[gate]
    _evaluate(monkeypatch, DecisionBundle(answers=answers))
    assert await review_composite("Do all actions and explain", plan, DecisionConfig(mode="cascade")) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [SubIntentKind.CONVERSE, SubIntentKind.WIDGET_MODIFY])
async def test_required_facet_cannot_be_omitted_from_the_candidate(
    monkeypatch: pytest.MonkeyPatch, missing: SubIntentKind
) -> None:
    plan = _composite()
    plan.sub_intents = [step for step in plan.sub_intents if step.kind != missing]
    _evaluate(monkeypatch, DecisionBundle(answers=_composite_answers(plan)))
    assert (
        await review_composite("Do every action including an explanation", plan, DecisionConfig(mode="cascade"))
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "granular", [SubIntentKind.WIDGET_EXTEND_SCHEMA, SubIntentKind.WIDGET_FIX_CODE, SubIntentKind.WIDGET_REWRITE]
)
async def test_granular_app_steps_still_cover_the_modify_facet(
    monkeypatch: pytest.MonkeyPatch, granular: SubIntentKind
) -> None:
    plan = _composite()
    plan.sub_intents[3] = SubIntent(
        kind=granular,
        app_id="planner",
        instruction="Update planner",
        extend_schema_props={"Task": {"priority": "integer"}}
        if granular == SubIntentKind.WIDGET_EXTEND_SCHEMA
        else None,
    )
    _evaluate(monkeypatch, DecisionBundle(answers=_composite_answers(plan)))
    assert await review_composite("Do all actions", plan, DecisionConfig(mode="cascade")) is True


@pytest.mark.asyncio
async def test_low_advisory_score_does_not_block_an_otherwise_accepted_composite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _composite()
    answers = _composite_answers(plan)
    answers["coverage"] = _score(0)
    _evaluate(monkeypatch, DecisionBundle(answers=answers))
    assert await review_composite("Do all actions", plan, DecisionConfig(mode="cascade")) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["off", "blank-request", "missing-parameters"])
async def test_composite_review_does_not_classify_an_unusable_candidate(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    plan = _composite()
    calls = _evaluate(monkeypatch, DecisionBundle())
    if case == "missing-parameters":
        plan.sub_intents[0].query = None
    config = DecisionConfig(mode="off" if case == "off" else "cascade")
    assert await review_composite(" " if case == "blank-request" else "Do all actions", plan, config) is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,error", [("shadow", None), ("cascade", "jev_response_invalid")])
async def test_composite_shadow_and_service_failure_do_not_skip_refiner(
    monkeypatch: pytest.MonkeyPatch, mode: str, error: str | None
) -> None:
    plan = _composite()
    calls = _evaluate(monkeypatch, DecisionBundle(answers=_composite_answers(plan), error=error))
    assert await review_composite("Do all actions", plan, DecisionConfig(mode=mode)) is False
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "shadow", "cascade"])
async def test_development_review_returns_only_advisory_evidence(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    bundle = DecisionBundle(answers={"relevant": _noul(0.01), "scope_creep": _noul(), "coverage": _score(0)})
    calls = _evaluate(monkeypatch, bundle)
    candidate = "Propose new UI and Graph grants"
    result = await review_development_plan("Build a task App", candidate, "planner", DecisionConfig(mode=mode))
    if mode == "off":
        assert result is None and calls == []
    else:
        assert result is bundle
        call = calls[0]
        assert call["purpose"] == "development_plan_review"
        assert call["state"] == {"instruction": "Build a task App", "candidate": candidate, "app_id": "planner"}
        assert set(call["questions"]) == {"relevant", "scope_creep", "coverage"}
        assert "permission" in call["questions"]["coverage"]["instructions"]
        assert "approved" not in result.model_dump() and "capabilities" not in result.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose", ["query", "composite", "development"])
@pytest.mark.parametrize("exception", [BudgetExhaustedError, asyncio.CancelledError])
async def test_stage_helpers_propagate_budget_exhaustion_and_cancellation(
    monkeypatch: pytest.MonkeyPatch, purpose: str, exception: type[BaseException]
) -> None:
    async def fail(*_args: Any, **_kwargs: Any) -> DecisionBundle:
        raise exception()

    monkeypatch.setattr(DecisionService, "evaluate", fail)
    config = DecisionConfig(mode="cascade")
    with pytest.raises(exception):
        if purpose == "query":
            await try_query_template("List tasks", _context(), config)
        elif purpose == "composite":
            await review_composite("Do all actions", _composite(), config)
        else:
            await review_development_plan("Build", "Plan", "planner", config)
