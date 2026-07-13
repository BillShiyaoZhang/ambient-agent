"""Unit tests for the markdown report renderer."""


from backend.experiments.report import render_report
from backend.experiments.runner import RoutingResult, aggregate
from backend.experiments.scenarios import get_scenario
from backend.experiments.variants import Variant


def _variant(id_: str) -> Variant:
    return Variant(
        id=id_,
        description="test",
        choices={"c1": "A", "c2": "A", "c3": "A", "c4": "A", "c5": "A", "c6": "C", "c7": "A"},
        system_prompt="",
        fallback_keywords=None,
        agent_system_prompt=None,
        plan_and_act_enabled=True,
    )


def _result(variant_id: str, sc, kind: str, app_id: str | None) -> RoutingResult:
    return RoutingResult(
        variant_id=variant_id,
        scenario_id=sc.id,
        repeat=0,
        kind=kind,
        app_id=app_id,
        confidence=0.9,
        rationale="",
        latency_ms=1000.0,
    )


def test_render_report_smoke():
    sc = get_scenario("S01")
    v = _variant("V_baseline")
    rs = [_result("V_baseline", sc, "widget_create", "clock-app-1234")]
    agg = aggregate(v, rs, {sc.id: sc})
    md = render_report([agg], [sc], metadata={"provider": "minimax", "model": "MiniMax-M3", "n_repeats": 1})
    assert "# Routing Experiment Report" in md
    assert "V_baseline" in md
    assert "Kind Acc" in md
    assert "MiniMax-M3" in md


def test_render_report_includes_ambiguous_drilldown():
    """The report should list ambiguous scenarios with their expected kind."""
    sc = get_scenario("S04")  # ambiguous
    v = _variant("V_baseline")
    rs = [_result("V_baseline", sc, "widget_modify", "clock-app-abcd")]
    agg = aggregate(v, rs, {sc.id: sc})
    md = render_report([agg], [sc])
    assert "S04" in md
    assert "建一个时钟" in md
    assert "widget_modify" in md


def test_render_report_handles_no_ofat_data():
    """If only baseline data is present, per-choice table is empty but doesn't crash."""
    sc = get_scenario("S01")
    v = _variant("V_baseline")
    rs = [_result("V_baseline", sc, "widget_create", "clock-app-1234")]
    agg = aggregate(v, rs, {sc.id: sc})
    md = render_report([agg], [sc])
    assert "_No winner data yet._" in md or "Per-Choice" in md


def test_render_report_sorts_by_score():
    sc1 = get_scenario("S01")
    sc2 = get_scenario("S02")
    v1 = _variant("V_C1A")
    v2 = _variant("V_C2A")

    # v1: all correct
    rs1 = [_result("V_C1A", sc1, "widget_create", "clock-app-xxxx")]
    # v2: all wrong
    rs2 = [_result("V_C2A", sc2, "converse", None)]

    a1 = aggregate(v1, rs1, {sc1.id: sc1})
    a2 = aggregate(v2, rs2, {sc2.id: sc2})
    md = render_report([a1, a2], [sc1, sc2])
    # The summary table should list V_C1A before V_C2A (higher score first).
    pos1 = md.find("V_C1A")
    pos2 = md.find("V_C2A")
    assert pos1 < pos2
