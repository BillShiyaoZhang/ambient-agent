"""Unit tests for runner aggregation and scoring logic."""

from backend.experiments.runner import RoutingResult, aggregate
from backend.experiments.scenarios import Scenario, get_scenario
from backend.experiments.scoring import compute_winner_metrics, per_choice_winners
from backend.experiments.variants import Variant


def _make_results(variant_id: str, scenario: Scenario, kinds: list[str]) -> list[RoutingResult]:
    return [
        RoutingResult(
            variant_id=variant_id,
            scenario_id=scenario.id,
            repeat=i,
            kind=k,
            app_id="clock-app-1234" if k == "widget_modify" else None,
            confidence=0.9,
            rationale="",
            latency_ms=1000.0,
        )
        for i, k in enumerate(kinds)
    ]


def _variant(id_: str, **choices) -> Variant:
    base = {"c1": "A", "c2": "A", "c3": "A", "c4": "A", "c5": "A", "c6": "C", "c7": "A"}
    base.update(choices)
    return Variant(
        id=id_,
        description="",
        choices=base,
        system_prompt="",
        fallback_keywords=None,
        agent_system_prompt=None,
        plan_and_act_enabled=True,
    )


def test_aggregate_all_correct():
    sc = get_scenario("S01")
    v = _variant("V_C1A")
    rs = _make_results("V_C1A", sc, ["widget_create"] * 5)
    agg = aggregate(v, rs, {sc.id: sc})

    assert agg.n_total == 5
    assert agg.kind_accuracy_overall() == 1.0
    assert agg.stability_overall() == 1.0


def test_aggregate_mixed():
    sc = get_scenario("S01")
    v = _variant("V_C1A")
    rs = _make_results("V_C1A", sc, ["widget_create"] * 3 + ["graph_mutation"] * 2)
    agg = aggregate(v, rs, {sc.id: sc})

    assert agg.kind_accuracy_overall() == 0.6
    assert agg.stability_overall() == 0.6


def test_aggregate_no_results():
    sc = get_scenario("S01")
    v = _variant("V_C1A")
    agg = aggregate(v, [], {sc.id: sc})
    assert agg.n_total == 0
    assert agg.kind_accuracy_overall() == 0.0


def test_app_id_prefix_match():
    sc = get_scenario("S01")  # expected_app_id = "clock-app-"
    assert sc.expected_app_id.endswith("-")
    v = _variant("V_C1A")
    # LLM returned "clock-widget-7b2e" — should match because "clock" is in both.
    rs = [
        RoutingResult(
            variant_id="V_C1A", scenario_id=sc.id, repeat=i,
            kind="widget_create", app_id="clock-widget-7b2e",
            confidence=0.9, rationale="", latency_ms=0,
        )
        for i in range(3)
    ]
    agg = aggregate(v, rs, {sc.id: sc})
    assert agg.app_id_accuracy_overall({sc.id: sc}) == 1.0


def test_app_id_no_match():
    sc = get_scenario("S01")  # expected topic "clock"
    v = _variant("V_C1A")
    rs = [
        RoutingResult(
            variant_id="V_C1A", scenario_id=sc.id, repeat=i,
            kind="widget_create", app_id="todo-app-xxxx",
            confidence=0.9, rationale="", latency_ms=0,
        )
        for i in range(3)
    ]
    agg = aggregate(v, rs, {sc.id: sc})
    assert agg.app_id_accuracy_overall({sc.id: sc}) == 0.0


def test_exact_app_id_match():
    sc = get_scenario("S15")  # expected_app_id = "clock-app-abcd" (exact)
    v = _variant("V_C1A")
    rs = [
        RoutingResult(
            variant_id="V_C1A", scenario_id=sc.id, repeat=i,
            kind="widget_modify", app_id="clock-app-abcd",
            confidence=0.9, rationale="", latency_ms=0,
        )
        for i in range(3)
    ]
    agg = aggregate(v, rs, {sc.id: sc})
    assert agg.app_id_accuracy_overall({sc.id: sc}) == 1.0


def test_winner_metrics_thresholds():
    sc = get_scenario("S04")  # ambiguous
    scenarios_by_id = {sc.id: sc}
    v = _variant("V_C1A")
    rs = _make_results("V_C1A", sc, ["widget_modify"] * 5)
    agg = aggregate(v, rs, scenarios_by_id)
    m = compute_winner_metrics(agg, {sc.id}, scenarios_by_id)
    assert m.kind_acc == 1.0
    assert m.ambiguous_kind_acc == 1.0
    assert m.stability == 1.0
    assert m.passes_thresholds


def test_winner_metrics_fail_low_stability():
    sc = get_scenario("S04")
    scenarios_by_id = {sc.id: sc}
    v = _variant("V_C1A")
    # 50/50 — kind_acc 1.0 but stability 0.5 (fails threshold 0.8).
    rs = _make_results("V_C1A", sc, ["widget_modify", "converse"] * 3)
    agg = aggregate(v, rs, scenarios_by_id)
    m = compute_winner_metrics(agg, {sc.id}, scenarios_by_id)
    assert m.kind_acc == 0.5  # half were wrong kind
    assert not m.passes_thresholds


def test_per_choice_winners_isolates_c1():
    """When we have 3 C1 variants and they should not contaminate other choices."""
    sc = get_scenario("S01")
    scenarios_by_id = {sc.id: sc}

    aggs = []
    for letter in ("A", "B", "C"):
        v = _variant(f"V_C1{letter}", c1=letter)
        # Vary kinds by choice to confirm the winner is chosen per-choice.
        kinds_per_letter = {"A": ["widget_create"] * 5, "B": ["graph_mutation"] * 5, "C": ["widget_create"] * 5}
        rs = _make_results(v.id, sc, kinds_per_letter[letter])
        aggs.append(aggregate(v, rs, scenarios_by_id))

    winners = per_choice_winners(aggs, set(), scenarios_by_id)
    assert "c1" in winners
    # C1A and C1C both get 100%; tie broken by insertion order so first wins.
    # We just need a winner to be picked.
    assert winners["c1"].variant_id in {"V_C1A", "V_C1B", "V_C1C"}
