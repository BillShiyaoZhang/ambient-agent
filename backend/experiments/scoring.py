"""Scoring helpers — used by runner and report."""

from __future__ import annotations

from dataclasses import dataclass

from backend.experiments.runner import VariantAggregate, ScenarioAggregate
from backend.experiments.scenarios import Scenario


# Thresholds from the plan
AMBIGUOUS_SCENARIO_MIN_KIND_ACC = 0.6
MIN_STABILITY = 0.8


@dataclass
class WinnerResult:
    variant_id: str
    score: float
    kind_acc: float
    app_id_acc: float
    stability: float
    ambiguous_kind_acc: float
    passes_thresholds: bool


def compute_winner_metrics(
    agg: VariantAggregate,
    ambiguous_scenario_ids: set[str],
    scenarios_by_id: dict[str, Scenario],
) -> WinnerResult:
    kind_acc = agg.kind_accuracy_overall()
    app_id_acc = agg.app_id_accuracy_overall(scenarios_by_id)
    stability = agg.stability_overall()

    # ambiguous kind accuracy
    amb_correct, amb_total = 0, 0
    for sid in ambiguous_scenario_ids:
        sa = agg.per_scenario.get(sid)
        if not sa:
            continue
        amb_total += sa.n
        amb_correct += sa.n_kinds.get(sa.expected_kind, 0)
    amb_kind_acc = amb_correct / amb_total if amb_total else 0.0

    score = kind_acc * 1.0 + app_id_acc * 0.5 + stability * 0.3

    passes = (
        kind_acc > 0  # always require > 0
        and stability >= MIN_STABILITY
        and amb_kind_acc >= AMBIGUOUS_SCENARIO_MIN_KIND_ACC
    )

    return WinnerResult(
        variant_id=agg.variant_id,
        score=score,
        kind_acc=kind_acc,
        app_id_acc=app_id_acc,
        stability=stability,
        ambiguous_kind_acc=amb_kind_acc,
        passes_thresholds=passes,
    )


def per_choice_winners(
    aggregates: list[VariantAggregate],
    ambiguous_scenario_ids: set[str],
    scenarios_by_id: dict[str, Scenario],
) -> dict[str, WinnerResult]:
    """Pick the best variant per choice C1..C7.

    OFAT convention: variants prefixed with V_C{i}{letter} are tied to choice C{i}.
    """
    by_choice: dict[str, list[VariantAggregate]] = {f"c{i}": [] for i in range(1, 8)}
    for agg in aggregates:
        if agg.variant_id == "V_baseline":
            continue
        # variant_id pattern: V_C{n}{letter}
        if not agg.variant_id.startswith("V_C"):
            continue
        try:
            choice_idx = int(agg.variant_id[3])
            letter = agg.variant_id[4]
        except (ValueError, IndexError):
            continue
        key = f"c{choice_idx}"
        if key in by_choice and letter in ("A", "B", "C"):
            by_choice[key].append(agg)

    out: dict[str, WinnerResult] = {}
    for choice, aggs in by_choice.items():
        if not aggs:
            continue
        best = max(
            aggs,
            key=lambda a: compute_winner_metrics(a, ambiguous_scenario_ids, scenarios_by_id).score,
        )
        out[choice] = compute_winner_metrics(best, ambiguous_scenario_ids, scenarios_by_id)
    return out