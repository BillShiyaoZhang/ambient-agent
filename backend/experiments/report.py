"""Markdown report generator for routing experiments."""

from __future__ import annotations

from datetime import datetime
from io import StringIO

from backend.experiments.runner import VariantAggregate
from backend.experiments.scoring import (
    AMBIGUOUS_SCENARIO_MIN_KIND_ACC,
    MIN_STABILITY,
    compute_winner_metrics,
    per_choice_winners,
)
from backend.experiments.scenarios import Scenario, get_ambiguous_scenarios


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = StringIO()
    out.write("| " + " | ".join(headers) + " |\n")
    out.write("|" + "|".join("---" for _ in headers) + "|\n")
    for r in rows:
        out.write("| " + " | ".join(r) + " |\n")
    return out.getvalue()


def render_summary(
    aggregates: list[VariantAggregate],
    ambiguous_ids: set[str],
    scenarios_by_id: dict[str, Scenario],
) -> str:
    rows = []
    for agg in aggregates:
        m = compute_winner_metrics(agg, ambiguous_ids, scenarios_by_id)
        rows.append([
            agg.variant_id,
            m.score.__format__(".3f"),
            _fmt_pct(m.kind_acc),
            _fmt_pct(m.app_id_acc),
            _fmt_pct(m.stability),
            _fmt_pct(m.ambiguous_kind_acc),
            "✅" if m.passes_thresholds else "❌",
        ])
    rows.sort(key=lambda r: float(r[1]), reverse=True)
    return _table(
        ["Variant", "Score", "Kind Acc", "App ID Acc", "Stability", "Ambig Kind Acc", "Passes"],
        rows,
    )


def render_per_choice_winners(
    aggregates: list[VariantAggregate],
    ambiguous_ids: set[str],
    scenarios_by_id: dict[str, Scenario],
) -> str:
    winners = per_choice_winners(aggregates, ambiguous_ids, scenarios_by_id)
    rows = []
    for choice in sorted(winners):
        w = winners[choice]
        rows.append([
            choice.upper(),
            w.variant_id,
            w.score.__format__(".3f"),
            _fmt_pct(w.kind_acc),
            _fmt_pct(w.app_id_acc),
            _fmt_pct(w.stability),
            _fmt_pct(w.ambiguous_kind_acc),
        ])
    return _table(
        ["Choice", "Winner Variant", "Score", "Kind Acc", "App ID Acc", "Stability", "Ambig Kind Acc"],
        rows,
    )


def render_ambiguous_drilldown(
    aggregates: list[VariantAggregate],
    ambiguous_scenarios: list[Scenario],
    scenarios_by_id: dict[str, Scenario],
) -> str:
    rows = []
    for sc in ambiguous_scenarios:
        baseline = next((a for a in aggregates if a.variant_id == "V_baseline"), None)
        # For drilldown, list every variant's accuracy on this scenario.
        per_variant = []
        for agg in aggregates:
            sa = agg.per_scenario.get(sc.id)
            if not sa:
                continue
            acc = sa.n_kinds.get(sc.expected_kind.value, 0) / sa.n if sa.n else 0.0
            per_variant.append(f"{agg.variant_id}={_fmt_pct(acc)}")
        rows.append([
            sc.id,
            sc.user_message,
            sc.expected_kind.value,
            ", ".join(per_variant[:6]) + (" ..." if len(per_variant) > 6 else ""),
        ])
    return _table(
        ["Scenario", "Message", "Expected", "Per-variant accuracy"],
        rows,
    )


def render_report(
    aggregates: list[VariantAggregate],
    scenarios: list[Scenario],
    metadata: dict | None = None,
) -> str:
    scenarios_by_id = {s.id: s for s in scenarios}
    ambiguous = get_ambiguous_scenarios()
    ambiguous_ids = {s.id for s in ambiguous}

    out = StringIO()
    out.write("# Routing Experiment Report\n\n")
    out.write(f"_Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z_\n\n")
    if metadata:
        out.write(f"_Provider: {metadata.get('provider', '?')}, Model: {metadata.get('model', '?')}, "
                  f"Repeats: {metadata.get('n_repeats', '?')}_\n\n")

    out.write("## Summary\n\n")
    out.write(render_summary(aggregates, ambiguous_ids, scenarios_by_id))
    out.write("\n\n## Per-Choice Winner (OFAT)\n\n")
    out.write(render_per_choice_winners(aggregates, ambiguous_ids, scenarios_by_id))
    out.write("\n\n## Ambiguous Scenario Drill-Down\n\n")
    out.write(render_ambiguous_drilldown(aggregates, ambiguous, scenarios_by_id))

    out.write("\n\n## Recommendation\n\n")
    winners = per_choice_winners(aggregates, ambiguous_ids, scenarios_by_id)
    if winners:
        out.write("Apply the winning letter for each choice:\n\n")
        for choice in sorted(winners):
            w = winners[choice]
            out.write(f"- **{choice.upper()}** → `{w.variant_id[-1]}` "
                      f"(score {w.score:.3f})\n")
    else:
        out.write("_No winner data yet._\n")

    out.write("\n\n## Thresholds\n\n")
    out.write(f"- Min stability: {MIN_STABILITY * 100:.0f}%\n")
    out.write(f"- Min ambiguous scenario kind accuracy: {AMBIGUOUS_SCENARIO_MIN_KIND_ACC * 100:.0f}%\n")

    return out.getvalue()
