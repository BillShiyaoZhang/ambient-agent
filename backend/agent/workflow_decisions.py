"""Semantic decisions over complete, stage-specific evidence and candidates."""

from __future__ import annotations

from typing import Any

from backend.agent.decisions import DecisionBundle, DecisionConfig, DecisionService
from backend.agent.generation import validate_complete_plan
from backend.agent.intent_plan import IntentKind, IntentPlan, SubIntentKind
from backend.agent.providers import ToolLoopBudget
from backend.router_context import RouterContext


def resolve_decision_config(snapshot: dict[str, Any] | None) -> DecisionConfig:
    return DecisionConfig.model_validate(snapshot) if snapshot is not None else DecisionConfig.from_env()


def _history(context: RouterContext) -> dict[str, Any]:
    return {"history": context.session_recent, "summary": context.session_summary}


async def try_query_template(
    content: str,
    context: RouterContext,
    config: DecisionConfig,
    *,
    db_session: Any = None,
    audit_context: dict[str, Any] | None = None,
    budget: ToolLoopBudget | None = None,
) -> IntentPlan | None:
    """Compile only explicit, unfiltered lists of one existing Graph entity.

    No arithmetic, dates, filters, writes, invented entities, or candidate
    truncation. A selected template is still subject to the durable query phase.
    """
    config = config.for_purpose("graph_query_template")
    if config.mode == "off":
        return None
    schemas = context.graph_snapshot.schema_manifest
    if not schemas or len(schemas) > config.max_candidates:
        return None
    ids = [item.get("id") for item in schemas]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        return None
    candidates = [
        {"key": f"q_{i}", "id": schema["id"], "description": schema.get("description", "")}
        for i, schema in enumerate(schemas)
    ]
    criteria = {
        item[
            "key"
        ]: f"List records of the existing entity identified by candidate key {item['key']} in state.candidates."
        for item in candidates
    }
    criteria["none"] = "No single unfiltered entity list exactly satisfies the request."
    questions = {
        "template": {
            "type": "choice",
            "instructions": "Which existing unfiltered list satisfies latest_request?",
            "criteria": criteria,
        },
        "unfiltered": {
            "type": "noul",
            "instructions": "Does latest_request explicitly request only a list of one existing Graph entity, without property filters, selected record IDs, ordering, counts, aggregates, date conditions, UI/code changes, writes, or other actions? Resolve references with history; answer no if any such requirement is present or the target is unclear.",
        },
    }
    bundle = await DecisionService.evaluate(
        "graph_query_template",
        {"latest_request": content, "candidates": candidates, **_history(context)},
        questions,
        config,
        db_session=db_session,
        audit_context=audit_context,
        budget=budget,
    )
    answer = bundle.answers.get("template", {})
    if (
        config.mode != "cascade"
        or bundle.error
        or not DecisionService.accepts_choice(answer, config)
        or not DecisionService.accepts_noul(bundle.answers.get("unfiltered", {}), config)
        or answer.get("choice") == "none"
    ):
        return None
    selected = next((item for item in candidates if item["key"] == answer["choice"]), None)
    if selected is None:
        return None
    return IntentPlan(
        kind=IntentKind.GRAPH_QUERY,
        confidence=max(answer["probabilities"].values()),
        rationale="validated read-only query template",
        instruction=content,
        query={"type": selected["id"], "limit": 500},
    )


async def review_composite(
    content: str,
    plan: IntentPlan,
    config: DecisionConfig,
    *,
    db_session: Any = None,
    audit_context: dict[str, Any] | None = None,
    budget: ToolLoopBudget | None = None,
) -> bool:
    """Only complete candidates can avoid parameter refinement, never preflight."""
    config = config.for_purpose("composite_review")
    if config.mode == "off" or not content.strip():
        return False
    try:
        validate_complete_plan(plan)
    except (TypeError, ValueError, AttributeError):
        return False
    facets = {
        "graph_query": "an explicit read-only Graph data query as an objective",
        "graph_mutation": "persisted Graph data changes",
        "widget_create": "creation of a new App",
        "widget_modify": "changes to an existing App's UI, code, behavior or schema",
        "converse": "a separate explanation or conversation response as an objective",
    }
    questions: dict[str, Any] = {
        f"needs_{key}": {
            "type": "noul",
            "instructions": f"Does latest_request require {description}? Do not count an incidental implementation step as a separate objective.",
        }
        for key, description in facets.items()
    }
    for index in range(len(plan.sub_intents)):
        questions[f"step_{index}"] = {
            "type": "noul",
            "instructions": f"Is plan.sub_intents[{index}] an appropriate, necessary step for latest_request, without introducing an unrelated objective or silently changing the user's target?",
        }
    questions["all_objectives"] = {
        "type": "noul",
        "instructions": "Does plan include every distinct objective and target requested in latest_request, including multiple actions or App targets of the same kind? Answer no when anything is omitted or the request is too ambiguous to establish full coverage.",
    }
    questions["order"] = {
        "type": "noul",
        "instructions": "Does the order of plan.sub_intents respect every ordering requirement in latest_request and every step's prerequisite, without consuming a result or artifact before its producer step? Answer no when a dependency or required order is unresolved.",
    }
    questions["coverage"] = {
        "type": "score",
        "instructions": "Rate how well the proposed plan covers the latest request's objectives. This score is advisory and does not validate parameters, dependencies or permissions.",
        "criteria": [
            "Major requested objectives omitted",
            "Some objectives omitted or unclear",
            "All requested objectives represented",
        ],
    }
    bundle = await DecisionService.evaluate(
        "composite_review",
        {"latest_request": content, "plan": plan.to_dict()},
        questions,
        config,
        db_session=db_session,
        audit_context=audit_context,
        budget=budget,
    )
    if bundle.error or config.mode != "cascade":
        return False
    represented = {
        "widget_modify"
        if sub.kind in (SubIntentKind.WIDGET_EXTEND_SCHEMA, SubIntentKind.WIDGET_FIX_CODE, SubIntentKind.WIDGET_REWRITE)
        else sub.kind.value
        for sub in plan.sub_intents
    }
    for key in facets:
        answer = bundle.answers.get(f"needs_{key}", {})
        yes = DecisionService.accepts_noul(answer, config)
        no = DecisionService.accepts_noul(answer, config, expected=False)
        if not (yes or no) or (yes and key not in represented):
            return False
    gates = [f"step_{index}" for index in range(len(plan.sub_intents))] + ["all_objectives", "order"]
    return all(DecisionService.accepts_noul(bundle.answers.get(key, {}), config) for key in gates)


async def review_development_plan(
    instruction: str,
    candidate: str,
    app_id: str,
    config: DecisionConfig,
    *,
    db_session: Any = None,
    audit_context: dict[str, Any] | None = None,
    budget: ToolLoopBudget | None = None,
) -> DecisionBundle | None:
    """Advisory semantic evidence tied to this candidate, never a user approval."""
    config = config.for_purpose("development_plan_review")
    if config.mode == "off":
        return None
    return await DecisionService.evaluate(
        "development_plan_review",
        {"instruction": instruction, "candidate": candidate, "app_id": app_id},
        {
            "relevant": {"type": "noul", "instructions": "Does candidate describe work that addresses instruction?"},
            "scope_creep": {
                "type": "noul",
                "instructions": "Does candidate introduce unrelated user-visible objectives beyond instruction?",
            },
            "coverage": {
                "type": "score",
                "instructions": "Rate coverage of the requested UI/features in candidate; do not assess execution permission.",
                "criteria": ["Major requested features missing", "Some features covered", "Requested features covered"],
            },
        },
        config,
        db_session=db_session,
        audit_context=audit_context,
        budget=budget,
    )
