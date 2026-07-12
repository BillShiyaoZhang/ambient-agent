"""Async runner for routing experiments.

For each (variant, scenario, repeat), call IntentRouter.route() against the
real LLM (configurable) and record the result.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from backend.agent.intent_plan import IntentPlan, IntentKind
from backend.agent.router import IntentRouter
from backend.experiments.scenarios import Scenario
from backend.experiments.variants import Variant

logger = logging.getLogger("experiments.runner")


@dataclass
class RoutingResult:
    """One observation: a single (variant, scenario, repeat) call."""

    variant_id: str
    scenario_id: str
    repeat: int
    kind: str | None
    app_id: str | None
    confidence: float
    rationale: str
    latency_ms: float
    error: str | None = None
    raw_response: str = ""


@dataclass
class ScenarioAggregate:
    scenario_id: str
    expected_kind: str
    n: int
    n_kinds: dict[str, int] = field(default_factory=dict)
    n_app_ids: dict[str, int] = field(default_factory=dict)
    n_errors: int = 0
    mean_latency_ms: float = 0.0

    def kind_accuracy(self) -> float:
        correct = self.n_kinds.get(self.expected_kind, 0)
        return correct / self.n if self.n else 0.0

    def stability(self) -> float:
        if self.n <= 1:
            return 1.0
        dominant = max(self.n_kinds.values())
        return dominant / self.n if self.n else 0.0


@dataclass
class VariantAggregate:
    variant_id: str
    description: str
    choices: dict[str, str]
    n_total: int = 0
    n_errors: int = 0
    mean_latency_ms: float = 0.0
    per_scenario: dict[str, ScenarioAggregate] = field(default_factory=dict)

    def kind_accuracy_overall(self) -> float:
        correct = sum(
            sa.n_kinds.get(sa.expected_kind, 0)
            for sa in self.per_scenario.values()
        )
        return correct / self.n_total if self.n_total else 0.0

    def app_id_accuracy_overall(self, scenarios_by_id: dict[str, Scenario]) -> float:
        """Score app_id correctness.

        Matching rules:
        - If expected_app_id is None: skip.
        - If expected_app_id ends with ``-``: treat as a "topic prefix".
          The generated id counts as correct if it contains the topic word
          (e.g. expected ``clock-app-`` matches both ``clock-app-XXXX`` and
          ``clock-widget-7b2e`` because both contain ``clock``).
        - Otherwise: exact match required.
        """
        total, correct = 0, 0
        for sid, sa in self.per_scenario.items():
            sc = scenarios_by_id[sid]
            if not sc.expected_app_id:
                continue
            if sc.expected_app_id.endswith("-"):
                # Topic-prefix match: extract the topic word(s).
                topic = sc.expected_app_id.rstrip("-").split("-")[0]
                # Also accept the second word as topic (e.g. "clock-app-" → "clock")
                for app_id, count in sa.n_app_ids.items():
                    if app_id and (topic in app_id.lower()):
                        correct += count
                total += sa.n
            else:
                total += sa.n
                correct += sa.n_app_ids.get(sc.expected_app_id, 0)
        return correct / total if total else 0.0

    def stability_overall(self) -> float:
        if not self.per_scenario:
            return 0.0
        return sum(sa.stability() for sa in self.per_scenario.values()) / len(self.per_scenario)

    def total_score(self) -> float:
        return (
            self.kind_accuracy_overall() * 1.0
            + self.app_id_accuracy_overall({}) * 0.5
            + self.stability_overall() * 0.3
        )


# Mapping from variant.choice c3 -> list of context sections to include.
_C3_SECTIONS = {
    "A": ["widgets", "graph_counts", "recent_nodes", "schemas", "history"],
    "B": ["widgets", "graph_counts", "history"],
    "C": ["widgets"],
}


async def _run_one(
    variant: Variant,
    scenario: Scenario,
    repeat: int,
    provider: str,
    model: str,
    temperature: float,
) -> RoutingResult:
    ctx = scenario.context
    c3 = variant.context_options.get("c3", "A")
    sections = _C3_SECTIONS.get(c3, None)
    include_hint = variant.choices.get("c4") == "B"  # C4 lenient hints widget title mapping

    t0 = time.perf_counter()
    error: str | None = None
    plan: IntentPlan | None = None
    raw_content = ""
    try:
        plan = await IntentRouter.route(
            content=scenario.user_message,
            context=ctx,
            db_session=None,
            provider_name=provider,
            model_name=model,
            override_system_prompt=variant.system_prompt,
            context_sections=sections,
            include_widget_keyword_hint=include_hint,
            fallback_keywords=variant.fallback_keywords,
            plan_and_act_enabled=variant.plan_and_act_enabled,
        )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    latency_ms = (time.perf_counter() - t0) * 1000.0

    return RoutingResult(
        variant_id=variant.id,
        scenario_id=scenario.id,
        repeat=repeat,
        kind=plan.kind.value if plan else None,
        app_id=plan.app_id if plan else None,
        confidence=plan.confidence if plan else 0.0,
        rationale=plan.rationale if plan else "",
        latency_ms=latency_ms,
        error=error,
        raw_response=raw_content,
    )


async def run_variant(
    variant: Variant,
    scenarios: list[Scenario],
    n_repeats: int = 5,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    concurrency: int = 3,
    on_result=None,
) -> list[RoutingResult]:
    """Run variant on all scenarios × n_repeats. Returns flat list of results."""
    provider = provider or os.getenv("LLM_PROVIDER", "minimax")
    model = model or os.getenv("LLM_MODEL", "MiniMax-M3")

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(scenario, repeat):
        async with sem:
            r = await _run_one(variant, scenario, repeat, provider, model, temperature)
            if on_result:
                on_result(r)
            return r

    tasks = [
        _bounded(sc, rep)
        for sc in scenarios
        for rep in range(n_repeats)
    ]
    return await asyncio.gather(*tasks)


def aggregate(
    variant: Variant,
    results: list[RoutingResult],
    scenarios_by_id: dict[str, Scenario],
) -> VariantAggregate:
    """Reduce a flat list of RoutingResult into a VariantAggregate."""
    agg = VariantAggregate(
        variant_id=variant.id,
        description=variant.description,
        choices=variant.choices,
    )

    # Group by scenario.
    by_scen: dict[str, list[RoutingResult]] = {}
    for r in results:
        by_scen.setdefault(r.scenario_id, []).append(r)

    total_lat = 0.0
    n_total = 0
    n_err = 0
    for sid, rs in by_scen.items():
        sc = scenarios_by_id[sid]
        sa = ScenarioAggregate(
            scenario_id=sid,
            expected_kind=sc.expected_kind.value,
            n=len(rs),
        )
        lat_sum = 0.0
        for r in rs:
            if r.error:
                sa.n_errors += 1
                n_err += 1
            if r.kind:
                sa.n_kinds[r.kind] = sa.n_kinds.get(r.kind, 0) + 1
            if r.app_id:
                sa.n_app_ids[r.app_id] = sa.n_app_ids.get(r.app_id, 0) + 1
            lat_sum += r.latency_ms
            total_lat += r.latency_ms
            n_total += 1
        sa.mean_latency_ms = lat_sum / len(rs) if rs else 0.0
        agg.per_scenario[sid] = sa

    agg.n_total = n_total
    agg.n_errors = n_err
    agg.mean_latency_ms = total_lat / n_total if n_total else 0.0
    return agg